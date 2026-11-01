"""Log dispatcher: derived jobs are enqueued by one consumer of the event log (ТЗ Фазы 3 WP11,
Т-§2.10, Т-§22.4).

One transaction per batch of a learner: read the events after `last_dispatched_seq`, apply the
rules (`dispatcher_rules.rules_for`), enqueue the jobs, move the offset, commit. The offset and
the jobs commit together or not at all. The dispatcher reads only relational tables: it never
reads the graph, never calls a model and never writes to Neo4j (law 3).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import PROJECTOR_WAKEUP_CHANNEL
from quack.events.catalog import EVENT_TYPE_VERSIONS
from quack.events.models import StoredEvent
from quack.events.reader import read_learner_events
from quack.jobs.dispatcher_rules import EventFacts, needs_facts, rules_for
from quack.jobs.queue import JobSpec, enqueue_job
from quack.logging import get_logger, log_context

log = get_logger(__name__)


async def _student_seq(session: AsyncSession, event: StoredEvent) -> int | None:
    row = (
        await session.execute(
            text(
                "SELECT seq FROM core.events WHERE learner_id = :l AND session_id = :s "
                "AND turn_id = :t AND type = 'turn.student_replied' ORDER BY seq DESC LIMIT 1"
            ),
            {"l": event.learner_id, "s": event.session_id, "t": event.turn_id},
        )
    ).first()
    return None if row is None else int(row[0])


async def _session_row(session: AsyncSession, learner_id: UUID, session_id: UUID) -> Any:
    return (
        await session.execute(
            text(
                "SELECT topic_id, last_checkpoint_turn FROM core.sessions "
                "WHERE learner_id = :l AND id = :s"
            ),
            {"l": learner_id, "s": session_id},
        )
    ).first()


async def load_facts(
    session: AsyncSession, event: StoredEvent, batch: dict[tuple[UUID | None, str], int]
) -> EventFacts:
    """Relational data a rule needs beyond the event (`DispatcherRule.needs`)."""
    if not needs_facts(event):
        return EventFacts()
    session_id = UUID(str(event.payload["session_id"]))
    row = await _session_row(session, event.learner_id, session_id)
    facts = EventFacts(
        session_found=row is not None,
        topic_id=None if row is None else row.topic_id,
        last_checkpoint_turn=None if row is None else int(row.last_checkpoint_turn),
    )
    if event.type == "turn.teacher_replied":
        student = batch.get((event.turn_id, "turn.student_replied"))
        if student is None and event.turn_id is not None:
            student = await _student_seq(session, event)
        facts = EventFacts(
            session_found=facts.session_found,
            topic_id=facts.topic_id,
            last_checkpoint_turn=facts.last_checkpoint_turn,
            student_seq=student,
        )
    return facts


async def _already_enqueued(session: AsyncSession, spec: JobSpec) -> bool:
    """WP11.3: a replayed trigger (offset reset or rebuilt) finds its job by the trigger seq.

    `required_seq` is the seq of the trigger event, so (kind, lock_key, required_seq) names one
    trigger whatever the status of its job. Jobs without `required_seq` rely on the
    `coalesce_key` of `enqueue_job` (С-§7.1).
    """
    if spec.required_seq is None:
        return False
    row = (
        await session.execute(
            text(
                "SELECT 1 FROM ops.jobs WHERE learner_id = :l AND kind = :k "
                "AND lock_key = :lk AND required_seq = :rs LIMIT 1"
            ),
            {
                "l": spec.learner_id,
                "k": spec.kind,
                "lk": spec.lock_key,
                "rs": spec.required_seq,
            },
        )
    ).first()
    return row is not None


@dataclass(frozen=True)
class DispatchOutcome:
    learner_id: UUID
    dispatched_seq: int
    events: int
    jobs: int


@dataclass
class Dispatcher:
    settings: Settings
    session_factory: SessionFactory
    redis: Redis | None = None

    async def pending_learners(self, limit: int = 100) -> list[UUID]:
        """Learners whose `last_seq` is ahead of their dispatcher offset (no row = 0)."""
        async with self.session_factory() as session:
            rows = await session.execute(
                text(
                    "SELECT s.learner_id FROM core.learner_seq s "
                    "LEFT JOIN ops.dispatcher_offsets d ON d.learner_id = s.learner_id "
                    "WHERE s.last_seq > COALESCE(d.last_dispatched_seq, 0) "
                    "ORDER BY s.updated_at, s.learner_id LIMIT :limit"
                ),
                {"limit": limit},
            )
            return [row[0] for row in rows]

    async def _dispatch_batch(self, learner_id: UUID) -> DispatchOutcome | None:
        """One transaction: events → rules → jobs → offset. None if another dispatcher holds
        the learner's offset row."""
        async with self.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO ops.dispatcher_offsets (learner_id, last_dispatched_seq, "
                    "updated_at) VALUES (:l, 0, now()) ON CONFLICT (learner_id) DO NOTHING"
                ),
                {"l": learner_id},
            )
            offset = (
                await session.execute(
                    text(
                        "SELECT last_dispatched_seq FROM ops.dispatcher_offsets "
                        "WHERE learner_id = :l FOR UPDATE SKIP LOCKED"
                    ),
                    {"l": learner_id},
                )
            ).scalar_one_or_none()
            if offset is None:
                return None
            events = await read_learner_events(
                session,
                learner_id,
                after_seq=int(offset),
                limit=self.settings.dispatcher_batch_size,
            )
            if not events:
                return DispatchOutcome(learner_id, int(offset), 0, 0)
            jobs = 0
            batch: dict[tuple[UUID | None, str], int] = {}
            for event in events:
                batch[(event.turn_id, event.type)] = event.seq
                if (event.type, event.schema_version) not in EVENT_TYPE_VERSIONS:
                    log.warning(
                        "dispatcher_unknown_event",
                        learner_id=str(learner_id),
                        seq=event.seq,
                        type=event.type,
                        schema_version=event.schema_version,
                    )
                    continue
                with log_context(learner_id=learner_id, seq=event.seq):
                    facts = await load_facts(session, event, batch)
                    specs = rules_for(
                        event, facts, checkpoint_every_turns=self.settings.checkpoint_every_turns
                    )
                    for spec in specs:
                        if await _already_enqueued(session, spec):
                            continue
                        await enqueue_job(session, spec)
                        jobs += 1
            dispatched = events[-1].seq
            await session.execute(
                text(
                    "UPDATE ops.dispatcher_offsets SET last_dispatched_seq = :s, "
                    "updated_at = now() WHERE learner_id = :l"
                ),
                {"l": learner_id, "s": dispatched},
            )
            return DispatchOutcome(learner_id, dispatched, len(events), jobs)

    async def process_learner(self, learner_id: UUID) -> int:
        """Dispatch the learner's events in order until caught up; returns the event count."""
        events = 0
        while True:
            outcome = await self._dispatch_batch(learner_id)
            if outcome is None:
                return events
            events += outcome.events
            if outcome.events < self.settings.dispatcher_batch_size:
                return events

    async def run_once(self) -> int:
        """One pass over the lagging learners, up to `dispatcher_concurrency` in parallel; a
        failure of one learner is logged and does not stop the others (T-D5). Returns the
        number of events dispatched."""
        learners = await self.pending_learners()
        if not learners:
            return 0
        limit = asyncio.Semaphore(max(1, self.settings.dispatcher_concurrency))

        async def one(learner_id: UUID) -> int:
            async with limit:
                try:
                    return await self.process_learner(learner_id)
                except Exception as exc:
                    log.error(
                        "dispatcher_learner_failed", learner_id=str(learner_id), error=repr(exc)
                    )
                    return 0

        return sum(await asyncio.gather(*(one(learner) for learner in learners)))

    async def lag(self) -> tuple[int, int]:
        """WP11.5: (learners lagging, max events behind) for `ops status`."""
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT COUNT(*), COALESCE(MAX(s.last_seq - "
                        "COALESCE(d.last_dispatched_seq, 0)), 0) FROM core.learner_seq s "
                        "LEFT JOIN ops.dispatcher_offsets d ON d.learner_id = s.learner_id "
                        "WHERE s.last_seq > COALESCE(d.last_dispatched_seq, 0)"
                    )
                )
            ).one()
            return int(row[0]), int(row[1])

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Every `consumer_poll_ms` and on the projector wakeup of new events (С-§6)."""
        poll = self.settings.consumer_poll_ms / 1000
        pubsub = None
        if self.redis is not None:
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(PROJECTOR_WAKEUP_CHANNEL)
            except Exception:
                log.warning("dispatcher_wakeup_subscribe_failed")
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
                        log.warning("dispatcher_wakeup_lost")
                        pubsub = None
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=poll)
        finally:
            if pubsub is not None:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()


async def supervise(
    name: str,
    task: Callable[[asyncio.Event], Awaitable[None]],
    stop: asyncio.Event,
    *,
    restart_pause_s: float,
) -> None:
    """WP11.1: run `task(stop)` until `stop`; an exception is logged and the task restarts after
    `restart_pause_s`, so a crash of the dispatcher does not stop the projector and back."""
    while not stop.is_set():
        try:
            await task(stop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("supervised_task_crashed", task=name, error=repr(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=restart_pause_s)
        else:
            return
