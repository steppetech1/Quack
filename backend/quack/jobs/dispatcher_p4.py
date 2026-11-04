"""Dispatcher of Phase 4 on `Db` (ТЗ Ф4 WP7.1–7.3; `Т-§2.10`, `Т-§7.5`, `Т-§13.1`–`Т-§13.3`).

The one consumer of the event log besides the projector: per learner, in `seq` order, from its
mark `ops.dispatcher_offsets`. One transaction per batch: read the events → close the episodes
→ apply the rows of the table (`rules_p4`) → put the jobs (`queue_p4.enqueue`) → move the mark
and its `counters`. A crash leaves nothing; a replay of the same events finds the same jobs by
their deterministic `job_key` and the same `episode.closed` by its key (law 5). The dispatcher
reads relational tables only (`Т-§13.3`: the journal, not the projection), calls no model and
never writes to Neo4j (law 3).

`counters` (D-A-160): `{"episodes": {<session_id>: open episode}, "new_topics": {<domain_id>: n}}`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.redis import PROJECTOR_WAKEUP_CHANNEL
from quack.events.keys import episode_key
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.events.registry import SPECS
from quack.jobs import facts_p4
from quack.jobs.coverage import mark_covered
from quack.jobs.episodes import ClosedEpisode, EpisodeTracker, episode_has_content
from quack.jobs.queue_p4 import enqueue, request_cancel
from quack.jobs.rules_p4 import (
    JobPlan,
    RuleFacts,
    cancelled_requests,
    plan_with_facts,
)
from quack.logging import get_logger, log_context

log = get_logger(__name__)

_EVENT_COLUMNS = (
    "learner_id, seq, id, type, schema_version, actor, domain_id, session_id, causation_seq, "
    "correlation_id, idempotency_key, payload_hash, occurred_at, payload, source_refs"
)
_READ_EVENTS = (
    f"SELECT {_EVENT_COLUMNS} FROM core.events WHERE learner_id = %(learner_id)s "
    "AND seq > %(after)s ORDER BY seq LIMIT %(limit)s"
)
# The events that can make an episode "with content" (episodes.episode_has_content).
_CONTENT_TYPES: tuple[str, ...] = (
    "message.sent",
    "canvas.line_committed",
    "canvas.line_erased",
    "voice.transcribed",
    "item.flagged_unclear",
    "element.responded",
    "answer.submitted",
    "verdict.recorded",
)
_READ_EPISODE = (
    f"SELECT {_EVENT_COLUMNS} FROM core.events WHERE learner_id = %(learner_id)s "
    "AND session_id = %(session_id)s AND seq BETWEEN %(from_seq)s AND %(to_seq)s "
    "AND type = ANY(%(types)s) ORDER BY seq"
)
_LOCK_OFFSET = (
    "SELECT last_dispatched_seq, counters FROM ops.dispatcher_offsets "
    "WHERE learner_id = %(learner_id)s FOR UPDATE SKIP LOCKED"
)
_MOVE_OFFSET = (
    "UPDATE ops.dispatcher_offsets SET last_dispatched_seq = %(seq)s, counters = %(counters)s, "
    "updated_at = now() WHERE learner_id = %(learner_id)s"
)


@dataclass(frozen=True)
class DispatchOutcome:
    learner_id: UUID
    dispatched_seq: int
    events: int
    jobs: int
    episodes: int = 0


@dataclass
class _Counters:
    """The `counters` column: the open episodes and the counters of the rules "every N"."""

    episodes: dict[str, Any] = field(default_factory=dict)
    new_topics: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, raw: dict[str, Any]) -> _Counters:
        return cls(dict(raw.get("episodes", {})), dict(raw.get("new_topics", {})))

    def dump(self) -> dict[str, Any]:
        return {"episodes": self.episodes, "new_topics": self.new_topics}


def _now() -> datetime:
    return datetime.now(UTC)


# The dispatcher walks every learner (Т-§13.3); these three statements are its cross-learner
# scans (D-A-164).
_PENDING_LEARNERS = (
    "SELECT l.learner_id FROM core.learners l LEFT JOIN ops.dispatcher_offsets d "
    "ON d.learner_id = l.learner_id WHERE l.seq > COALESCE(d.last_dispatched_seq, 0) "
    "ORDER BY l.learner_id LIMIT %(limit)s"
)
_LAG = (
    "SELECT count(*) AS n, COALESCE(max(l.seq - COALESCE(d.last_dispatched_seq, 0)), 0) "
    "AS behind FROM core.learners l LEFT JOIN ops.dispatcher_offsets d "
    "ON d.learner_id = l.learner_id WHERE l.seq > COALESCE(d.last_dispatched_seq, 0)"
)
_IDLE_LEARNERS = (
    "SELECT DISTINCT d.learner_id FROM ops.dispatcher_offsets d, "
    "jsonb_each(COALESCE(d.counters->'episodes', '{}'::jsonb)) e "
    "WHERE (e.value->>'last_at')::timestamptz <= %(cutoff)s"
)


@dataclass
class DispatcherP4:
    settings: Settings
    db: Db
    redis: Redis | None = None
    clock: Callable[[], datetime] = _now

    # --- the pass over the log --------------------------------------------------------------

    async def pending_learners(self, limit: int = 100) -> list[UUID]:
        """Learners whose event counter is ahead of their mark (no mark row = 0)."""
        rows = await self.db.fetch_all(_PENDING_LEARNERS, {"limit": limit})
        return [r["learner_id"] for r in rows]

    async def lag(self) -> tuple[int, int]:
        """(learners behind, the largest number of events behind) — for `ops status`."""
        row = await self.db.fetch_one(_LAG)
        assert row is not None
        return int(row["n"]), int(row["behind"])

    async def _lock_mark(self, learner_id: UUID) -> tuple[int, _Counters] | None:
        await self.db.execute(
            "INSERT INTO ops.dispatcher_offsets (learner_id) VALUES (%(learner_id)s) "
            "ON CONFLICT (learner_id) DO NOTHING",
            {"learner_id": learner_id},
        )
        row = await self.db.fetch_one(_LOCK_OFFSET, {"learner_id": learner_id})
        if row is None:
            return None  # another dispatcher holds this learner
        return int(row["last_dispatched_seq"]), _Counters.load(row["counters"])

    async def dispatch_batch(self, learner_id: UUID) -> DispatchOutcome | None:
        """One transaction for the next batch of the learner; None if another dispatcher holds
        its mark."""
        async with self.db.transaction():
            locked = await self._lock_mark(learner_id)
            if locked is None:
                return None
            after, counters = locked
            events = await self.db.fetch_all(
                _READ_EVENTS,
                {
                    "learner_id": learner_id,
                    "after": after,
                    "limit": self.settings.dispatcher_batch_size,
                },
                row=StoredEvent,
            )
            if not events:
                return DispatchOutcome(learner_id, after, 0, 0)
            tracker = EpisodeTracker(self.settings, counters.episodes)
            jobs = episodes = 0
            for event in events:
                if (event.type, event.schema_version) not in SPECS:
                    log.warning(
                        "dispatcher_unknown_event",
                        learner_id=str(learner_id),
                        seq=event.seq,
                        type=event.type,
                        schema_version=event.schema_version,
                    )
                    continue
                with log_context(learner_id=learner_id, seq=event.seq):
                    for closed in tracker.feed(event):
                        await self._write_episode_closed(learner_id, closed)
                        episodes += 1
                    jobs += await self._apply_rules(event, counters)
            counters.episodes = tracker.dump()
            last = events[-1].seq
            await self.db.execute(
                _MOVE_OFFSET,
                {"learner_id": learner_id, "seq": last, "counters": Jsonb(counters.dump())},
            )
            return DispatchOutcome(learner_id, last, len(events), jobs, episodes)

    async def process_learner(self, learner_id: UUID) -> int:
        """Dispatch the learner's events until caught up; returns the number of events."""
        total = 0
        while True:
            outcome = await self.dispatch_batch(learner_id)
            if outcome is None:
                return total
            total += outcome.events
            if outcome.events < self.settings.dispatcher_batch_size:
                return total

    # --- episodes ---------------------------------------------------------------------------

    async def _write_episode_closed(self, learner_id: UUID, closed: ClosedEpisode) -> StoredEvent:
        """`episode.closed {from_seq, to_seq, has_content}` through `append_events` in the
        transaction of the mark (WP7.2); key `episode:{session_id}:{to_seq}`."""
        content = await self.db.fetch_all(
            _READ_EPISODE,
            {
                "learner_id": learner_id,
                "session_id": closed.session_id,
                "from_seq": closed.from_seq,
                "to_seq": closed.to_seq,
                "types": list(_CONTENT_TYPES),
            },
            row=StoredEvent,
        )
        (stored,) = await append_events(
            self.db,
            learner_id,
            [
                NewEvent(
                    type="episode.closed",
                    payload={
                        "from_seq": closed.from_seq,
                        "to_seq": closed.to_seq,
                        "has_content": episode_has_content(content),
                    },
                    actor="system",
                    idempotency_key=episode_key(closed.session_id, closed.to_seq),
                    session_id=closed.session_id,
                    domain_id=closed.domain_id,
                    causation_seq=closed.to_seq,
                    occurred_at=closed.closed_at,
                )
            ],
            redis=self.redis,
        )
        log.info(
            "episode_closed",
            learner_id=str(learner_id),
            session_id=str(closed.session_id),
            reason=closed.reason,
            seq=stored.seq,
        )
        return stored

    async def close_idle_episodes(self) -> int:
        """`dispatcher.episode_idle` (WP7.5, `Т-§7.5`): the periodic check — an episode of a
        session with no event for `episode_idle_min` minutes (by the clock) is closed. A learner
        with events the dispatcher has not read yet is left alone: those events say more."""
        now = self.clock()
        cutoff = now - timedelta(minutes=self.settings.episode_idle_min)
        rows = await self.db.fetch_all(_IDLE_LEARNERS, {"cutoff": cutoff})
        closed_total = 0
        for row in rows:
            try:
                closed_total += await self._close_idle_of(row["learner_id"], now)
            except Exception as exc:  # law 7: one learner's failure does not stop the others
                log.error(
                    "dispatcher_idle_learner_failed",
                    learner_id=str(row["learner_id"]),
                    error=repr(exc),
                )
        return closed_total

    async def _close_idle_of(self, learner_id: UUID, now: datetime) -> int:
        async with self.db.transaction():
            locked = await self._lock_mark(learner_id)
            if locked is None:
                return 0
            seq, counters = locked
            head = await self.db.fetch_one(
                "SELECT seq FROM core.learners WHERE learner_id = %(learner_id)s",
                {"learner_id": learner_id},
            )
            if head is None or int(head["seq"]) != seq:
                return 0
            tracker = EpisodeTracker(self.settings, counters.episodes)
            closed = tracker.idle(now)
            for episode in closed:
                await self._write_episode_closed(learner_id, episode)
            if closed:
                counters.episodes = tracker.dump()
                await self.db.execute(
                    _MOVE_OFFSET,
                    {"learner_id": learner_id, "seq": seq, "counters": Jsonb(counters.dump())},
                )
            return len(closed)

    # --- rules ------------------------------------------------------------------------------

    async def _facts(self, event: StoredEvent, counters: _Counters) -> RuleFacts:
        """Data beyond the event for the rows that read it (`facts_p4`)."""
        learner_id, seq = event.learner_id, event.seq
        if event.type == "skill.split":
            skill_id = UUID(str(event.payload["skill_id"]))
            episodes = await facts_p4.skill_split_episodes(self.db, learner_id, skill_id, seq)
            return RuleFacts(episode_seqs=episodes)
        if event.type == "session.ended":
            sid = UUID(str(event.payload["session_id"]))
            return RuleFacts(
                topic_ids=await facts_p4.session_topic_ids(self.db, learner_id, sid, seq),
                domain_id=await facts_p4.session_domain_id(self.db, learner_id, sid),
                temporary_entities=await facts_p4.session_has_temporary_entities(
                    self.db, learner_id, sid, seq
                ),
            )
        if event.type == "task.closed":
            task_id = UUID(str(event.payload["task_id"]))
            return RuleFacts(
                topic_ids=await facts_p4.hinted_task_topic_ids(self.db, learner_id, task_id, seq)
            )
        if event.type == "topic.created":
            domain = str(event.payload["domain_id"])
            count = counters.new_topics.get(domain, 0) + 1
            due = count >= self.settings.graph_hygiene_every_topics
            counters.new_topics[domain] = 0 if due else count
            return RuleFacts(hygiene_due=due)
        return RuleFacts()

    async def _apply_rules(self, event: StoredEvent, counters: _Counters) -> int:
        facts = await self._facts(event, counters)
        plans: list[JobPlan] = plan_with_facts(event, self.settings, facts)
        if event.type == "turn.committed":
            requests = cancelled_requests(event)
            if requests:
                await request_cancel(self.db, event.learner_id, requests, redis=self.redis)
        if event.type == "episode.closed" and not event.payload["has_content"]:
            # WP7.2, `Т-§7.5`: nothing to interpret — the dispatcher covers the episode itself.
            extractor = self.settings.llm_roles["extractor"].prompt
            assert extractor is not None
            await mark_covered(
                self.db,
                event.learner_id,
                episode_seq=event.seq,
                extractor=extractor,
                status="nothing_to_extract",
                observation_seq=None,
            )
        put = 0
        for plan in plans:
            if await enqueue(self.db, plan, redis=self.redis) != "duplicate":
                put += 1
        return put

    # --- loops ------------------------------------------------------------------------------

    async def run_once(self) -> int:
        """Idle episodes, then one pass over the lagging learners, up to
        `dispatcher_concurrency` at a time; a failure of one learner is logged and does not stop
        the others. Returns the number of events dispatched."""
        try:
            await self.close_idle_episodes()
        except Exception as exc:
            log.error("dispatcher_idle_failed", error=repr(exc))
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

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Every `consumer_poll_ms` and on the wakeup of new events (`Т-§2.10`)."""
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
