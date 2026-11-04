"""The batch of one learner, `rules@3` (ТЗ Ф4 WP3.3; Т-§2.4; D-A-181).

Under the learner's advisory lock (the same rebuild and deletion take): the watermark from
`Learner.projectedSeq`, the next `projector_batch_size` events after it, the slice their handlers
declared, phase A, then the difference and the new watermark in one Neo4j transaction; the copy
of the watermark in `ops.projector_offsets` after the commit. An event the projector cannot
apply stops the learner's stream with a row in `ops.projector_failures`; `resume` removes it.
Phase B folds the skills the batch touched (WP4); misconceptions, the profile and topics
(WP5–WP6) plug in after it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import orjson
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.db import LockKeyspace
from quack.db.redis import projector_applied_channel
from quack.events.log import StoredEvent
from quack.graph.profile_fold import PROFILE_EVENT_TYPES
from quack.graph.projection.phase_a import EventFailed, apply_events, batch_need
from quack.graph.projection.phase_b import FoldLoader, run_folds
from quack.graph.projection.store import (
    load_fold_inputs,
    load_profile_evidence,
    load_slice,
    load_topic_inputs,
    read_watermark,
    write_batch,
)
from quack.graph.projection.types import NodeRef, SubGraph
from quack.graph.projection.view import BatchSlice, Need, RulesParams
from quack.logging import get_logger

log = get_logger(__name__)

# WP3.6: the version of handlers, folds and parameters written on `Learner.projectorVersion`.
RULES_VERSION = "rules@3"

_EVENT_COLUMNS = (
    "learner_id, seq, id, type, schema_version, actor, domain_id, session_id, causation_seq, "
    "correlation_id, idempotency_key, payload_hash, occurred_at, payload, source_refs"
)
_EVENTS_AFTER = (
    "SELECT " + _EVENT_COLUMNS + " FROM core.events "
    "WHERE learner_id = %(learner_id)s AND seq > %(after)s ORDER BY seq LIMIT %(limit)s"
)
_EVENTS_BY_SEQ = (
    "SELECT " + _EVENT_COLUMNS + " FROM core.events "
    "WHERE learner_id = %(learner_id)s AND seq = ANY(%(seqs)s) ORDER BY seq"
)
_FAILURE = "SELECT seq, error FROM ops.projector_failures WHERE learner_id = %(learner_id)s"
_RECORD_FAILURE = (
    "INSERT INTO ops.projector_failures (learner_id, seq, error) "
    "VALUES (%(learner_id)s, %(seq)s, %(error)s) "
    "ON CONFLICT (learner_id) DO UPDATE SET seq = EXCLUDED.seq, error = EXCLUDED.error"
)
_RESUME = "DELETE FROM ops.projector_failures WHERE learner_id = %(learner_id)s"
_OFFSET = (
    "INSERT INTO ops.projector_offsets (learner_id, seq) VALUES (%(learner_id)s, %(seq)s) "
    "ON CONFLICT (learner_id) DO UPDATE SET seq = GREATEST(ops.projector_offsets.seq, EXCLUDED.seq)"
)

type Status = Literal["applied", "empty", "stopped", "failed", "busy"]


@dataclass(frozen=True)
class BatchOutcome:
    """`busy` — another projector, rebuild or deletion holds the learner; `stopped` — the stream
    stands on a failure; `failed` — this batch stopped it."""

    status: Status
    applied_seq: int
    failed_seq: int | None = None


def _event_refs(need: Need, events: dict[int, StoredEvent]) -> set[NodeRef]:
    refs: set[NodeRef] = set()
    for seq, field_name, label in need.event_refs:
        event = events.get(seq)
        value = event.payload.get(field_name) if event is not None else None
        if isinstance(value, str):
            refs.add((label, value))
    return refs


_PROFILE_EVENTS = (
    "SELECT " + _EVENT_COLUMNS + " FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = ANY(%(types)s) ORDER BY seq"
)


async def _profile_events(db: Db, learner_id: UUID) -> list[StoredEvent]:
    """The journal events the profile fold reads (WP5.3)."""
    types = sorted(PROFILE_EVENT_TYPES)
    return await db.fetch_all(
        _PROFILE_EVENTS, {"learner_id": learner_id, "types": types}, row=StoredEvent
    )


class Neo4jFolds:
    """Phases B–C read what the first slice did not hold from Neo4j and the journal."""

    def __init__(self, neo4j: AsyncDriver, db: Db, learner_id: UUID) -> None:
        self._neo4j, self._db, self._learner_id = neo4j, db, learner_id
        self._learner = str(learner_id)

    async def profile_inputs(self) -> tuple[SubGraph, Sequence[StoredEvent]]:
        evidence = await load_profile_evidence(self._neo4j, self._learner)
        return evidence, await _profile_events(self._db, self._learner_id)

    async def fold_inputs(self, skills: set[str], misconceptions: set[str]) -> SubGraph:
        return await load_fold_inputs(self._neo4j, self._learner, skills, misconceptions)

    async def topic_inputs(
        self, topics: set[str], skills: set[str], hubs: set[str]
    ) -> tuple[SubGraph, set[str]]:
        return await load_topic_inputs(self._neo4j, self._learner, topics, skills, hubs)


async def _publish(redis: Redis, learner_id: UUID, applied: int, topics: list[str]) -> None:
    """After the commit (Т-§2.4, ТЗ Ф4 §4.8): which topics changed, on the channel the learner
    stream reads (`projection_advanced`). A lost message is only logged (law 9)."""
    try:
        message = orjson.dumps({"applied_seq": applied, "topics": topics}).decode()
        await redis.publish(projector_applied_channel(learner_id), message)
    except Exception:
        log.warning("projector_applied_publish_failed", learner_id=str(learner_id))


async def load_batch_inputs(
    db: Db, neo4j: AsyncDriver, learner_id: UUID, events: Sequence[StoredEvent]
) -> tuple[SubGraph, list[StoredEvent]]:
    """What the handlers of `events` declared (`needs`): the slice from Neo4j and the earlier
    journal events they read. Reads only; the batch loop and the session overlay share it."""
    need = batch_need(events)
    in_batch = {e.seq for e in events}
    wanted = sorted(need.events - in_batch)
    earlier = await db.fetch_all(
        _EVENTS_BY_SEQ, {"learner_id": learner_id, "seqs": wanted}, row=StoredEvent
    )
    known = {e.seq: e for e in [*earlier, *events]}
    loaded = await load_slice(neo4j, str(learner_id), need, _event_refs(need, known))
    return loaded, earlier


async def run_batch(
    learner_id: UUID,
    events: Sequence[StoredEvent],
    loaded: SubGraph,
    earlier: Sequence[StoredEvent],
    settings: Settings,
    loader: FoldLoader,
) -> BatchSlice:
    """Phases A–C of `events` over `loaded` in memory (Т-§2.4); nothing is written. The same
    handlers and folds serve the batch loop and the session overlay (Т-§2.5). An event or a fold
    the rules cannot apply raises `EventFailed`."""
    batch = BatchSlice(str(learner_id), loaded, earlier, RulesParams.from_settings(settings))
    apply_events(events, batch)
    try:
        await run_folds(batch, settings, loader, events)
    except Exception as exc:  # a fold error is a program error, like a handler's
        raise EventFailed(events[-1].seq, exc) from exc
    return batch


async def project_learner(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    learner_id: UUID,
    *,
    redis: Redis | None = None,
) -> BatchOutcome:
    """Apply the next batch of one learner (Т-§2.4). A temporary Neo4j error propagates: the
    caller repeats the batch with a growing pause."""
    params = {"learner_id": learner_id}
    async with db.advisory_lock(LockKeyspace.LEARNER_PROJECTION, learner_id) as acquired:
        if not acquired:
            return BatchOutcome("busy", 0)
        watermark = await read_watermark(neo4j, str(learner_id))
        after = watermark[0] if watermark is not None else 0
        failure = await db.fetch_one(_FAILURE, params)
        if failure is not None:
            return BatchOutcome("stopped", after, int(failure["seq"]))
        limit = settings.projector_batch_max_events
        events = await db.fetch_all(
            _EVENTS_AFTER, params | {"after": after, "limit": limit}, row=StoredEvent
        )
        if not events:
            # The copy of the watermark may lag after a crash between commit and copy (Т-§2.4):
            # bring it level, so the pass over lagging learners does not find this one again.
            await db.execute(_OFFSET, params | {"seq": after})
            return BatchOutcome("empty", after)
        try:
            loaded, earlier = await load_batch_inputs(db, neo4j, learner_id, events)
            batch = await run_batch(
                learner_id, events, loaded, earlier, settings, Neo4jFolds(neo4j, db, learner_id)
            )
        except EventFailed as failed:
            await db.execute(
                _RECORD_FAILURE, params | {"seq": failed.seq, "error": repr(failed.cause)[:2000]}
            )
            return BatchOutcome("failed", after, failed.seq)
        last = events[-1].seq
        diff = batch.diff()
        await write_batch(neo4j, str(learner_id), diff, last, RULES_VERSION)
        await db.execute(_OFFSET, params | {"seq": last})
        if redis is not None:
            topics = sorted({n.id for n in diff.upsert_nodes if n.label == "Topic"})
            await _publish(redis, learner_id, last, topics)
        return BatchOutcome("applied", last)


async def resume(db: Db, learner_id: UUID) -> None:
    """`ops resume` (WP9.6, S15): the stopped stream of the learner goes on from its failure."""
    await db.execute(_RESUME, {"learner_id": learner_id})
