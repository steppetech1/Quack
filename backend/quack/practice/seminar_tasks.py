"""Seminar tasks, WP12.5 / WP20.2 (ТЗ Ф4; `Т-§5.6`; #271): a problem found in an uploaded
material becomes a task with markup. D-B-218.

`material_parser` (C) hands over the statement, the answer of the material if it has one, and
the skill the page teaches. Here `task_generator` marks the problem (`purpose` = «seminar»: the
statement is kept as it is, the answer of the material is kept as the key), code checks the
form (`Т-§5.3`), and the table of `Т-§5.6` decides the verification:

- with an answer from the material — `disputed` when the checker and the arbiter disagree with
  it (verdicts on the task fall to class `model`), `checked` when they agree; the task is kept
  either way;
- without an answer — Wonder writes the key (a hard call) and the task goes as a generated one:
  checked, or thrown away.

Ids come from the key of the task, so a retry writes nothing new (law 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db import Db
from quack.events.values import AnswerType, Level
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap
from quack.practice.markup import markup_record
from quack.practice.role_outputs import TaskCondition, TaskGeneratorOutput
from quack.practice.task_check import verify_generated
from quack.practice.task_generation import (
    GENERATOR_ROLE,
    NONE_LINE,
    GenerationRequest,
    _Draft,
    family_id,
    generator_variables,
    markup_errors,
)
from quack.practice.task_store import Verification, create_task

SEMINAR_PURPOSE = (
    "seminar: mark the source problem below, do not change its statement; when an answer from "
    "the material is given, it is the key of the problem"
)


@dataclass(frozen=True)
class SeminarTask:
    """A problem of a material. `key` — deterministic (`seminar:{material_id}:{n}`)."""

    key: str
    statement: str
    skill_id: UUID
    skill_name: str
    topic_name: str
    latex: str | None = None
    answer: str | None = None
    level: Level = 3
    kind: AnswerType = "expression"
    course_context: str = ""
    domain_id: UUID | None = None


def seminar_task_id(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"task:seminar:{key}")


def _source_block(task: SeminarTask) -> str:
    lines = [f"Statement: {task.statement}"]
    if task.latex:
        lines.append(f"Formula: {task.latex}")
    lines.append(f"Answer from the material: {task.answer or NONE_LINE}")
    return "\n".join(lines)


async def create_seminar_task(
    db: Db,
    gateway: LLMGateway,
    settings: Settings,
    task: SeminarTask,
    *,
    learner_id: UUID,
    compute: ComputeClient | None = None,
) -> UUID | None:
    """Mark, check and store one seminar problem; its id, or `None` when it cannot be marked
    or (no answer in the material) the check throws it away."""
    prompt = settings.llm_roles[GENERATOR_ROLE].prompt
    if prompt is None:
        raise ValueError(f"role {GENERATOR_ROLE!r} has no prompt")
    labels = LabelMap.build(settings, skills=[task.skill_id])
    request = GenerationRequest(
        key=task.key,
        skill_id=task.skill_id,
        skill_name=task.skill_name,
        topic_name=task.topic_name,
        level=task.level,
        kind=task.kind,
        course_context=task.course_context,
    )
    variables = generator_variables(request, labels)
    variables["purpose"] = SEMINAR_PURPOSE
    variables["source_problem"] = _source_block(task)
    result = await gateway.generate_structured(
        GENERATOR_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        TaskGeneratorOutput,
        variables,
        learner_id,
        None,
        None,
        f"{task.key}:{prompt}",
        check=lambda output: markup_errors(output, labels),
        hard=task.answer is None or task.kind == "proof",
    )
    if not result.value.tasks:
        return None
    draft = result.value.tasks[0]
    if markup_errors(TaskGeneratorOutput(tasks=[draft]), labels) is not None:
        return None
    markup = markup_record(draft.markup, labels)
    condition = TaskCondition(text=task.statement, latex=task.latex or draft.condition.latex)
    agreed = await verify_generated(
        gateway,
        settings,
        _Draft(condition, markup.answer),
        markup,
        learner_id=learner_id,
        key=task.key,
        skill_names={task.skill_id: task.skill_name},
        generator_solution=draft.solution,
        source=None,
        compute=compute,
    )
    verification: Verification = "checked"
    if not agreed:
        if task.answer is None:
            return None  # as a generated task: thrown away (Т-§5.6)
        verification = "disputed"
    task_id = seminar_task_id(task.key)
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=family_id(task.key),
        source="seminar",
        condition=condition,
        markup=markup,
        verification=verification,
        actor=prompt,
        idempotency_key=f"task:seminar:{task.key}",
        domain_id=task.domain_id,
    )
    return task_id
