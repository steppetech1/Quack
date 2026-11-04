"""Log dispatcher (ТЗ Фазы 3 §8.3a, WP11) on the `Db` and the rules of Phase 4 (D-A-230, D-A-232):
the transaction of the mark (T-D2), the order and the isolation of the learners (T-D5), a crash of
the dispatcher beside the projector (T-D6), an unknown schema version (T-D7), the lag (WP11.5).
The rows of the table of `Т-§13.3` in action are in `tests/jobs/test_dispatcher_p4.py`; the rule
`session.digest_created → artifact.refresh_cheatsheet` is gone with the kind (`REMOVED_JOB_KINDS`):
the digest puts `analyst.deep` only, and the cheat sheets follow `session.ended`
(`artifact.topic_refresh`)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from neo4j import AsyncDriver
from structlog.testing import capture_logs

from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.graph.projection.process import ProjectorP4
from quack.jobs import dispatcher_p4
from quack.jobs.dispatcher_p4 import DispatcherP4
from quack.jobs.queue_p4 import EnqueueResult
from quack.jobs.rules_p4 import JobPlan
from quack.runtime.phase4 import supervise
from tests.db.fixtures import create_learner
from tests.events.samples import sample_payload

# `session.started` → `message.sent` → `task.closed` closes the episode (the dispatcher writes
# `episode.closed`), puts `analyst.run`, and the next pass puts `extract.episode` (T-D1).
STORY_JOBS = ["analyst.run", "extract.episode"]


@dataclass
class Story:
    learner: UUID
    session: UUID
    started_seq: int
    closed_seq: int


async def _put(
    db: Db, learner: UUID, event_type: str, session: UUID, domain: UUID, **payload: Any
) -> StoredEvent:
    data = {**sample_payload(event_type), **payload}
    if event_type.startswith("session."):
        data["session_id"] = str(session)
    if event_type == "session.started":
        data["domain_id"] = str(domain)
    (stored,) = await append_events(
        db,
        learner,
        [
            NewEvent(
                type=event_type,
                payload=data,
                actor="code",
                idempotency_key=str(uuid4()),
                session_id=session,
                domain_id=domain,
            )
        ],
    )
    return stored


async def _story(db: Db, learner: UUID | None = None) -> Story:
    learner = learner or await create_learner(db)
    session, domain = uuid4(), uuid4()
    started = await _put(db, learner, "session.started", session, domain)
    await _put(db, learner, "message.sent", session, domain)
    closed = await _put(db, learner, "task.closed", session, domain)
    return Story(learner, session, started.seq, closed.seq)


async def _kinds(db: Db, learner: UUID) -> list[str]:
    rows = await db.fetch_all(
        "SELECT kind FROM ops.jobs WHERE learner_id = %(l)s ORDER BY kind", {"l": learner}
    )
    return [r["kind"] for r in rows]


async def _offset(db: Db, learner: UUID) -> int | None:
    row = await db.fetch_one(
        "SELECT last_dispatched_seq FROM ops.dispatcher_offsets WHERE learner_id = %(l)s",
        {"l": learner},
    )
    return None if row is None else int(row["last_dispatched_seq"])


async def _last_seq(db: Db, learner: UUID) -> int:
    row = await db.fetch_one(
        "SELECT seq FROM core.learners WHERE learner_id = %(l)s", {"l": learner}
    )
    assert row is not None
    return int(row["seq"])


async def _drain(dispatcher: DispatcherP4, learner: UUID) -> None:
    """Dispatch until the learner is caught up (the episodes the dispatcher closes are events)."""
    while await dispatcher.process_learner(learner):
        pass


# --- T-D2 -------------------------------------------------------------------------------


async def test_dispatcher_offset_and_jobs_commit_together(
    pg_db: Db, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing enqueue rolls the batch back whole: no job, the mark stays; the next pass redoes
    it."""
    story = await _story(pg_db)
    dispatcher = DispatcherP4(settings, pg_db)
    real = dispatcher_p4.enqueue
    calls = 0

    async def flaky(db: Db, plan: JobPlan, **kwargs: Any) -> EnqueueResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("enqueue failed")
        return await real(db, plan, **kwargs)

    monkeypatch.setattr(dispatcher_p4, "enqueue", flaky)
    await dispatcher.run_once()  # the first batch: the episode closes, `analyst.run` is put
    assert calls == 1 and await _kinds(pg_db, story.learner) == ["analyst.run"]
    assert await _offset(pg_db, story.learner) == story.closed_seq
    with capture_logs() as logs:
        await dispatcher.run_once()  # the second batch: `episode.closed` → `extract.episode`
    assert any(entry["event"] == "dispatcher_learner_failed" for entry in logs)
    # The failed batch left neither a job nor a moved mark.
    assert await _kinds(pg_db, story.learner) == ["analyst.run"]
    assert await _offset(pg_db, story.learner) == story.closed_seq

    monkeypatch.setattr(dispatcher_p4, "enqueue", real)
    await dispatcher.run_once()
    assert await _kinds(pg_db, story.learner) == STORY_JOBS
    assert await _offset(pg_db, story.learner) == await _last_seq(pg_db, story.learner)


