"""Bank selection, WP12.7 (ТЗ Ф4; `Т-§5.7`): tasks of a learner that test a skill at a level,
whose family the learner has not met on this skill yet; with a misconception — the tasks whose
option traps it go first; checked tasks before the rest; at most `task_select_limit`. The Neo4j
session is read only (law 3). D-B-169.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from neo4j import AsyncDriver

from quack.config import Settings
from quack.db.neo4j import read_session

# Т-§5.7 «ориентир реализации»; every node is matched within the learner (Т-§3.5).
_SELECT = """
MATCH (s:Skill {id: $skillId, learnerId: $learnerId})<-[r:TESTS]-(t:Task)
WHERE t.learnerId = $learnerId AND r.level = $level
  AND NOT EXISTS {
    MATCH (:Task {familyId: t.familyId, learnerId: $learnerId})<-[:ON_TASK]-(:Evidence)-[:ON]->(s)
  }
OPTIONAL MATCH (t)-[:HAS_OPTION]->(:Option)-[:TRAPS]->(m:Misconception {id: $misconceptionId})
WITH t, count(m) > 0 AS trapsTarget
RETURN t.id AS task_id, trapsTarget AS traps_target, t.verification = 'checked' AS checked
ORDER BY traps_target DESC, checked DESC, t.id
LIMIT $limit
"""


@dataclass(frozen=True)
class SelectedTask:
    task_id: UUID
    traps_target: bool
    checked: bool


async def select_tasks(
    driver: AsyncDriver,
    settings: Settings,
    *,
    learner_id: UUID,
    skill_id: UUID,
    level: int,
    misconception_id: UUID | None = None,
) -> list[SelectedTask]:
    """`Т-§5.7`: the bank tasks for the skill and level, best first."""
    async with read_session(driver) as session:
        result = await session.run(
            _SELECT,
            learnerId=str(learner_id),
            skillId=str(skill_id),
            level=level,
            misconceptionId=str(misconception_id) if misconception_id else None,
            limit=settings.task_select_limit,
        )
        rows = [record async for record in result]
    return [
        SelectedTask(UUID(r["task_id"]), bool(r["traps_target"]), bool(r["checked"])) for r in rows
    ]
