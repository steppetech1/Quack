"""Rebuild of one learner on `rules@3` (ТЗ Ф4 WP3.5; Т-§2.7, Д-§6.3).

Under the learner's advisory lock (the same as the batch loop and deletion): every event of the
learner through phases A–C in memory, batch by batch (the same handlers and folds, Т-§2.5 —
batches give the graph of applying at once, T-P1), then the subgraph in Neo4j is replaced. A
learner larger than `rebuild_max_tx_ops` is written by parts and only without an active session
(presence in Redis); vectors come from `embedding.ready` events like everything else.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.db import LockKeyspace
from quack.db.redis import presence_key
from quack.events.log import StoredEvent
from quack.graph.projection.phase_a import EventFailed, apply_events
from quack.graph.projection.phase_b import WholeSlice, run_folds
from quack.graph.projection.runner import RULES_VERSION
from quack.graph.projection.store import replace_learner
from quack.graph.projection.types import SubGraph
from quack.graph.projection.view import BatchSlice, RulesParams
from quack.logging import get_logger

log = get_logger(__name__)

_EVENTS = (
    "SELECT learner_id, seq, id, type, schema_version, actor, domain_id, session_id, "
    "causation_seq, correlation_id, idempotency_key, payload_hash, occurred_at, payload, "
    "source_refs FROM core.events WHERE learner_id = %(learner_id)s ORDER BY seq"
)
_OFFSET = (
    "INSERT INTO ops.projector_offsets (learner_id, seq) VALUES (%(learner_id)s, %(seq)s) "
    "ON CONFLICT (learner_id) DO UPDATE SET seq = EXCLUDED.seq"
)

type RebuildStatus = Literal["rebuilt", "busy", "active_session", "failed"]


@dataclass(frozen=True)
class RebuildOutcome:
    status: RebuildStatus
    applied_seq: int = 0
    operations: int = 0
    failed_seq: int | None = None


async def project_all(
    learner_id: UUID, events: Sequence[StoredEvent], settings: Settings
) -> SubGraph:
    """Phases A–C over the whole log in memory, in batches of `projector_batch_max_events`."""
    store, done = SubGraph(), list[StoredEvent]()
    size = max(1, settings.projector_batch_max_events)
    params = RulesParams.from_settings(settings)
    for start in range(0, len(events), size):
        chunk = list(events[start : start + size])
        batch = BatchSlice(str(learner_id), store, done, params)
        apply_events(chunk, batch)
        try:
            await run_folds(batch, settings, WholeSlice([*done, *chunk]), chunk)
        except Exception as exc:  # a fold error is a program error (D-A-186)
            raise EventFailed(chunk[-1].seq, exc) from exc
        store, done = batch.snapshot(), [*done, *chunk]
    return store


async def rebuild_learner(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    learner_id: UUID,
    *,
    redis: Redis | None = None,
    respect_presence: bool = True,
) -> RebuildOutcome:
    async with db.advisory_lock(LockKeyspace.LEARNER_PROJECTION, learner_id) as acquired:
        if not acquired:
            return RebuildOutcome("busy")
        events = await db.fetch_all(_EVENTS, {"learner_id": learner_id}, row=StoredEvent)
        try:
            graph = await project_all(learner_id, events, settings)
        except EventFailed as failed:
            log.error("rebuild_failed", learner_id=str(learner_id), seq=failed.seq)
            return RebuildOutcome("failed", failed_seq=failed.seq)
        applied = events[-1].seq if events else 0
        operations = len(graph.nodes) + len(graph.rels)
        limit = settings.rebuild_max_tx_ops
        chunk: int | None = None
        if operations > limit:
            if (
                respect_presence
                and redis is not None
                and await redis.exists(presence_key(learner_id))
            ):
                return RebuildOutcome("active_session", applied, operations)
            chunk = limit
            # Written by parts the graph is not whole until the last write: with the copy of the
            # watermark at 0 a rebuild that fails midway leaves the learner to the batch loop
            # (`_PENDING`), which clears the partial graph and projects the log again (Т-§2.7).
            await db.execute(_OFFSET, {"learner_id": learner_id, "seq": 0})
        await replace_learner(neo4j, str(learner_id), graph, applied, RULES_VERSION, chunk=chunk)
        await db.execute(_OFFSET, {"learner_id": learner_id, "seq": applied})
        log.info("rebuild_done", learner_id=str(learner_id), seq=applied, operations=operations)
        return RebuildOutcome("rebuilt", applied, operations)
