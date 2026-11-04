"""Task generation, WP12.6 (ТЗ Ф4; `Т-§5.4`, `Т-§5.6`, `Т-§5.7`): `task_generator` (Spark; a
`proof` answer or level 6 — a hard call on Wonder, `Т-§11.1`) writes variants of one step;
code checks the form of each markup (`Т-§5.3`), then the truth (`verify_generated`, WP12.5);
what passes goes to the bank as `task.created {source: generated, verification: checked}` with
one `familyId` per call (`Т-§5.4`); a variant of the given source task, and every retest variant,
takes the family of the source (`Т-§5.4`, `Т-§5.7`). A variant that fails is dropped, the others
stay. D-B-168, D-B-186.

Ids come from the call key, so a retry writes the same tasks (law 5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db import Db
from quack.events.values import AnswerSpec, AnswerType, Level
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap
from quack.practice.markup import markup_record, validate_markup
from quack.practice.role_outputs import TaskCondition, TaskGeneratorOutput
from quack.practice.task_check import verify_generated
from quack.practice.task_store import TaskDocument, create_task

GENERATOR_ROLE = "task_generator"
_SELECT_FAMILY = (
    "SELECT payload->>'family_id' AS family_id FROM core.events "
    "WHERE learner_id = %(learner_id)s AND seq = %(seq)s AND type = 'task.created'"
)
NONE_LINE = "(none)"
# Т-§5.5, Т-§11.1 «для сложных — Wonder»: the same rule as the solution space (WP12.4).
HARD_LEVEL = 6

type Purpose = Literal["practice", "retest", "probe", "known_check", "card"]  # "seminar": own text


@dataclass(frozen=True)
class GenerationRequest:
    """What to generate (WP12.6): a skill and a level, optionally a misconception to catch and
    a source task for variants. `key` — deterministic key of the request (law 5)."""

    key: str
    skill_id: UUID
    skill_name: str
    topic_name: str
    level: Level
    kind: AnswerType
    purpose: Purpose = "practice"
    misconceptions: Mapping[UUID, str] | None = None
    course_context: str = ""
    excluded_families: Sequence[str] = ()
    source: TaskDocument | None = None
    domain_id: UUID | None = None
    session_id: UUID | None = None


def generator_variables(request: GenerationRequest, labels: LabelMap) -> dict[str, str]:
    """The nine input blocks of `task_generator@1`, with labels of the call."""
    skill = labels.label("skill", request.skill_id)
    known = [
        f"{label}: {statement}"
        for misconception_id, statement in (request.misconceptions or {}).items()
        if (label := labels.label("misconception", misconception_id))
    ]
    source = request.source
    source_text = NONE_LINE
    if source is not None:
        label = labels.label("task", source.task_id)
        source_text = f"{label}: {source.condition.text}"
        if source.condition.latex:
            source_text += f"\n{source.condition.latex}"
    return {
        "topic_name": request.topic_name,
        "target_skill": f"{skill}: {request.skill_name}",
        "target_level": str(request.level),
        "kind": request.kind,
        "purpose": request.purpose,
        "known_misconceptions": "\n".join(known) or NONE_LINE,
        "course_context": request.course_context or NONE_LINE,
        "excluded_families": "\n".join(request.excluded_families) or NONE_LINE,
        "source_problem": source_text,
    }


def markup_errors(output: TaskGeneratorOutput, labels: LabelMap) -> str | None:
    """Т-§5.3 form rules and known labels; the text goes back to the model once (Т-§4.5)."""
    errors = [
        f"tasks[{i}]: {v.rule} ({v.detail})"
        for i, draft in enumerate(output.tasks)
        for v in validate_markup(
            draft.markup,
            skill_labels=labels.labels("skill"),
            misconception_labels=labels.labels("misconception"),
            task_labels=labels.labels("task"),
        )
    ]
    return "; ".join(errors) if errors else None


def family_id(key: str) -> UUID:
    """One `familyId` per call (`Т-§5.4`), stable across retries."""
    return uuid5(NAMESPACE_URL, f"family:{key}")


def generated_task_id(key: str, index: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"task:{key}:{index}")


async def source_family(db: Db, *, learner_id: UUID, source: TaskDocument) -> UUID:
    """The `familyId` of the source task, from its `task.created`."""
    row = await db.fetch_one(_SELECT_FAMILY, {"learner_id": learner_id, "seq": source.created_seq})
    if row is None:
        raise LookupError(f"task {source.task_id} has no task.created at seq {source.created_seq}")
    return UUID(row["family_id"])


def is_variant_of_source(request: GenerationRequest, variant_of: UUID | None) -> bool:
    """`Т-§5.4`: a task that says it is a variant of the source shares its family; `Т-§5.7`:
    so does every retest variant (it proves retention, never level 4)."""
    source = request.source
    return source is not None and (request.purpose == "retest" or variant_of == source.task_id)


async def generate_tasks(
    db: Db,
    gateway: LLMGateway,
    settings: Settings,
    request: GenerationRequest,
    *,
    learner_id: UUID,
    compute: ComputeClient | None = None,
) -> list[UUID]:
    """Generate, check and store the variants of one request; the ids of the stored tasks."""
    prompt = settings.llm_roles[GENERATOR_ROLE].prompt
    if prompt is None:
        raise ValueError(f"role {GENERATOR_ROLE!r} has no prompt")
    labels = LabelMap.build(
        settings,
        skills=[request.skill_id],
        misconceptions=list(request.misconceptions or {}),
        tasks=[request.source.task_id] if request.source else [],
    )
    result = await gateway.generate_structured(
        GENERATOR_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        TaskGeneratorOutput,
        generator_variables(request, labels),
        learner_id,
        request.session_id,
        None,
        f"{request.key}:{prompt}",
        check=lambda output: markup_errors(output, labels),
        hard=request.kind == "proof" or request.level == HARD_LEVEL,
    )
    names = {request.skill_id: request.skill_name}
    inherited = (
        await source_family(db, learner_id=learner_id, source=request.source)
        if request.source
        else None
    )
    stored: list[UUID] = []
    for index, draft in enumerate(result.value.tasks):
        if markup_errors(TaskGeneratorOutput(tasks=[draft]), labels) is not None:
            continue  # still wrong after the repair: dropped
        markup = markup_record(draft.markup, labels)
        draft_view = _Draft(draft.condition, markup.answer)
        ok = await verify_generated(
            gateway,
            settings,
            draft_view,
            markup,
            learner_id=learner_id,
            key=f"{request.key}:{index}",
            skill_names=names,
            generator_solution=draft.solution,
            source=request.source,
            compute=compute,
        )
        if not ok:
            continue  # the arbiter disagreed too: thrown away (Т-§5.6)
        task_id = generated_task_id(request.key, index)
        await create_task(
            db,
            learner_id=learner_id,
            task_id=task_id,
            family_id=inherited
            if inherited is not None and is_variant_of_source(request, markup.variant_of)
            else family_id(request.key),
            source="generated",
            condition=draft.condition,
            markup=markup,
            verification="checked",
            actor=prompt,
            idempotency_key=f"task:{request.key}:{index}",
            domain_id=request.domain_id,
            session_id=request.session_id,
        )
        stored.append(task_id)
    return stored


@dataclass(frozen=True)
class _Draft:
    """A draft as the checker sees it (`TaskText`)."""

    condition: TaskCondition
    answer_spec: AnswerSpec
