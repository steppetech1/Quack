"""Task pick from the bank for a degraded turn (ТЗ-П2 §2.3 п.5) and for `tasks/next`
(ТЗ-П3 §2.2 п.1) — one implementation (WP6.2)."""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from quack.practice.evaluation import TaskToCheck


async def pick_task(
    session: AsyncSession,
    learner_id: UUID,
    topic_id: UUID,
    *,
    exclude_task_ids: Collection[UUID] = (),
    solved_family_ids: Collection[str] = (),
    kinds: Collection[str] | None = None,
    session_id: UUID | None = None,
) -> TaskToCheck | None:
    """Unassigned task of the topic, families without a correct solve first."""
    raise NotImplementedError
