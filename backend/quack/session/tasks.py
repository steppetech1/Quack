"""Task-bank pick and submission evaluation helpers (ТЗ §2.3 steps 4-5); reads `core.task_bank`."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from quack.compute_client.client import (
    ComputeClient,
    ComputeUnavailableError,
    MathCheckRequest,
)
from quack.events.payloads import SkillEvalScore, TaskEvaluated, TaskSubmitted
from quack.graph.overlay import SessionOverlay

# Evaluable without an LLM (ТЗ §2.3.4, Т-§17.1).
COMPUTE_KINDS: tuple[str, ...] = ("math", "choice")
MIN_INDEPENDENCE = 0.1

_TASK_COLUMNS = (
    "id, topic_id, family_id, difficulty, kind, prompt_text, reference_solution, target_skills"
)

_TB_COLUMNS = ", ".join(f"tb.{c.strip()}" for c in _TASK_COLUMNS.split(","))


@dataclass(frozen=True)
class TaskBankRow:
    id: UUID
    topic_id: UUID
    family_id: str
    difficulty: float
    kind: str
    prompt_text: str
    reference_solution: dict[str, Any]
    target_skills: list[dict[str, Any]]


@dataclass(frozen=True)
class SubmissionOutcome:
    """`evaluated is None` means "not evaluated here" (open task, parse_error, compute outage)."""

    submitted: TaskSubmitted
    evaluated: TaskEvaluated | None


def _row(row: Any) -> TaskBankRow:
    return TaskBankRow(
        id=row.id,
        topic_id=row.topic_id,
        family_id=str(row.family_id),
        difficulty=float(row.difficulty),
        kind=str(row.kind),
        prompt_text=str(row.prompt_text),
        reference_solution=dict(row.reference_solution or {}),
        target_skills=list(row.target_skills or []),
    )


async def pick_task(
    session: AsyncSession,
    learner_id: UUID,
    topic_id: UUID,
    exclude_task_ids: Collection[UUID],
    solved_family_ids: Collection[str] = (),
    kinds: Collection[str] | None = None,
    *,
    session_id: UUID | None = None,
) -> TaskBankRow | None:
    """Unassigned task of the topic, preferring families without a successful solve.

    `exclude_task_ids` is `overlay.assigned_task_ids`; with `session_id` the tasks already
    assigned in that session (projected or not) are excluded too. A family counts as solved
    if it is in `solved_family_ids` or the log has a correct `task.evaluated` of the family.
    """
    sql = (
        f"SELECT {_TB_COLUMNS}, (tb.family_id = ANY(:solved) OR EXISTS ("
        "  SELECT 1 FROM core.events e JOIN core.task_bank t2 "
        "    ON t2.learner_id = e.learner_id AND t2.id = (e.payload->>'task_id')::uuid "
        "  WHERE e.learner_id = :l AND e.type = 'task.evaluated' "
        "    AND (e.payload->>'is_correct')::boolean AND t2.family_id = tb.family_id"
        ")) AS solved FROM core.task_bank tb "
        "WHERE tb.learner_id = :l AND tb.topic_id = :t AND NOT (tb.id = ANY(:exclude))"
    )
    params: dict[str, Any] = {
        "l": learner_id,
        "t": topic_id,
        "solved": sorted(set(solved_family_ids)),
        "exclude": sorted(set(exclude_task_ids), key=str),
    }
    if kinds is not None:
        sql += " AND tb.kind = ANY(:kinds)"
        params["kinds"] = sorted(set(kinds))
    if session_id is not None:
        sql += (
            " AND NOT EXISTS (SELECT 1 FROM core.events a WHERE a.learner_id = :l "
            "AND a.session_id = :s AND a.type = 'task.assigned' "
            "AND (a.payload->>'task_id')::uuid = tb.id)"
        )
        params["s"] = session_id
    sql += " ORDER BY solved, tb.difficulty, tb.created_seq, tb.id LIMIT 1"
    found = (await session.execute(text(sql), params)).first()
    return _row(found) if found is not None else None


async def load_task(session: AsyncSession, learner_id: UUID, task_id: UUID) -> TaskBankRow | None:
    result = await session.execute(
        text(f"SELECT {_TASK_COLUMNS} FROM core.task_bank WHERE learner_id = :l AND id = :id"),
        {"l": learner_id, "id": task_id},
    )
    row = result.first()
    return _row(row) if row is not None else None


def hint_penalties(overlay: SessionOverlay, task_id: UUID) -> list[float]:
    """`independence_penalty` of every hint in `overlay.hints[task_id]`."""
    seqs = {h.seq for h in overlay.hints.get(task_id, [])}
    return [
        float(e.payload.get("independence_penalty", 0.0))
        for e in overlay.events
        if e.type == "task.hint_given" and e.seq in seqs
    ]


def compute_independence(penalties: Iterable[float]) -> float:
    """`max(0.1, 1.0 - sum(independence_penalty))`."""
    return max(MIN_INDEPENDENCE, min(1.0, round(1.0 - sum(penalties), 4)))


def _skill_scores(task: TaskBankRow, score: float) -> list[SkillEvalScore]:
    tested_depth = max(0.0, min(1.0, task.difficulty))
    scores: list[SkillEvalScore] = []
    for target in task.target_skills:
        skill_id = (target.get("skill") or {}).get("skill_id")
        if skill_id is None:
            continue
        scores.append(
            SkillEvalScore(skill_id=UUID(str(skill_id)), score=score, tested_depth=tested_depth)
        )
    return scores


def _normalize(value: str) -> str:
    return "".join(value.split()).casefold()


async def _check(compute: ComputeClient, task: TaskBankRow, answer: str) -> tuple[bool, str] | None:
    ref = task.reference_solution
    if task.kind == "choice":
        option = ref.get("option")
        if option is None:
            return None
        ok = _normalize(answer) == _normalize(str(option))
        return ok, "correct" if ok else f"the correct option is {option}"
    if task.kind == "math":
        reference = ref.get("sympy") or ref.get("answer")
        if reference is None:
            return None
        request = MathCheckRequest.model_validate(
            {
                "mode": ref.get("mode", "expression_equiv"),
                "student_expr": answer[:1000],
                "reference_expr": str(reference)[:1000],
                "variables": ref.get("variables", ["x"]),
            }
        )
        try:
            result = await compute.check_math(request)
        except ComputeUnavailableError:
            return None
        if result.status != "completed" or result.is_equivalent is None:
            return None
        ok = bool(result.is_equivalent)
        return ok, "correct" if ok else (result.diff_hint or "the answer is not equivalent")
    return None


async def evaluate_submission(
    compute: ComputeClient,
    task: TaskBankRow,
    answer: str,
    session_id: UUID,
    turn_id: UUID,
    hints_used: int,
    penalties: Iterable[float],
) -> SubmissionOutcome:
    """Compute check for math/choice; `evaluated=None` for open tasks and on
    parse_error/compute outage. `answer` is the turn text, `penalties` come from
    `hint_penalties(overlay, task.id)`."""
    submitted = TaskSubmitted(
        session_id=session_id,
        turn_id=turn_id,
        task_id=task.id,
        answer=answer,
        hints_used=max(0, hints_used),
    )
    verdict = await _check(compute, task, answer)
    if verdict is None:
        return SubmissionOutcome(submitted, None)
    is_correct, feedback = verdict
    score = 1.0 if is_correct else 0.0
    evaluated = TaskEvaluated(
        session_id=session_id,
        turn_id=turn_id,
        task_id=task.id,
        evaluator="compute",
        is_correct=is_correct,
        score=score,
        independence=compute_independence(penalties),
        feedback=feedback,
        skill_scores=_skill_scores(task, score),
    )
    return SubmissionOutcome(submitted, evaluated)
