"""Pure decisions of a teacher turn made by code before or after the model call.

- `resolve_degraded_mode` (ТЗ Фазы 3 §4.4, seam S7): the one value of `degraded_mode` in
  `turn.teacher_replied` and SSE `turn_completed`; the first reason in priority order wins.
- `teacher_reasoning_effort` (WP6.5, ТЗ Ф4 WP13.8, Т-§12.9): `high` on the first turn of a
  session and a goal change, building a plan, review of a proof and the second failure in a
  row on the same skill; `teacher_fallback` is always `low`.
- `MEMORY_UNAVAILABLE_MARKER` (WP6.1, Т-§20.7): the line of the memory slot while Neo4j is
  down; part of the prompt contract of `teacher@1` (§7.6).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from quack.events.payloads import TaskEvaluated

type DegradedMode = Literal[
    "budget_exceeded", "llm_unavailable", "memory_unavailable", "teacher_fallback", "projector_lag"
]

# Priority of §4.4, strongest change of the learner experience first.
DEGRADED_MODE_PRIORITY: tuple[DegradedMode, ...] = (
    "budget_exceeded",
    "llm_unavailable",
    "memory_unavailable",
    "teacher_fallback",
    "projector_lag",
)

MEMORY_UNAVAILABLE_MARKER = "memory temporarily unavailable"


def resolve_degraded_mode(
    *,
    budget_exceeded: bool,
    llm_unavailable: bool,
    memory_unavailable: bool,
    teacher_fallback: bool,
    projector_lag: bool,
) -> DegradedMode | None:
    """First true reason in `DEGRADED_MODE_PRIORITY`, `None` for a regular turn (`T-H3`)."""
    flags: dict[DegradedMode, bool] = {
        "budget_exceeded": budget_exceeded,
        "llm_unavailable": llm_unavailable,
        "memory_unavailable": memory_unavailable,
        "teacher_fallback": teacher_fallback,
        "projector_lag": projector_lag,
    }
    return next((mode for mode in DEGRADED_MODE_PRIORITY if flags[mode]), None)


def teacher_reasoning_effort(
    *,
    role: Literal["teacher", "teacher_fallback"],
    is_first_turn: bool,
    session_evaluations: Sequence[TaskEvaluated],
    goal_changed: bool = False,
    builds_plan: bool = False,
    proof_answer: bool = False,
) -> Literal["low", "high"]:
    """Effort of the teacher call (`T-H9`, `T-TT8`); `session_evaluations` in `seq` order.

    `high` by the table of Т-§12.9: the first turn of the session or a goal change, a turn
    that builds a plan, the review of a `proof` answer, and the two latest `task.evaluated`
    of the session both failed on one skill; `teacher_fallback` is always `low`.
    """
    if role == "teacher_fallback":
        return "low"
    if is_first_turn or goal_changed or builds_plan or proof_answer:
        return "high"
    if len(session_evaluations) < 2:
        return "low"
    previous, last = session_evaluations[-2], session_evaluations[-1]
    if previous.is_correct or last.is_correct:
        return "low"
    shared = {s.skill_id for s in previous.skill_scores} & {s.skill_id for s in last.skill_scores}
    return "high" if shared else "low"
