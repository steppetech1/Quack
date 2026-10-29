"""Read/write interfaces of a learner graph: Neo4j in production, in memory for rebuild,
overlay and tests. Both implementations must give identical results (Т-§2.5)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Protocol

from quack.graph.mutations import Mutation
from quack.graph.types import BatchNeed, LearnerWatermark, SubGraph


class GraphSource(Protocol):
    async def load_watermark(self, learner_id: str) -> LearnerWatermark | None:
        """Learner.appliedSeq / projectorVersion, or None if the learner node is absent."""
        ...

    async def load_batch_slice(self, need: BatchNeed) -> SubGraph:
        """Query 1: aliases, mentioned nodes (+ one hop out), all Domain/Topic/Skill/
        Misconception nodes of affected domains with rels between them, evidence of
        superseded seqs."""
        ...

    async def load_fold_history(
        self, learner_id: str, skill_ids: Collection[str], misconception_ids: Collection[str]
    ) -> SubGraph:
        """Query 2: Evidence (EVIDENCE_FOR) and Exposure (EXPOSED_TO) of skills and
        manifestations (OBSERVED_MISCONCEPTION) of misconceptions, with the rels."""
        ...

    async def load_topic_context(self, learner_id: str, topic_ids: Collection[str]) -> SubGraph:
        """Query 3: topics, their skills (PART_OF), DEPENDS_ON rels into those skills,
        prerequisite and successor topics (PREREQ_OF)."""
        ...


class GraphSink(Protocol):
    async def apply(self, mutations: Sequence[Mutation]) -> None:
        """Apply all mutations in one transaction."""
        ...


class GraphStore(GraphSource, GraphSink, Protocol):
    """A store that is both read and written by the projector."""
