"""Task bank pick by skill and level (Т-§5.7; ТЗ Ф4 WP12.7, seam S7).

The query of Т-§5.7 over the learner's `Task` nodes in Neo4j: a skill and a level, families
the learner has not met on this skill, optionally trapping misconception M; tasks that
trap M first, then checked ones; `LIMIT 5`. Read session only (law 3). The Phase 3 picks
from `core.task_bank` (`quack.practice.selection`) go away with that table (WP1.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from neo4j import AsyncDriver

# Т-§5.7 ориентир: `LIMIT 5`.
SELECT_LIMIT = 5


@dataclass(frozen=True)
class SelectedTask:
    task_id: UUID
    traps_target: bool
    checked: bool


async def select_tasks(
    neo4j: AsyncDriver,
    *,
    learner_id: UUID,
    skill_id: UUID,
    level: int,
    misconception_id: UUID | None = None,
) -> list[SelectedTask]:
    """Т-§5.7: up to `SELECT_LIMIT` tasks, ordered by `traps_target`, then `checked`."""
    raise NotImplementedError
