"""Domain classification, WP16.4 (ТЗ Ф4; Т-§4.9, Т-§22.9; `T-AG5`): a direct call of
`domain_classifier` without the queue; `existing`/`new`/`unclear`; the threshold
`domain_autolink_confidence`. D-B-173."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ValidationError

from quack.api.errors import ApiError
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import DomainCreated
from quack.events.log import NewEvent, append_events
from quack.llm.errors import LLMSchemaError, LLMUnavailableError
from quack.llm.gateway import LLMGateway, LLMResult
from quack.practice.domain_classify import (
    UNCLEAR,
    ClassifyDomainRequest,
    DomainSuggestion,
    classify_domain,
)
from quack.practice.role_outputs import DomainClassifierOutput
from tests.db.fixtures import create_learner

SETTINGS = Settings()


def _out(decision: str, *, domain: str | None = None, name: str | None = None, conf: float = 0.9):
    return DomainClassifierOutput(
        decision=cast(Any, decision), domain=domain, new_name=name, confidence=conf
    )


@dataclass
class _Gateway:
    """Answers in order; a failed `check` takes the next answer once (the repair path, Т-§4.5)."""

    answers: list[DomainClassifierOutput | Exception]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_structured(
        self,
        role: str,
        prompt_version: int,
        response_model: type[BaseModel],
        variables: dict[str, Any],
        learner_id: UUID,
        session_id: UUID | None,
        turn_id: UUID | None,
        idempotency_key: str,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> LLMResult[Any]:
        self.calls.append({"role": role, "variables": variables, "key": idempotency_key})
        check: Callable[[Any], str | None] = kwargs["check"]
        for attempt in range(2):
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            if check(answer) is None:
                return LLMResult(
                    value=answer, from_cache=False, prompt_hash="h", model="m", usage=None
                )
            if attempt == 1:
                break
        raise LLMSchemaError("repair failed")


async def _domains(db: Db, learner_id: UUID, names: list[str]) -> list[UUID]:
    ids = [uuid4() for _ in names]
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="domain.created",
                payload=DomainCreated(domain_id=d, name=n),
                actor="learner",
                idempotency_key=f"domain:{d}",
            )
            for d, n in zip(ids, names, strict=True)
        ],
    )
    return ids


async def _classify(
    db: Db, gateway: _Gateway, learner_id: UUID, **request: Any
) -> DomainSuggestion:
    return await classify_domain(
        db,
        cast(LLMGateway, gateway),
        SETTINGS,
        learner_id=learner_id,
        request=ClassifyDomainRequest(**request),
    )


async def test_domain_classifier_direct_p1(pg_db: Db) -> None:
    """T-AG5: the call goes straight to the gateway with the learner's domains by label;
    `existing` gives the domain id, `new` the name, `unclear` and an unsure answer nothing;
    nothing is written (no job, no event)."""
    learner_id = await create_learner(pg_db)
    calculus, algebra = await _domains(pg_db, learner_id, ["Calculus", "Linear algebra"])
    other = await create_learner(pg_db)
    await _domains(pg_db, other, ["Someone else's domain"])
    threshold = SETTINGS.domain_autolink_confidence

    gateway = _Gateway([_out("existing", domain="D2", conf=threshold)])
    found = await _classify(pg_db, gateway, learner_id, text="eigenvalues of a 3x3 matrix")
    assert found == DomainSuggestion(decision="existing", domain_id=algebra, confidence=threshold)
    (call,) = gateway.calls
    assert call["role"] == "domain_classifier"
    assert call["variables"] == {
        "domains": "D1: Calculus\nD2: Linear algebra",
        "text": "eigenvalues of a 3x3 matrix",
    }
    assert str(calculus) not in str(call["variables"])

    new = await _classify(
        pg_db, _Gateway([_out("new", name=" Organic chemistry ")]), learner_id, text="alkanes"
    )
    assert new == DomainSuggestion(decision="new", name="Organic chemistry", confidence=0.9)
    unsure = _Gateway([_out("existing", domain="D1", conf=threshold - 0.01)])
    assert await _classify(pg_db, unsure, learner_id, text="limits") == UNCLEAR
    assert await _classify(pg_db, _Gateway([_out("unclear")]), learner_id, text="hi") == UNCLEAR

    # Т-§4.5: a label not given and `new` without a name go back once
    repaired = _Gateway([_out("existing", domain="D7"), _out("existing", domain="D1")])
    found = await _classify(pg_db, repaired, learner_id, text="series")
    assert found.domain_id == calculus
    broken = _Gateway([_out("new"), _out("new", name="")])
    assert await _classify(pg_db, broken, learner_id, text="x") == UNCLEAR

    rows = await pg_db.fetch_all(
        "SELECT type FROM core.events WHERE learner_id = %(l)s", {"l": learner_id}
    )
    assert [r["type"] for r in rows] == ["domain.created", "domain.created"]


async def test_classifier_without_domains_and_when_down(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    gateway = _Gateway([_out("new", name="Calculus")])
    assert (await _classify(pg_db, gateway, learner_id, text="limits")).name == "Calculus"
    assert gateway.calls[0]["variables"]["domains"] == "(none)"
    down = _Gateway([LLMUnavailableError("down")])
    assert await _classify(pg_db, down, learner_id, text="limits") == UNCLEAR


async def test_classifier_reads_the_first_page_of_a_material(pg_db: Db) -> None:
    """The beginning of a file is its first page with text; a material of another learner
    is 404; a material not parsed yet is `unclear` without a call."""
    learner_id = await create_learner(pg_db)
    material = uuid4()
    await pg_db.execute(
        "INSERT INTO content.materials (material_id, learner_id, object_key, mime, status) "
        "VALUES (%(m)s, %(l)s, 'k', 'application/pdf', 'uploaded')",
        {"m": material, "l": learner_id},
    )
    empty = _Gateway([])
    assert await _classify(pg_db, empty, learner_id, material_id=material) == UNCLEAR
    assert empty.calls == []
    await pg_db.executemany(
        "INSERT INTO content.material_pages (material_id, page, learner_id, text) "
        "VALUES (%(m)s, %(p)s, %(l)s, %(t)s)",
        [
            {"m": material, "p": 1, "l": learner_id, "t": "  "},
            {"m": material, "p": 2, "l": learner_id, "t": "Chapter 1. Limits"},
            {"m": material, "p": 3, "l": learner_id, "t": "Chapter 2. Series"},
        ],
    )
    gateway = _Gateway([_out("new", name="Calculus")])
    await _classify(pg_db, gateway, learner_id, material_id=material)
    assert gateway.calls[0]["variables"]["text"] == "Chapter 1. Limits"

    stranger = await create_learner(pg_db)
    with pytest.raises(ApiError) as missing:
        await _classify(pg_db, _Gateway([]), stranger, material_id=material)
    assert missing.value.status_code == 404


def test_request_takes_exactly_one_source() -> None:
    with pytest.raises(ValidationError):
        ClassifyDomainRequest()
    with pytest.raises(ValidationError):
        ClassifyDomainRequest(text="a", material_id=uuid4())
    with pytest.raises(ValidationError):
        ClassifyDomainRequest(text="")
