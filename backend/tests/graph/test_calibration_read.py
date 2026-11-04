"""Evidence read of the tier-weight calibration (ТЗ Ф4 WP7.5; D-A-245): one query per learner,
active evidence only and of that learner only; the unit picks the level, the tier, the sources
before the answer, decided outcomes and no shown solution."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from neo4j import AsyncDriver

from quack.graph.calibration_read import read_tier_evidence, unit_evidence


async def test_only_active_evidence_of_the_learner_before_the_answer_is_picked(
    neo4j: AsyncDriver,
) -> None:
    learner, other, skill = uuid4(), uuid4(), uuid4()

    async def seed(owner: UUID, skill_id: UUID, **props: Any) -> None:
        base = {"level": 3, "tier": 2, "outcome": "correct", "status": "active", "sourceSeq": 5}
        base["assistance"] = "none"
        await neo4j.execute_query(
            "MERGE (s:Skill {id: $skill}) SET s.learnerId = $owner "
            "CREATE (e:Evidence {id: $id}) SET e += $props, e.learnerId = $owner "
            "CREATE (e)-[:ON]->(s)",
            skill=str(skill_id),
            owner=str(owner),
            id=str(uuid4()),
            props={**base, **props},
        )

    await seed(learner, skill)  # picked
    await seed(learner, skill, tier=3, outcome="incorrect")  # another tier
    await seed(learner, skill, status="superseded")  # not active: not read
    await seed(learner, skill, level=0)  # level 0: not read
    await seed(learner, skill, level=2)  # another level
    await seed(learner, skill, sourceSeq=9)  # at or after the answer (answer_seq 9)
    await seed(learner, skill, outcome="partial")  # not decided
    await seed(learner, skill, assistance="shown_solution")  # the solution was shown
    await seed(other, uuid4(), tier=2)  # another learner, another skill
    read = await read_tier_evidence(neo4j, learner, [skill])
    assert len(read) == 6  # all the active evidence of the learner with level >= 1
    picked = unit_evidence(read, skill, 3, 2, 9)
    assert [(e.tier, e.outcome, e.seq) for e in picked] == [(2, "correct", 5)]
    assert [e.outcome for e in unit_evidence(read, skill, 3, 3, 9)] == ["incorrect"]
    assert await read_tier_evidence(neo4j, learner, []) == []
