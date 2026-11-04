"""Bank selection, WP12.7 (ТЗ Ф4; `Т-§5.7`): skill and level, a family met on the skill is
skipped, a task trapping the asked misconception first, checked before unchecked, the limit,
another learner's tasks never (D-B-169)."""

from __future__ import annotations

from typing import Any, LiteralString
from uuid import UUID, uuid4

from neo4j import AsyncDriver

from quack.config import Settings
from quack.practice.select import select_tasks

LEARNER, OTHER = uuid4(), uuid4()
SKILL, MISCONCEPTION = uuid4(), uuid4()


async def _run(driver: AsyncDriver, query: LiteralString, **params: Any) -> None:
    async with driver.session() as session:
        await session.run(query, **params)


async def _task(
    driver: AsyncDriver,
    *,
    learner: UUID = LEARNER,
    skill: UUID = SKILL,
    level: int = 3,
    family: str | None = None,
    checked: bool = False,
    traps: bool = False,
) -> UUID:
    task_id = uuid4()
    await _run(
        driver,
        "MERGE (s:Skill {id: $skill, learnerId: $learner}) "
        "CREATE (t:Task {id: $task, learnerId: $learner, familyId: $family, "
        "verification: $verification})-[:TESTS {level: $level, part: 'main'}]->(s)",
        skill=str(skill),
        learner=str(learner),
        task=str(task_id),
        family=family or str(task_id),
        verification="checked" if checked else "unchecked",
        level=level,
    )
    if traps:
        await _run(
            driver,
            "MATCH (t:Task {id: $task}) MERGE (m:Misconception {id: $m, learnerId: $learner}) "
            "CREATE (t)-[:HAS_OPTION]->(:Option {id: $option})-[:TRAPS]->(m)",
            task=str(task_id),
            m=str(MISCONCEPTION),
            learner=str(learner),
            option=str(uuid4()),
        )
    return task_id


async def _met(driver: AsyncDriver, task_id: UUID) -> None:
    """The learner has evidence on the skill from this task (its family is met)."""
    await _run(
        driver,
        "MATCH (t:Task {id: $task}), (s:Skill {id: $skill, learnerId: $learner}) "
        "CREATE (t)<-[:ON_TASK]-(:Evidence {id: $e, learnerId: $learner})-[:ON]->(s)",
        task=str(task_id),
        skill=str(SKILL),
        learner=str(LEARNER),
        e=str(uuid4()),
    )


async def test_select_tasks(neo4j: AsyncDriver) -> None:
    plain = await _task(neo4j)
    checked = await _task(neo4j, checked=True)
    trap = await _task(neo4j, traps=True)
    await _task(neo4j, level=4)  # another level
    await _task(neo4j, learner=OTHER, skill=uuid4(), checked=True)  # another learner
    seen = await _task(neo4j, family="fam-1")
    await _task(neo4j, family="fam-1", checked=True)  # same family as a met task
    await _met(neo4j, seen)
    await _run(  # a task of another learner wrongly linked to this skill: never selected
        neo4j,
        "MATCH (s:Skill {id: $skill}) CREATE (:Task {id: $task, learnerId: $other, "
        "familyId: $task, verification: 'checked'})-[:TESTS {level: 3, part: 'main'}]->(s)",
        skill=str(SKILL),
        task=str(uuid4()),
        other=str(OTHER),
    )

    settings = Settings()
    found = await select_tasks(
        neo4j, settings, learner_id=LEARNER, skill_id=SKILL, level=3, misconception_id=MISCONCEPTION
    )
    assert [t.task_id for t in found] == [trap, checked, plain]
    assert [(t.traps_target, t.checked) for t in found] == [
        (True, False),
        (False, True),
        (False, False),
    ]

    no_m = await select_tasks(neo4j, settings, learner_id=LEARNER, skill_id=SKILL, level=3)
    assert no_m[0].task_id == checked  # without a misconception: checked first
    one = await select_tasks(
        neo4j, Settings(task_select_limit=1), learner_id=LEARNER, skill_id=SKILL, level=3
    )
    assert [t.task_id for t in one] == [checked]
