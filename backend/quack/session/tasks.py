"""Task-bank pick and submission evaluation helpers (ТЗ §2.3 steps 4-5); reads `core.task_bank`."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from quack.compute_client.client import ComputeClient
from quack.events.payloads import TaskEvaluated
from quack.graph.overlay import HintView


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


async def pick_task(
    session: AsyncSession,
    learner_id: UUID,
    topic_id: UUID,
    exclude_task_ids: Collection[UUID],
    solved_family_ids: Collection[str],
    kinds: Collection[str] | None = None,
) -> TaskBankRow | None:
    """Unassigned task of the topic, preferring families without a successful solve."""
    raise NotImplementedError


async def load_task(session: AsyncSession, learner_id: UUID, task_id: UUID) -> TaskBankRow | None:
    raise NotImplementedError


def compute_independence(hints: Collection[HintView]) -> float:
    """`max(0.1, 1.0 - sum(independence_penalty))`."""
    raise NotImplementedError


async def evaluate_submission(
    compute: ComputeClient,
    task: TaskBankRow,
    answer: str,
    session_id: UUID,
    turn_id: UUID,
    hints: Collection[HintView],
) -> TaskEvaluated | None:
    """Compute check for math/choice; None for open tasks and on parse_error/compute outage."""
    raise NotImplementedError
