"""Projector worker loop (Т-§2.4, spec §4.3–4.4): per-learner batches under the keyspace-1
advisory lock, one Neo4j transaction per batch, `projector:applied:{learner_id}` after commit,
poison events into ops.dlq (source = 'projector')."""

from __future__ import annotations

import asyncio
import contextlib
import traceback
from dataclasses import dataclass
from uuid import UUID, uuid4

import orjson
from neo4j import AsyncDriver
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.locks import LockKeyspace, advisory_lock
from quack.db.postgres import SessionFactory
from quack.db.redis import PROJECTOR_WAKEUP_CHANNEL, projector_applied_channel
from quack.events.models import StoredEvent
from quack.events.reader import read_learner_events
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import ProjectorBatchOutcome, rules_mismatch, run_projector_batch
from quack.logging import get_logger, log_context

log = get_logger(__name__)

# Neo4j errors that are retried with the next loop instead of stopping the learner.
TRANSIENT_ERRORS = (TransientError, ServiceUnavailable, SessionExpired)


async def record_offset(session_factory: SessionFactory, learner_id: UUID, applied: int) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO ops.projector_offsets (learner_id, applied_seq, updated_at) "
                "VALUES (:l, :s, now()) ON CONFLICT (learner_id) DO UPDATE "
                "SET applied_seq = EXCLUDED.applied_seq, updated_at = now()"
            ),
            {"l": learner_id, "s": applied},
        )


async def publish_applied(redis: Redis | None, learner_id: UUID, applied: int) -> None:
    if redis is None:
        return
    try:
        await redis.publish(
            projector_applied_channel(learner_id), orjson.dumps({"applied_seq": applied}).decode()
        )
    except Exception:
        log.warning("projector_applied_publish_failed", learner_id=str(learner_id))


@dataclass
class ProjectorWorker:
    settings: Settings
    engine: AsyncEngine
    session_factory: SessionFactory
    driver: AsyncDriver
    redis: Redis | None

    async def pending_learners(self, limit: int = 100) -> list[UUID]:
        """Learners whose last_seq is ahead of ops.projector_offsets and not stopped in DLQ."""
        async with self.session_factory() as session:
            rows = await session.execute(
                text(
                    "SELECT s.learner_id FROM core.learner_seq s "
                    "LEFT JOIN ops.projector_offsets o ON o.learner_id = s.learner_id "
                    "WHERE s.last_seq > COALESCE(o.applied_seq, 0) AND NOT EXISTS ("
                    "SELECT 1 FROM ops.dlq d WHERE d.source = 'projector' "
                    "AND d.learner_id = s.learner_id AND d.replayed_at IS NULL) "
                    "ORDER BY s.updated_at, s.learner_id LIMIT :limit"
                ),
                {"limit": limit},
            )
            return [row[0] for row in rows]

    async def _poison(self, learner_id: UUID, event: StoredEvent, exc: Exception) -> None:
        log.error("projector_poison_event", seq=event.seq, type=event.type, error=repr(exc))
        async with self.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO ops.dlq (id, source, kind, learner_id, seq, job_id, payload, "
                    "error_type, error_message, stacktrace, created_at) VALUES (:id, 'projector', "
                    ":kind, :l, :seq, NULL, CAST(:payload AS JSONB), :etype, :emsg, :stack, now())"
                ),
                {
                    "id": uuid4(),
                    "kind": event.type,
                    "l": learner_id,
                    "seq": event.seq,
                    "payload": orjson.dumps(event.payload).decode(),
                    "etype": type(exc).__name__,
                    "emsg": str(exc),
                    "stack": "".join(traceback.format_exception(exc)),
                },
            )

    async def _apply(
        self, learner_id: UUID, store: Neo4jGraphStore, events: list[StoredEvent]
    ) -> ProjectorBatchOutcome:
        try:
            return await run_projector_batch(learner_id, events, store, self.settings.fold)
        except TRANSIENT_ERRORS:
            raise
        except Exception as batch_exc:
            if len(events) == 1:
                await self._poison(learner_id, events[0], batch_exc)
                return ProjectorBatchOutcome("empty", events[0].seq - 1, 0)
        # Find the exact poison event: everything before it is applied (no skipping).
        outcome = ProjectorBatchOutcome("empty", events[0].seq - 1, 0)
        for event in events:
            try:
                outcome = await run_projector_batch(learner_id, [event], store, self.settings.fold)
            except TRANSIENT_ERRORS:
                raise
            except Exception as exc:
                await self._poison(learner_id, event, exc)
                break
        return outcome

    async def process_learner(self, learner_id: UUID) -> ProjectorBatchOutcome | None:
        """Apply one batch; None if the learner lock is held elsewhere."""
        async with advisory_lock(
            self.engine, LockKeyspace.LEARNER_PROJECTION, learner_id
        ) as acquired:
            if not acquired:
                return None
            store = Neo4jGraphStore(self.driver, str(learner_id))
            watermark = await store.load_watermark(str(learner_id))
            applied = watermark.applied_seq if watermark else 0
            if watermark is not None and rules_mismatch(watermark.projector_version):
                log.warning(
                    "projector_rules_mismatch_skip",
                    learner_id=str(learner_id),
                    projector_version=watermark.projector_version,
                )
                return ProjectorBatchOutcome("skipped_rules", applied, 0)
            async with self.session_factory() as session:
                events = await read_learner_events(
                    session,
                    learner_id,
                    after_seq=applied,
                    limit=self.settings.projector_batch_max_events,
                )
            if not events:
                await record_offset(self.session_factory, learner_id, applied)
                return ProjectorBatchOutcome("empty", applied, 0)
            with log_context(learner_id=learner_id, seq=events[-1].seq):
                outcome = await self._apply(learner_id, store, events)
            if outcome.applied_seq > applied:
                await record_offset(self.session_factory, learner_id, outcome.applied_seq)
                await publish_applied(self.redis, learner_id, outcome.applied_seq)
            return outcome

    async def run_once(self) -> int:
        """Process every pending learner once; returns the number of applied batches."""
        applied = 0
        for learner_id in await self.pending_learners():
            try:
                outcome = await self.process_learner(learner_id)
            except TRANSIENT_ERRORS as exc:
                log.warning(
                    "projector_transient_error", learner_id=str(learner_id), error=repr(exc)
                )
                continue
            except Exception as exc:
                log.error("projector_learner_stopped", learner_id=str(learner_id), error=repr(exc))
                continue
            if outcome is not None and outcome.status == "applied":
                applied += 1
        return applied

    async def run_forever(self, stop: asyncio.Event) -> None:
        poll = self.settings.consumer_poll_ms / 1000
        pubsub = None
        if self.redis is not None:
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(PROJECTOR_WAKEUP_CHANNEL)
            except Exception:
                log.warning("projector_wakeup_subscribe_failed")
                pubsub = None
        try:
            while not stop.is_set():
                if await self.run_once():
                    continue
                if pubsub is not None:
                    try:
                        await pubsub.get_message(ignore_subscribe_messages=True, timeout=poll)
                        continue
                    except Exception:
                        log.warning("projector_wakeup_lost")
                        pubsub = None
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=poll)
        finally:
            if pubsub is not None:
                await pubsub.aclose()
