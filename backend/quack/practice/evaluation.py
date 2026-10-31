"""One answer check for a turn with `related_task_id` and for `POST .../tasks/{id}/submit`
(WP6.2, ТЗ-П3 §2.2 п.3): choice → compute → LLM `evaluator@1` → deterministic fallback,
plus `independence` by the hints given. Same input → same `task.evaluated` fields
(`evaluator`, `is_correct`, `score`, `independence`, `skill_scores`,
`triggered_misconceptions`), checked by `T-H2`. Code moves here from `quack/artifacts/`
without changing its behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from quack.compute_client.client import ComputeClient
from quack.llm.gateway import LLMGateway


@dataclass(frozen=True)
class TaskToCheck:
    """The bank row fields the cascade needs."""

    task_id: UUID
    kind: str
    prompt_text: str
    reference_solution: dict[str, Any]
    target_skills: list[dict[str, Any]]
    target_misconceptions: list[str]
    difficulty: float


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


def independence_from_hints(hint_penalties: Sequence[float]) -> float:
    """`independence` by the penalties of the hints given for the task (WP6.2)."""
    raise NotImplementedError


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
) -> AnswerVerdict:
    """The full cascade; never raises on LLM or compute failure (falls back)."""
    raise NotImplementedError