# --- T-D5 -------------------------------------------------------------------------------


async def test_dispatcher_orders_per_learner_and_isolates_failures(
    pg_db: Db, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = await create_learner(pg_db), await create_learner(pg_db)
    for _ in range(2):  # interleaved: A, B, A, B
        await _story(pg_db, a)
        await _story(pg_db, b)

    real = dispatcher_p4.plan_with_facts

    def broken_for_a(event: StoredEvent, *args: Any, **kwargs: Any) -> list[JobPlan]:
        if event.learner_id == a and event.type == "task.closed":
            raise RuntimeError("rule failed")
        return real(event, *args, **kwargs)

    monkeypatch.setattr(dispatcher_p4, "plan_with_facts", broken_for_a)
    dispatcher = DispatcherP4(settings, pg_db)
    with capture_logs() as logs:
        await dispatcher.run_once()
        await dispatcher.run_once()  # the second pass: the episodes the first one closed
    assert any(entry["event"] == "dispatcher_learner_failed" for entry in logs)
    assert await _kinds(pg_db, a) == []
    assert await _offset(pg_db, a) in (None, 0)
    # B's two sessions: each puts its analyst (one coalesced job per learner) and its episode.
    assert await _kinds(pg_db, b) == ["analyst.run", "extract.episode", "extract.episode"]
    assert await _offset(pg_db, b) == await _last_seq(pg_db, b)


# --- T-D6 -------------------------------------------------------------------------------


async def test_dispatcher_crash_does_not_stop_projector(
    pg_db: Db, settings: Settings, neo4j: AsyncDriver
) -> None:
    learner = await create_learner(pg_db)
    domain = uuid4()
    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="domain.created",
                payload={"domain_id": str(domain), "name": "Algebra"},
                actor="code",
                idempotency_key="d",
                domain_id=domain,
            ),
            NewEvent(
                type="material.uploaded",
                payload={"material_id": str(uuid4()), "object_key": "k", "mime": "application/pdf"},
                actor="code",
                idempotency_key="m",
                domain_id=domain,
            ),
        ],
    )
    fast = settings.model_copy(update={"dispatcher_restart_pause_s": 0.05, "consumer_poll_ms": 20})
    dispatcher = DispatcherP4(fast, pg_db)
    projector = ProjectorP4(fast, pg_db, neo4j)
    starts = 0

    async def crashing(stop: asyncio.Event) -> None:
        nonlocal starts
        starts += 1
        if starts == 1:
            raise RuntimeError("dispatcher crashed")
        await dispatcher.run_forever(stop)

    stop = asyncio.Event()

    async def projected() -> int | None:
        row = await pg_db.fetch_one(
            "SELECT seq FROM ops.projector_offsets WHERE learner_id = %(l)s", {"l": learner}
        )
        return None if row is None else int(row["seq"])

    async def until_done() -> None:
        last = await _last_seq(pg_db, learner)
        for _ in range(200):
            if await projected() == last and await _offset(pg_db, learner) == last:
                break
            await asyncio.sleep(0.05)
        stop.set()

    with capture_logs() as logs:
        await asyncio.wait_for(
            asyncio.gather(
                supervise("dispatcher", crashing, stop, restart_pause_s=0.05),
                supervise("projector", projector.run_forever, stop, restart_pause_s=0.05),
                until_done(),
            ),
            timeout=15,
        )
    crashes = [e for e in logs if e["event"] == "supervised_task_crashed"]
    assert [e["task"] for e in crashes] == ["dispatcher"]
    assert starts == 2
    assert await projected() == await _last_seq(pg_db, learner)
    assert await _kinds(pg_db, learner) == ["material.parse"]


# --- T-D7 -------------------------------------------------------------------------------


async def test_dispatcher_skips_unknown_event(pg_db: Db, settings: Settings) -> None:
    story = await _story(pg_db)
    # The `task.closed` now carries a schema version this code does not know.
    await pg_db.execute(
        "UPDATE core.events SET schema_version = 99 WHERE learner_id = %(l)s AND seq = %(s)s",
        {"l": story.learner, "s": story.closed_seq},
    )
    ended = await _put(pg_db, story.learner, "session.ended", story.session, uuid4())
    with capture_logs() as logs:
        await DispatcherP4(settings, pg_db).run_once()
    unknown = [e for e in logs if e["event"] == "dispatcher_unknown_event"]
    assert [e["seq"] for e in unknown] == [story.closed_seq]
    assert await _offset(pg_db, story.learner) == ended.seq
    assert "analyst.run" in await _kinds(pg_db, story.learner)  # `session.ended` was dispatched
    assert await pg_db.fetch_all("SELECT 1 FROM core.events WHERE type = 'episode.closed'") != []


# --- WP11.5: lag for `ops status` --------------------------------------------------------


async def test_dispatcher_lag(pg_db: Db, settings: Settings) -> None:
    story = await _story(pg_db)
    dispatcher = DispatcherP4(settings, pg_db)
    lagging, max_lag = await dispatcher.lag()
    assert lagging >= 1 and max_lag >= await _last_seq(pg_db, story.learner)
    await _drain(dispatcher, story.learner)
    assert await dispatcher.lag() == (0, 0)
