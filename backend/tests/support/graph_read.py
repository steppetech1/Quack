"""`FixedGraphReader` — the `quack.graph.read` double with fixed data (ТЗ Ф4 WP6.4, seam S4).

For the tests of streams B and C until WP6.4 (G2) lands. The data is one small learner, the same
on every call, so assertions can name ids:

- `SKILL_PROVEN` — level 3 proven by a direct Т1/Т2 (`proven_by_direct`), level 4 not tested;
- `SKILL_FAILED` — level 3 `failed`, the latest Т1 of the level is a failure (`last_t1_failed`);
- `SKILL_QUIET` — nothing checked (law 8: `not_tested`, not `failed`).

`FixedGraphReader(lag_degraded=True)` gives a slice the projector did not catch up with.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from quack.events.values import Level
from quack.graph.overlay import SessionOverlay
from quack.graph.read import (
    CandidateSkill,
    EvidenceFact,
    ExtractionCandidates,
    Frontier,
    HubGoal,
    HubReadiness,
    HubTopicGoal,
    LevelFact,
    ManifestationFact,
    MaterialRef,
    MemorySlice,
    MisconceptionDetail,
    MisconceptionFact,
    PastSolution,
    PrepContext,
    PrerequisiteFact,
    ProfileFacts,
    RequiresLink,
    ReviewCandidate,
    ReviewScope,
    RootCauseFact,
    SimilarMistake,
    SkillFact,
    SliceFocus,
    StateAt,
    TopicMemory,
    TopicRequires,
    WhyFacts,
)

LEARNER = UUID(int=0xA0)
DOMAIN = UUID(int=0xD0)
HUB = UUID(int=0xB0)
TOPIC_READY = UUID(int=0x71)
TOPIC_OPEN = UUID(int=0x72)
SKILL_PROVEN = UUID(int=0x51)
SKILL_FAILED = UUID(int=0x52)
SKILL_QUIET = UUID(int=0x53)
MISCONCEPTION = UUID(int=0x61)
MISCONCEPTION_STATEMENT = "adds the denominators when adding fractions"
MISCONCEPTION_TRIGGERS = ("fractions with different denominators",)
LAPSE_AT_MS = 1_699_000_000_000
HINT_STEP = 2
EVIDENCE_PROVEN = UUID(int=0xE1)
EVIDENCE_FAILED = UUID(int=0xE2)
PROJECTED_SEQ = 120
NOW_MS = 1_700_000_000_000

_LEVELS: dict[UUID, list[LevelFact]] = {
    SKILL_PROVEN: [
        LevelFact(
            level=3,
            status="proven",
            basis="direct",
            deciding_evidence_id=EVIDENCE_PROVEN,
            evidence_count=3,
            proven_by_direct=True,
        ),
        LevelFact(level=4, status="not_tested"),
    ],
    SKILL_FAILED: [
        LevelFact(
            level=3,
            status="failed",
            deciding_evidence_id=EVIDENCE_FAILED,
            evidence_count=1,
            last_t1_failed=True,
        )
    ],
    SKILL_QUIET: [LevelFact(level=3, status="not_tested")],
}
# SKILL_FAILED requires SKILL_PROVEN (shown, evidenced) and SKILL_QUIET (weak link, untouched).
_REQUIRES: dict[UUID, list[RequiresLink]] = {
    SKILL_FAILED: [
        RequiresLink(skill_id=SKILL_PROVEN, confidence=0.9, has_exposure=True, has_evidence=True),
        RequiresLink(skill_id=SKILL_QUIET, confidence=0.4),
    ]
}
_TOPIC_OF = {SKILL_PROVEN: TOPIC_READY, SKILL_FAILED: TOPIC_OPEN, SKILL_QUIET: TOPIC_OPEN}
_NAMES = {SKILL_PROVEN: "add fractions", SKILL_FAILED: "common denominator",
          SKILL_QUIET: "reduce a fraction"}  # fmt: skip
_TOPIC_NAMES = {TOPIC_READY: "adding fractions", TOPIC_OPEN: "reducing fractions"}
GOAL_DEADLINE_MS = NOW_MS + 5 * 86_400_000
EVIDENCE_MANIFEST = UUID(int=0xE3)
# Signals of the failed skill: a gap type with its confidence and a memory lapse (Т-§8.2, Т-§8.4).
_SIGNALS: dict[UUID, dict[str, Any]] = {
    SKILL_FAILED: {
        "gap_type": "execution",
        "gap_type_confidence": 0.8,
        "last_lapse_at_ms": LAPSE_AT_MS,
    },
}


def _evidence(evidence_id: UUID, skill_id: UUID, outcome: str, seq: int) -> EvidenceFact:
    return EvidenceFact.model_validate(
        {
            "evidence_id": evidence_id,
            "skill_id": skill_id,
            "level": 3,
            "outcome": outcome,
            "source": "code",
            "quote": "a = b",
            "seq": seq,
            "occurred_at_ms": NOW_MS - seq,
        }
    )


def _misconception(**extra: Any) -> MisconceptionFact:
    return MisconceptionFact.model_validate(
        {
            "misconception_id": MISCONCEPTION,
            "skill_id": SKILL_FAILED,
            "status": "suspected",
            "statement": MISCONCEPTION_STATEMENT,
            "triggers": list(MISCONCEPTION_TRIGGERS),
            **extra,
        }
    )


def _skill(skill_id: UUID) -> SkillFact:
    return SkillFact(
        skill_id=skill_id,
        name=_NAMES[skill_id],
        topic_id=_TOPIC_OF[skill_id],
        topic_name=_TOPIC_NAMES[_TOPIC_OF[skill_id]],
        domain_id=DOMAIN,
        levels=_LEVELS[skill_id],
        requires=_REQUIRES.get(skill_id, []),
        **_SIGNALS.get(skill_id, {}),
    )


class FixedGraphReader:
    """Implements `quack.graph.read.GraphReader` with the fixed data above."""

    def __init__(self, *, lag_degraded: bool = False) -> None:
        self.lag_degraded = lag_degraded

    async def slice_for_turn(
        self,
        learner_id: UUID,
        session_id: UUID,
        focus: SliceFocus,
        overlay: SessionOverlay | None = None,
    ) -> MemorySlice:
        wanted = focus.skill_ids or list(_LEVELS)
        return MemorySlice(
            learner_id=learner_id,
            projected_seq=PROJECTED_SEQ,
            lag_degraded=self.lag_degraded,
            skills=[_skill(s) for s in wanted if s in _LEVELS],
            misconceptions=[
                MisconceptionFact(
                    misconception_id=MISCONCEPTION,
                    skill_id=SKILL_FAILED,
                    status="suspected",
                    statement=MISCONCEPTION_STATEMENT,
                    triggers=list(MISCONCEPTION_TRIGGERS),
                    manifestation_count=1,
                    quotes=["a = b"],
                )
            ],
            root_causes=[RootCauseFact(skill_id=SKILL_QUIET, hypothesis_count=2)],
        )

    async def frontier(
        self, skill_id: UUID, target_levels: Sequence[Level], overlay: SessionOverlay | None = None
    ) -> Frontier:
        reached = {f.level for f in _LEVELS.get(skill_id, []) if f.status in ("proven", "implied")}
        open_levels = [level for level in sorted(target_levels) if level not in reached]
        return Frontier(skill_id=skill_id, level=open_levels[0] if open_levels else None)

    async def topic_readiness(self, hub_id: UUID) -> HubReadiness:
        return HubReadiness(
            hub_id=hub_id, ready_topic_ids=[TOPIC_READY], not_ready_topic_ids=[TOPIC_OPEN]
        )

    async def why(self, topic_id: UUID, level: Level | None = None) -> WhyFacts:
        deciding = _evidence(EVIDENCE_PROVEN, SKILL_PROVEN, "correct", 100)
        return WhyFacts(topic_id=topic_id, level=3, deciding=deciding, recent=[deciding])

    async def topic_requires(self, domain_id: UUID) -> list[TopicRequires]:
        return [TopicRequires(from_topic_id=TOPIC_OPEN, to_topic_id=TOPIC_READY, weight=2)]

    async def recall_probability(self, skill_id: UUID, now_ms: int) -> float | None:
        return 0.9 if skill_id == SKILL_PROVEN else None

    async def review_candidates(
        self, learner_id: UUID, now_ms: int, scope: ReviewScope | None = None
    ) -> list[ReviewCandidate]:
        found = [
            ReviewCandidate(skill_id=SKILL_PROVEN, priority=0.7, reasons=["retention"]),
            ReviewCandidate(
                skill_id=SKILL_FAILED,
                priority=0.4,
                reasons=["misconception_recheck", "hub_requirement"],
            ),
        ]
        if scope is not None and scope.max_items is not None:
            found = found[: scope.max_items]
        return found

    async def prep_context(self, hub_id: UUID, now_ms: int) -> PrepContext:
        return PrepContext(
            hub_id=hub_id,
            days_to_deadline=5,
            ready_count=1,
            total_count=2,
            coverage_topic_ids=[TOPIC_READY, TOPIC_OPEN],
            control_materials=[
                MaterialRef(
                    chunk_id=UUID(int=0xC1), material_id=UUID(int=0xC2), kind="past_variant", page=4
                )
            ],
            candidates=await self.review_candidates(LEARNER, now_ms),
        )

    async def skill_history(
        self, skill_id: UUID, level: Level | None = None, limit: int = 10
    ) -> list[EvidenceFact]:
        if skill_id == SKILL_FAILED:
            hinted = _evidence(EVIDENCE_FAILED, skill_id, "incorrect", 110).model_dump()
            hinted |= {"assistance": "hint", "hint_step": HINT_STEP}
            return [EvidenceFact.model_validate(hinted)][:limit]
        return [_evidence(EVIDENCE_PROVEN, skill_id, "correct", 100)][:limit]

    async def prerequisites(self, skill_id: UUID, limit: int = 10) -> list[PrerequisiteFact]:
        return [PrerequisiteFact(skill_id=SKILL_PROVEN, levels=_LEVELS[SKILL_PROVEN])][:limit]

    async def similar_mistakes(
        self, learner_id: UUID, evidence_id: UUID | None = None, text: str | None = None
    ) -> list[SimilarMistake]:
        return [SimilarMistake(evidence_id=EVIDENCE_FAILED, skill_id=SKILL_FAILED, quote="a = b")]

    async def past_solution(
        self, learner_id: UUID, task_id: UUID | None = None, skill_id: UUID | None = None
    ) -> list[PastSolution]:
        return [PastSolution(skill_id=SKILL_PROVEN, lines=["a = b"], occurred_at_ms=NOW_MS)]

    async def state_at(self, learner_id: UUID, seq_n: int, skill_ids: Sequence[UUID]) -> StateAt:
        return StateAt(
            learner_id=learner_id,
            seq=seq_n,
            skills=[_skill(s) for s in skill_ids if s in _LEVELS],
        )

    async def extraction_candidates(
        self,
        learner_id: UUID,
        session_id: UUID,
        episode_vector: Sequence[float] | None,
        k: int,
        *,
        topic_id: UUID | None = None,
        skill_ids: Sequence[UUID] | None = None,
    ) -> ExtractionCandidates:
        wanted = list(_LEVELS)
        if topic_id is not None:
            wanted = [s for s in wanted if _TOPIC_OF[s] == topic_id]
        if skill_ids is not None:
            wanted = [s for s in wanted if s in skill_ids]
        wanted = wanted[:k]
        skills = [CandidateSkill(skill_id=s, name=_NAMES[s], topic_id=_TOPIC_OF[s]) for s in wanted]
        misconception = MisconceptionFact(
            misconception_id=MISCONCEPTION,
            skill_id=SKILL_FAILED,
            status="suspected",
            statement=MISCONCEPTION_STATEMENT,
            triggers=list(MISCONCEPTION_TRIGGERS),
        )
        known = [misconception] if SKILL_FAILED in wanted else []
        return ExtractionCandidates(skills=skills, misconceptions=known)

    async def domain_goals(self, domain_id: UUID) -> list[HubGoal]:
        return [
            HubGoal(
                hub_id=HUB,
                name="fractions exam",
                kind="exam",
                control_type="tasks",
                deadline_at_ms=GOAL_DEADLINE_MS,
                topics=[
                    HubTopicGoal(topic_id=t, name=_TOPIC_NAMES[t], required_levels=[2, 3])
                    for t in (TOPIC_READY, TOPIC_OPEN)
                ],
            )
        ]

    async def profile(self, learner_id: UUID) -> ProfileFacts:
        return ProfileFacts(
            learner_id=learner_id,
            prefs={"language": "ru", "answer_length": "short"},
            manner="short steps, no lectures",
            learned_helpful_approach="small_steps",
            learned_confidence_brier=0.21,
            learned_confidence_bias="over",
            learned_branch_rate=None,  # fewer than `profile_min_samples`: «no data»
            learned_reasoning_share=0.4,
        )

    async def topic_memory(self, topic_ids: Sequence[UUID]) -> list[TopicMemory]:
        found = []
        for topic_id in topic_ids:
            if topic_id not in _TOPIC_NAMES:
                continue
            skills = [_skill(s) for s in _LEVELS if _TOPIC_OF[s] == topic_id]
            found.append(
                TopicMemory(
                    topic_id=topic_id,
                    name=_TOPIC_NAMES[topic_id],
                    domain_id=DOMAIN,
                    skills=skills,
                    misconceptions=[_misconception()] if topic_id == TOPIC_OPEN else [],
                )
            )
        return found

    async def misconception_detail(self, misconception_id: UUID) -> MisconceptionDetail | None:
        if misconception_id != MISCONCEPTION:
            return None
        return MisconceptionDetail(
            misconception_id=MISCONCEPTION,
            status="confirmed",
            statement=MISCONCEPTION_STATEMENT,
            triggers=list(MISCONCEPTION_TRIGGERS),
            skill_id=SKILL_FAILED,
            skill_name=_NAMES[SKILL_FAILED],
            manifestations=[
                ManifestationFact(
                    evidence_id=EVIDENCE_FAILED,
                    quote="a = b",
                    task_id=None,
                    source_seq=110,
                    at_ms=NOW_MS - 110,
                    tier="code",
                ),
                ManifestationFact(
                    evidence_id=EVIDENCE_MANIFEST,
                    quote=None,
                    task_id=UUID(int=0x77),
                    source_seq=115,
                    at_ms=NOW_MS - 115,
                    tier="model",
                ),
            ],
        )
