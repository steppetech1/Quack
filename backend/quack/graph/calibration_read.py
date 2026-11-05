"""Evidence read for the weekly calibration of the tier weights (ТЗ Ф4 WP7.5; D-A-245).

One query per learner reads the active evidence of all the skills the window tests. It is a
read of the graph slice for a job, not a contract read (`graph/read/`): nothing is written and
every query carries the learner (law 1). `unit_evidence` picks, for one observation (skill, level,
answer), the evidence of one tier that the state could know before the answer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from neo4j import AsyncDriver

from quack.db.neo4j import cypher

_QUERY = (
    "MATCH (e:Evidence)-[:ON]->(s:Skill) WHERE s.learnerId = $l AND e.learnerId = $l "
    "AND e.status = 'active' AND e.level >= 1 AND s.id IN $skills "
    "RETURN s.id AS skill, e.level AS level, e.tier AS tier, e.outcome AS outcome, "
    "e.assistance AS assistance, e.sourceSeq AS seq"
)


@dataclass(frozen=True)
class TierEvidence:
    """One active evidence node, as much of it as the calibration of the weights needs."""

    skill_id: UUID
    level: int
    tier: int
    outcome: str
    assistance: str
    seq: int


EvidenceReader = Callable[[UUID, Sequence[UUID]], Awaitable[list[TierEvidence]]]


def _int(value: object) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


async def read_tier_evidence(
    neo4j: AsyncDriver, learner_id: UUID, skill_ids: Sequence[UUID]
) -> list[TierEvidence]:
    """Active evidence on `skill_ids` of one learner (one query, read routing)."""
    if not skill_ids:
        return []
    result = await neo4j.execute_query(
        cypher(_QUERY),
        parameters_={"l": str(learner_id), "skills": [str(s) for s in skill_ids]},
        routing_="r",
    )
    return [
        TierEvidence(
            skill_id=UUID(str(r["skill"])),
            level=_int(r["level"]),
            tier=_int(r["tier"]),
            outcome=str(r["outcome"]),
            assistance=str(r["assistance"] or "none"),
            seq=_int(r["seq"]),
        )
        for r in result.records
    ]


def unit_evidence(
    evidence: Iterable[TierEvidence], skill_id: UUID, level: int, tier: int, answer_seq: int
) -> list[TierEvidence]:
    """Evidence of `tier` on (`skill_id`, `level`) that was in the graph before the answer:
    `sourceSeq < answer_seq`, a decided outcome, no shown solution (Т-§7.4)."""
    return [
        e
        for e in evidence
        if e.skill_id == skill_id
        and e.level == level
        and e.tier == tier
        and e.seq < answer_seq
        and e.outcome in {"correct", "incorrect"}
        and e.assistance != "shown_solution"
    ]
