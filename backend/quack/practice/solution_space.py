"""The solution space of a task, WP12.4 (ТЗ Ф4; `Т-§5.5`): 3–8 key steps, 2–4 wrong branches,
a ladder of 3 hints, the answer by the task's spec. Built by the role `solution_space` — Spark;
a `proof` answer or a level-6 task is a hard call, on the role's `hard_model` (Wonder,
Т-§11.1). Stored in `content.tasks.solution_space` with the prompt version; the misconception
of a wrong branch is stored as its id, not as the label of the call (labels live one call,
Т-§4.5). D-B-166.

Bank and seminar tasks get it ahead (`task.solution_space`, rule of `task.created`); tasks on the
fly lazily (`agent.solution_space` from the canvas). A task that already has a space is not
rebuilt: the job is idempotent by the task.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from psycopg.types.json import Jsonb

from quack.config import Settings
from quack.db import Db
from quack.events.event_types import TaskCreated
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap
from quack.practice.role_outputs import SolutionSpace, WrongBranch
from quack.practice.task_store import TaskDocument, get_task

SPACE_ROLE = "solution_space"
NONE_LINE = "(none)"
# Т-§5.5 «для сложных — Wonder»: the hardest level of the markup (ТЗ Ф4 WP12.4).
DEEP_LEVEL = 6

_SELECT_MARKUP = (
    "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s AND seq = %(seq)s "
    "AND type = 'task.created'"
)
_UPDATE_SPACE = (
    "UPDATE content.tasks SET solution_space = %(space)s, solution_space_version = %(version)s "
    "WHERE learner_id = %(learner_id)s AND task_id = %(task_id)s AND solution_space IS NULL"
)


@dataclass(frozen=True)
class SkillMisconception:
    """A known misconception of a skill the task tests (`quack.graph.read`)."""

    misconception_id: UUID
    statement: str


def is_hard(doc: TaskDocument, created: TaskCreated) -> bool:
    """WP12.4: a `proof` answer or a task testing level 6 — Wonder."""
    levels = {t.level for t in created.markup.tests}
    return doc.answer_spec.type == "proof" or DEEP_LEVEL in levels


def space_variables(
    doc: TaskDocument,
    misconceptions: Sequence[SkillMisconception],
    labels: LabelMap,
    reference_solution: str | None = None,
) -> dict[str, str]:
    """The four input blocks of `solution_space@1`."""
    condition = doc.condition.text
    if doc.condition.latex:
        condition += f"\n{doc.condition.latex}"
    known = [
        f"{label}: {m.statement}"
        for m in misconceptions
        if (label := labels.label("misconception", m.misconception_id))
    ]
    return {
        "problem": condition,
        "answer_spec": doc.answer_spec.model_dump_json(exclude_none=True),
        "reference_solution": reference_solution or NONE_LINE,
        "known_misconceptions": "\n".join(known) or NONE_LINE,
    }


def space_errors(output: SolutionSpace, labels: LabelMap) -> str | None:
    """Т-§4.5: a misconception label the call did not give goes back to the model once."""
    known = labels.labels("misconception")
    unknown = [
        b.misconception
        for b in output.wrong_branches
        if b.misconception is not None and b.misconception not in known
    ]
    if not unknown:
        return None
    return "unknown labels: " + ", ".join(dict.fromkeys(unknown)) + "; use only the given labels"


def stored_space(output: SolutionSpace, labels: LabelMap) -> SolutionSpace:
    """Labels → ids in the wrong branches; an unknown label left after the repair → null."""
    known = labels.labels("misconception")
    branches = [
        WrongBranch(
            sign=b.sign,
            gap_type=b.gap_type,
            misconception=str(known[b.misconception]) if b.misconception in known else None,
        )
        for b in output.wrong_branches
    ]
    return output.model_copy(update={"wrong_branches": branches})


async def build_solution_space(
    db: Db,
    gateway: LLMGateway,
    settings: Settings,
    *,
    learner_id: UUID,
    task_id: UUID,
    misconceptions: Sequence[SkillMisconception] = (),
    reference_solution: str | None = None,
) -> SolutionSpace | None:
    """Build and store the space of a learner's task; the stored one if it exists; `None` for
    a task that is not the learner's."""
    doc = await get_task(db, learner_id=learner_id, task_id=task_id)
    if doc is None:
        return None
    if doc.solution_space is not None:
        return doc.solution_space
    row = await db.fetch_one(_SELECT_MARKUP, {"learner_id": learner_id, "seq": doc.created_seq})
    if row is None:
        raise LookupError(f"task {task_id} has no task.created at seq {doc.created_seq}")
    created = TaskCreated.model_validate(row["payload"])
    prompt = settings.llm_roles[SPACE_ROLE].prompt
    if prompt is None:
        raise ValueError(f"role {SPACE_ROLE!r} has no prompt")
    version = prompt.rsplit("@", 1)[1]
    labels = LabelMap.build(settings, misconceptions=[m.misconception_id for m in misconceptions])
    result = await gateway.generate_structured(
        SPACE_ROLE,
        int(version),
        SolutionSpace,
        space_variables(doc, misconceptions, labels, reference_solution),
        learner_id,
        None,
        None,
        f"solution_space:{task_id}:{prompt}",
        check=lambda output: space_errors(output, labels),
        hard=is_hard(doc, created),
    )
    space = stored_space(result.value, labels)
    await db.execute(
        _UPDATE_SPACE,
        {
            "space": Jsonb(space.model_dump(mode="json")),
            "version": prompt,
            "learner_id": learner_id,
            "task_id": task_id,
        },
    )
    stored = await get_task(db, learner_id=learner_id, task_id=task_id)
    return stored.solution_space if stored is not None else space
