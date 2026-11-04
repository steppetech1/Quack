"""`GraphReader` — signatures of the shared memory read (ТЗ Ф4 WP6.4, seam S4).

Every method reads with the watermark check (Т-§2.6, `snapshot_read_retries`); methods that
serve a turn take the session overlay. The real implementation is WP6.4 (G2); streams B and C
test against `tests/support/graph_read.py`. `materials_prefetch` / `materials_search` live in
`quack/graph/read/materials.py` (stream C).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from neo4j import AsyncDriver

from quack.config import Settings
from quack.db import Db
from quack.events.values import Level
from quack.graph.overlay import SessionOverlay
from quack.graph.read.models import (
    EvidenceFact,
    ExtractionCandidates,
    Frontier,
    HubGoal,
    HubReadiness,
    MemorySlice,
    MisconceptionDetail,
    PastSolution,
    PrepContext,
    PrerequisiteFact,
    ProfileFacts,
    ReviewCandidate,
    ReviewScope,
    SimilarMistake,
    SliceFocus,
    StateAt,
    TopicMemory,
    TopicRequires,
    WhyFacts,
)


class GraphReader(Protocol):
    async def slice_for_turn(
        self,
        learner_id: UUID,
        session_id: UUID,
        focus: SliceFocus,
        overlay: SessionOverlay | None = None,
    ) -> MemorySlice: ...

    async def frontier(
        self, skill_id: UUID, target_levels: Sequence[Level], overlay: SessionOverlay | None = None
    ) -> Frontier: ...

    async def topic_readiness(self, hub_id: UUID) -> HubReadiness: ...

    async def why(self, topic_id: UUID, level: Level | None = None) -> WhyFacts: ...

    async def topic_requires(self, domain_id: UUID) -> list[TopicRequires]: ...

    async def recall_probability(self, skill_id: UUID, now_ms: int) -> float | None: ...

    async def review_candidates(
        self, learner_id: UUID, now_ms: int, scope: ReviewScope | None = None
    ) -> list[ReviewCandidate]: ...

    async def prep_context(self, hub_id: UUID, now_ms: int) -> PrepContext: ...

    async def skill_history(
        self, skill_id: UUID, level: Level | None = None, limit: int = 10
    ) -> list[EvidenceFact]: ...

    async def prerequisites(self, skill_id: UUID, limit: int = 10) -> list[PrerequisiteFact]: ...

    async def similar_mistakes(
        self, learner_id: UUID, evidence_id: UUID | None = None, text: str | None = None
    ) -> list[SimilarMistake]: ...

    async def past_solution(
        self, learner_id: UUID, task_id: UUID | None = None, skill_id: UUID | None = None
    ) -> list[PastSolution]: ...

    async def state_at(
        self, learner_id: UUID, seq_n: int, skill_ids: Sequence[UUID]
    ) -> StateAt: ...

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
        """Candidates of the extractor (WP15 п.2, п.6; D-A-135): the skills of the topics active
        in the session plus the nearest to `episode_vector` by `skill_emb`, at most `k`; no
        vector (Modal is down) — the session's skills only. `topic_id` / `skill_ids` restrict
        re-extraction to a topic (`episode.reassigned`) or to skills (`skill.split`)."""
        ...

    async def domain_goals(self, domain_id: UUID) -> list[HubGoal]:
        """Slot 4 of the teacher context (Т-§10.1): the goal hubs of the domain with the
        required levels of their topics, by the graph — merges and renames applied."""
        ...

    async def profile(self, learner_id: UUID) -> ProfileFacts:
        """Slot 3 (Т-§9.5): preferences, manner and the learned part of the profile."""
        ...

    async def topic_memory(self, topic_ids: Sequence[UUID]) -> list[TopicMemory]:
        """Skills and misconceptions of the topics of the session goal (Т-§10.2); an unknown
        topic id is skipped."""
        ...

    async def misconception_detail(self, misconception_id: UUID) -> MisconceptionDetail | None:
        """One misconception with all manifestations (WP16.2, Т-§9.3). The id goes through the
        alias: after `misconception.merged` the target is read; `None` — no such one."""
        ...


def create_graph_reader(
    neo4j: AsyncDriver, settings: Settings, *, learner_id: UUID, db: Db | None = None
) -> GraphReader:
    """The reader of a process, **bound to one learner**: built from the resources of
    `JobContext` (`ctx.neo4j`, `ctx.settings`), not a field of it (the field set is a contract,
    ТЗ Ф4 §4.2), with `learner_id=job.learner_id`. A reader without a learner does not exist:
    methods that take only an id of a node (`topic_memory`, `topic_requires`, ...) would take the
    owner from the node and read another learner's data (law 1, Т-§3.4; D-A-144). The
    implementation is `Neo4jGraphReader` (WP6.4, G2); tests pass `FixedGraphReader`. `db`
    (`ctx.db`) lets `state_at` replay the log of Phase 4 with `rules@3` (WP8.3, D-A-191) and
    `slice_for_turn` wait for the journal (D-A-210)."""
    from quack.graph.read.neo4j_reader import Neo4jGraphReader

    return Neo4jGraphReader(neo4j, settings, learner_id=learner_id, db=db)
