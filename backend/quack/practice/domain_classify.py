"""Domain classification, WP16.4 (ТЗ Ф4; Т-§4.9, Т-§22.9; `T-AG5`): the body of
`POST /v1/domains/classify`. D-B-173.

- The API calls `domain_classifier` (Spark, P1) through the gateway directly, without the queue.
- Input: the learner's domains with labels `D…` (from `domain.created`, law 1) and the first
  message, or the first page with text of an uploaded material.
- Output: `existing` (a domain of the learner), `new` (a name), `unclear`. A suggestion only at
  `confidence >= domain_autolink_confidence`; below it — `unclear`, no domain is suggested.
- The learner confirms with one tap before the first turn; a new domain is `domain.created` of
  `POST /v1/domains` (stream A). Nothing is written here.

The body lives in `practice`: the route is declared in `quack/domains/router.py` (ТЗ Ф4 §7.3
п.3), and a product package imports only `practice` among the product ones (§4.1).
"""

from __future__ import annotations

import hashlib
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quack.api.errors import ApiError
from quack.config import Settings
from quack.db.db import Db
from quack.llm.errors import LLMBudgetExceededError, LLMSchemaError, LLMUnavailableError
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap
from quack.practice.role_outputs import DomainClassifierOutput

CLASSIFIER_ROLE = "domain_classifier"
NO_DOMAINS = "(none)"
_LLM_ERRORS = (LLMUnavailableError, LLMBudgetExceededError, LLMSchemaError)

_DOMAINS = (
    "SELECT payload->>'domain_id' AS domain_id, payload->>'name' AS name FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = 'domain.created' ORDER BY seq"
)
_MATERIAL = (
    "SELECT 1 AS found FROM content.materials "
    "WHERE learner_id = %(learner_id)s AND material_id = %(material_id)s"
)
_FIRST_PAGE = (
    "SELECT text FROM content.material_pages WHERE learner_id = %(learner_id)s "
    "AND material_id = %(material_id)s AND btrim(text) <> '' ORDER BY page LIMIT 1"
)


class ClassifyDomainRequest(BaseModel):
    """`{text | material_id}`: exactly one of them."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1)
    material_id: UUID | None = None

    @model_validator(mode="after")
    def _one_source(self) -> ClassifyDomainRequest:
        if (self.text is None) == (self.material_id is None):
            raise ValueError("give exactly one of text and material_id")
        return self


class DomainSuggestion(BaseModel):
    """`existing` — `domain_id` of the learner's domain; `new` — `name` of a new domain;
    `unclear` — nothing is suggested."""

    decision: Literal["existing", "new", "unclear"]
    domain_id: UUID | None = None
    name: str | None = None
    confidence: float | None = None


UNCLEAR = DomainSuggestion(decision="unclear")


def classifier_errors(output: DomainClassifierOutput, labels: LabelMap) -> str | None:
    """Т-§4.5: the decision and its field must agree, the label must be given — else once back."""
    if output.decision == "existing" and output.domain not in labels.labels("domain"):
        return "`existing` needs the label of one of the given domains in `domain`"
    if output.decision == "new" and not (output.new_name or "").strip():
        return "`new` needs `new_name`"
    return None


def suggestion(
    output: DomainClassifierOutput, labels: LabelMap, settings: Settings
) -> DomainSuggestion:
    """A suggestion only when the classifier is sure (`domain_autolink_confidence`)."""
    if output.decision == "unclear" or output.confidence < settings.domain_autolink_confidence:
        return UNCLEAR
    if output.decision == "existing":
        assert output.domain is not None  # checked by `classifier_errors`
        domain_id = labels.resolve("domain", output.domain)
        return DomainSuggestion(
            decision="existing", domain_id=domain_id, confidence=output.confidence
        )
    return DomainSuggestion(
        decision="new", name=(output.new_name or "").strip(), confidence=output.confidence
    )


async def _source_text(db: Db, learner_id: UUID, request: ClassifyDomainRequest) -> str | None:
    if request.text is not None:
        return request.text
    params = {"learner_id": learner_id, "material_id": request.material_id}
    if await db.fetch_one(_MATERIAL, params) is None:
        raise ApiError("NOT_FOUND", "material not found", status_code=404)
    page = await db.fetch_one(_FIRST_PAGE, params)
    return None if page is None else str(page["text"])


async def classify_domain(
    db: Db,
    gateway: LLMGateway,
    settings: Settings,
    *,
    learner_id: UUID,
    request: ClassifyDomainRequest,
) -> DomainSuggestion:
    """`POST /v1/domains/classify`. A material without text yet, or a failed call, is
    `unclear`: the classifier only suggests, the learner picks the domain themselves."""
    text = await _source_text(db, learner_id, request)
    if text is None:
        return UNCLEAR
    rows = await db.fetch_all(_DOMAINS, {"learner_id": learner_id})
    names = {UUID(str(r["domain_id"])): str(r["name"]) for r in rows}
    labels = LabelMap.build(settings, domains=list(names))
    domain_lines = [
        f"{label}: {name}"
        for domain_id, name in names.items()
        if (label := labels.label("domain", domain_id))
    ]
    prompt = settings.llm_roles[CLASSIFIER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'domain_classifier' has no prompt")
    digest = hashlib.sha256(text.encode()).hexdigest()
    known = hashlib.sha256(",".join(sorted(map(str, names))).encode()).hexdigest()[:16]
    try:
        result = await gateway.generate_structured(
            CLASSIFIER_ROLE,
            int(prompt.rsplit("@", 1)[1]),
            DomainClassifierOutput,
            {"domains": "\n".join(domain_lines) or NO_DOMAINS, "text": text},
            learner_id,
            None,
            None,
            f"domain_classify:{learner_id}:{digest}:{known}",
            check=lambda output: classifier_errors(output, labels),
        )
    except _LLM_ERRORS:
        return UNCLEAR
    return suggestion(result.value, labels, settings)
