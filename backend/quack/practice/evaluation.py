"""One answer check for a turn with `related_task_id` and for `POST .../tasks/{id}/submit`
(WP6.2, ТЗ-П3 §2.2 п.3): choice → compute → LLM `evaluator@1` → deterministic fallback,
plus `independence` by the hints given. Same input → same `task.evaluated` fields
(`evaluator`, `is_correct`, `score`, `independence`, `skill_scores`,
`triggered_misconceptions`), checked by `T-H2`.

Moved from `quack/artifacts/practice.py` without changing its behaviour (ТЗ-П3 decisions
1, 4, 8, A1); the turn of stream 2 now calls the same code (D-B-006).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from quack.compute_client.client import (
    ComputeClient,
    ComputeUnavailableError,
    MathCheckRequest,
    MathCheckResponse,
)
from quack.config import Settings
from quack.llm.errors import LLMBudgetExceededError, LLMSchemaError, LLMUnavailableError
from quack.llm.gateway import LLMGateway
from quack.practice.schemas import EvaluatorOutput

# Errors after which practice switches to its deterministic fallbacks (Т-§22.7, decision 4).
LLM_ERRORS = (LLMUnavailableError, LLMBudgetExceededError, LLMSchemaError)

# Feedback of verdicts given without the LLM (compute, choice, decision 4).
FEEDBACK_CORRECT = "Correct: your answer matches the reference answer."
FEEDBACK_INCORRECT = "Not quite: your answer does not match the reference answer."

EVALUATOR_ACTOR = "evaluator@1"
CODE_ACTOR = "code"

TASK_COLUMNS = (
    "id, topic_id, family_id, difficulty, kind, prompt_text, reference_solution, "
    "target_skills, target_misconceptions"
)
_TASK_COLUMNS = TASK_COLUMNS


@dataclass(frozen=True)
class TaskToCheck:
    """A `core.task_bank` row: input of the cascade and result of the pick."""

    task_id: UUID
    kind: str
    prompt_text: str
    reference_solution: dict[str, Any]
    target_skills: list[dict[str, Any]]
    target_misconceptions: list[str]
    difficulty: float
    topic_id: UUID
    family_id: str

    @property
    def skill_ids(self) -> list[str]:
        return [t["skill"]["skill_id"] for t in self.target_skills if t["skill"].get("skill_id")]


@dataclass(frozen=True)
class AnswerVerdict:
    """Fields of `task.evaluated` decided by the cascade, plus the event actor."""

    evaluator: Literal["compute", "llm", "hybrid"]
    is_correct: bool
    score: float
    independence: float
    feedback: str
    skill_scores: list[dict[str, Any]]
    triggered_misconceptions: list[dict[str, Any]]
    actor: str


def task_from_row(row: Any) -> TaskToCheck:
    """`TaskToCheck` from a `core.task_bank` row (mapping or row with attributes)."""
    get = row.__getitem__ if isinstance(row, dict) else lambda name: getattr(row, name)
    return TaskToCheck(
        task_id=get("id"),
        kind=str(get("kind")),
        prompt_text=str(get("prompt_text")),
        reference_solution=dict(get("reference_solution") or {}),
        target_skills=list(get("target_skills") or []),
        target_misconceptions=list(get("target_misconceptions") or []),
        difficulty=float(get("difficulty")),
        topic_id=get("topic_id"),
        family_id=str(get("family_id")),
    )


async def load_task(session: AsyncSession, learner_id: UUID, task_id: UUID) -> TaskToCheck | None:
    """The bank task of the learner, or `None`."""
    row = (
        await session.execute(
            text(f"SELECT {_TASK_COLUMNS} FROM core.task_bank WHERE learner_id = :l AND id = :t"),
            {"l": learner_id, "t": task_id},
        )
    ).first()
    return None if row is None else task_from_row(row)


async def hint_penalties(
    session: AsyncSession, learner_id: UUID, session_id: UUID, task_id: UUID
) -> list[tuple[int, float]]:
    """`(hint_level, independence_penalty)` of every `task.hint_given` for the task in the
    session, from the log (projected or not), in `seq` order."""
    rows = (
        await session.execute(
            text(
                "SELECT payload FROM core.events WHERE learner_id = :l AND session_id = :s "
                "AND type = 'task.hint_given' AND payload->>'task_id' = :t ORDER BY seq"
            ),
            {"l": learner_id, "s": session_id, "t": str(task_id)},
        )
    ).all()
    return [(int(r.payload["hint_level"]), float(r.payload["independence_penalty"])) for r in rows]


def independence_from_hints(penalties: Sequence[float]) -> float:
    """Decision 1 of ТЗ-П3 (Т-§7.4): the highest hint level counts, not the sum."""
    return round(1.0 - max(penalties, default=0.0), 4)


def _squash(value: str) -> str:
    """Decision 4: no whitespace at all, casefolded."""
    return "".join(value.split()).casefold()


def _strip_fold(value: str) -> str:
    return value.strip().casefold()


def _matches(answer: str, reference: Any, normalize: Callable[[str], str]) -> bool:
    return reference is not None and normalize(answer) == normalize(str(reference))


def _code_verdict(
    task: TaskToCheck, evaluator: Literal["compute", "hybrid"], is_correct: bool, **common: Any
) -> AnswerVerdict:
    """Verdict without the LLM: every target skill gets the score (decision 8)."""
    score = 1.0 if is_correct else 0.0
    return AnswerVerdict(
        evaluator=evaluator,
        is_correct=is_correct,
        score=score,
        feedback=FEEDBACK_CORRECT if is_correct else FEEDBACK_INCORRECT,
        skill_scores=[
            {"skill_id": s, "score": score, "tested_depth": common["tested_depth"]}
            for s in task.skill_ids
        ],
        triggered_misconceptions=[],
        actor=CODE_ACTOR,
        independence=common["independence"],
    )


async def _check_math(
    compute: ComputeClient, reference: dict[str, Any], answer: str
) -> MathCheckResponse | None:
    """A1. None when compute gives no verdict: an invalid request (an answer over 1000
    characters counts as `parse_error`) or `ComputeUnavailableError`."""
    try:
        request = MathCheckRequest.model_validate(
            {
                "mode": reference.get("check_mode", "expression_equiv"),
                "student_expr": answer,
                "reference_expr": reference.get("sympy"),
                "variables": reference.get("variables", ["x"]),
            }
        )
    except ValidationError:
        return None
    try:
        return await compute.check_math(request)
    except ComputeUnavailableError:
        return None


async def evaluate_answer(
    task: TaskToCheck,
    answer: str,
    hint_penalties: Sequence[float],
    *,
    learner_id: UUID,
    session_id: UUID,
    turn_id: UUID,
    idempotency_key: str,
    gateway: LLMGateway,
    compute: ComputeClient,
    settings: Settings,
) -> AnswerVerdict:
    """The full cascade; never raises on LLM or compute failure (falls back).

    `idempotency_key` is the key of the `evaluator@1` call in the outbox.
    """
    common = {
        "independence": independence_from_hints(hint_penalties),
        "tested_depth": settings.compute_tested_depth,
    }
    reference = task.reference_solution
    if task.kind == "choice":
        return _code_verdict(
            task, "compute", _matches(answer, reference.get("option"), _strip_fold), **common
        )
    evaluator: Literal["llm", "hybrid"]
    if task.kind == "math":
        checked = await _check_math(compute, reference, answer)
        if checked is not None and checked.status == "completed":
            return _code_verdict(task, "compute", bool(checked.is_equivalent), **common)
        evaluator = "hybrid"
    else:
        evaluator = "llm"

    skill_ids = task.skill_ids
    try:
        result = await gateway.generate_structured(
            role="evaluator",
            prompt_version=1,
            response_model=EvaluatorOutput,
            variables={
                "prompt_text": task.prompt_text,
                "reference_solution_json": reference,
                "student_answer": answer,
                "target_skills_json": [{"skill_id": s} for s in skill_ids],
                "known_misconceptions_json": task.target_misconceptions,
            },
            learner_id=learner_id,
            session_id=session_id,
            turn_id=turn_id,
            idempotency_key=idempotency_key,
        )
    except LLM_ERRORS:
        expected = reference.get("answer") or reference.get("sympy")
        return _code_verdict(task, "hybrid", _matches(answer, expected, _squash), **common)

    verdict = result.value
    return AnswerVerdict(
        evaluator=evaluator,
        is_correct=verdict.is_correct,
        score=verdict.score,
        independence=common["independence"],
        feedback=verdict.feedback,
        skill_scores=[
            s.model_dump(mode="json") for s in verdict.skill_scores if str(s.skill_id) in skill_ids
        ],
        triggered_misconceptions=[
            m.model_dump(mode="json") for m in verdict.triggered_misconceptions
        ],
        actor=EVALUATOR_ACTOR,
    )
