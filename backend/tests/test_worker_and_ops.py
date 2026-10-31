"""Projector worker loop, ops rebuild / replay-dlq / purge-deleted and the job runner
(Т-§2.4, §2.7, §2.9, spec §4.3–4.4, §7, Д-§6)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.events import append_events
from quack.events.models import NewEvent, StoredEvent
from quack.events.reader import read_learner_events
from quack.graph import projector as projector_module
from quack.graph.ids import CURRENT_RULES_VERSION
from quack.graph.mutations import AdvanceWatermark
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import project_in_memory
from quack.graph.rebuild import rebuild_learner
from quack.graph.worker import ProjectorWorker
from quack.jobs import kinds
from quack.jobs.queue import ClaimedJob, JobSpec, claim_next_job, enqueue_job, fail_or_retry_job
from quack.jobs.runner import JobContext, JobRunner, register_job_handler
from quack.ops.commands import purge_deleted, replay_dlq
from tests.scenario import build_scenario
from tests.support.resources import build_job_context


def _as_new(events: list[StoredEvent]) -> list[NewEvent]:
    return [
        NewEvent(
            type=e.type,
            payload=e.payload,
            actor=e.actor,
            idempotency_key=e.idempotency_key,
            occurred_at=e.occurred_at,
            session_id=e.session_id,
            domain_id=e.domain_id,
        )
        for e in events
    ]


async def _seed(db: SessionFactory) -> tuple[UUID, list[StoredEvent]]:
    log, _ = build_scenario()
    async with db() as session, session.begin():
        stored = await append_events(session, log.learner_id, _as_new(log.events), False)
    return log.learner_id, stored


async def _q(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


def _worker(settings: Settings, pg: AsyncEngine, db: SessionFactory, neo4j: AsyncDriver, redis):
    tuned = settings.model_copy(update={"projector_batch_max_events": 7})
    return ProjectorWorker(tuned, pg, db, neo4j, redis)


async def _drain(worker: ProjectorWorker) -> None:
    for _ in range(50):
        if await worker.run_once() == 0:
            return
    raise AssertionError("projector did not converge")


async def test_worker_projects_log_into_neo4j(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    learner, stored = await _seed(db)
    worker = _worker(settings, pg_engine, db, neo4j, redis)
    assert await worker.pending_learners() == [learner]
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"projector:applied:{learner}")
    await pubsub.get_message(timeout=1.0)
    await _drain(worker)
    assert await worker.pending_learners() == []

    async with db() as session:
        events = await read_learner_events(session, learner)
    expected = await project_in_memory(learner, events, settings.fold, batch_size=100)
    actual = await Neo4jGraphStore(neo4j, str(learner)).dump()
    assert actual.snapshot() == expected.snapshot()
    [offset] = await _q(
        db, "SELECT applied_seq FROM ops.projector_offsets WHERE learner_id = :l", l=learner
    )
    assert offset["applied_seq"] == stored[-1].seq
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
    assert message is not None and "applied_seq" in json.loads(message["data"])
    await pubsub.aclose()


async def test_poison_event_stops_learner_until_replay(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    learner, stored = await _seed(db)
    original = projector_module.apply_event_to_trace

    def poisoned(event, view):
        if event.seq == 5:
            raise RuntimeError("handler bug")
        return original(event, view)

    monkeypatch.setattr(projector_module, "apply_event_to_trace", poisoned)
    worker = _worker(settings, pg_engine, db, neo4j, None)
    await _drain(worker)
    wm = await Neo4jGraphStore(neo4j, str(learner)).load_watermark(str(learner))
    assert wm is not None and wm.applied_seq == 4  # everything before the poison event
    [dlq] = await _q(db, "SELECT * FROM ops.dlq WHERE source = 'projector'")
    assert dlq["learner_id"] == learner and dlq["seq"] == 5
    assert dlq["error_type"] == "RuntimeError" and dlq["kind"] == stored[4].type
    assert await worker.pending_learners() == []  # stream stopped, others continue

    monkeypatch.setattr(projector_module, "apply_event_to_trace", original)
    assert await replay_dlq(db) == 1
    assert await worker.pending_learners() == [learner]
    await _drain(worker)
    wm = await Neo4jGraphStore(neo4j, str(learner)).load_watermark(str(learner))
    assert wm is not None and wm.applied_seq == stored[-1].seq


async def test_rules_mismatch_skips_until_rebuild(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    learner, stored = await _seed(db)
    store = Neo4jGraphStore(neo4j, str(learner))
    await store.apply([AdvanceWatermark(str(learner), 0, "rules@0")])
    worker = _worker(settings, pg_engine, db, neo4j, redis)
    outcome = await worker.process_learner(learner)
    assert outcome is not None and outcome.status == "skipped_rules"

    result = await rebuild_learner(settings, pg_engine, db, neo4j, redis, learner)
    assert result.status == "rebuilt" and result.applied_seq == stored[-1].seq
    async with db() as session:
        events = await read_learner_events(session, learner)
    expected = await project_in_memory(learner, events, settings.fold, batch_size=3)
    assert (await store.dump()).snapshot() == expected.snapshot()
    wm = await store.load_watermark(str(learner))
    assert wm is not None and wm.projector_version == CURRENT_RULES_VERSION
    assert (await worker.process_learner(learner)) is not None


async def test_chunked_rebuild_respects_presence(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    learner, _ = await _seed(db)
    tiny = settings.model_copy(update={"rebuild_max_tx_ops": 10})
    await redis.set(f"presence:learner:{learner}", str(uuid4()), ex=90)
    busy = await rebuild_learner(tiny, pg_engine, db, neo4j, redis, learner)
    assert busy.status == "active_session"
    assert (await Neo4jGraphStore(neo4j, str(learner)).dump()).nodes == {}
    await redis.delete(f"presence:learner:{learner}")
    done = await rebuild_learner(tiny, pg_engine, db, neo4j, redis, learner)
    assert done.status == "rebuilt" and done.operations > 10
    async with db() as session:
        events = await read_learner_events(session, learner)
    expected = await project_in_memory(learner, events, settings.fold, batch_size=100)
    assert (await Neo4jGraphStore(neo4j, str(learner)).dump()).snapshot() == expected.snapshot()


async def test_replay_failed_job(db: SessionFactory) -> None:
    spec = kinds.turn_extract(uuid4(), uuid4(), {"turn": 3})
    async with db() as session, session.begin():
        await enqueue_job(session, JobSpec(**{**spec.__dict__, "max_attempts": 1}))
    async with db() as session, session.begin():
        job = await claim_next_job(session, "w")
        assert job is not None
        await fail_or_retry_job(session, job.id, "w", RuntimeError("x"))
    assert await replay_dlq(db) == 1
    queued = await _q(db, "SELECT kind, payload, lock_key FROM ops.jobs WHERE status = 'queued'")
    assert queued == [{"kind": "turn.extract", "payload": {"turn": 3}, "lock_key": spec.lock_key}]
    [dlq] = await _q(db, "SELECT replayed_at FROM ops.dlq")
    assert dlq["replayed_at"] is not None
    assert await replay_dlq(db) == 0


async def test_purge_deleted_removes_all_learner_data(
    pg_engine: AsyncEngine, db: SessionFactory, neo4j: AsyncDriver, settings: Settings
) -> None:
    learner, _ = await _seed(db)
    other, _ = await _seed(db)
    worker = ProjectorWorker(settings, pg_engine, db, neo4j, None)
    await _drain(worker)
    async with db() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO ops.deletion_tombstones (learner_id, requested_at, status) "
                "VALUES (:l, now(), 'pending_purge')"
            ),
            {"l": learner},
        )
    purged = await purge_deleted(pg_engine, db, neo4j, chunk_size=20)
    assert purged == [learner]
    for table in ("core.events", "core.learner_seq", "core.sessions", "core.domains"):
        assert await _q(db, f"SELECT 1 FROM {table} WHERE learner_id = :l", l=learner) == []
    assert await _q(db, "SELECT 1 FROM core.events WHERE learner_id = :l", l=other) != []
    assert (await Neo4jGraphStore(neo4j, str(learner)).dump()).nodes == {}
    assert (await Neo4jGraphStore(neo4j, str(other)).dump()).nodes != {}
    [tomb] = await _q(db, "SELECT status, purged_at FROM ops.deletion_tombstones")
    assert tomb["status"] == "purged" and tomb["purged_at"] is not None


async def test_job_runner_executes_and_retries(settings: Settings, db: SessionFactory) -> None:
    seen: list[ClaimedJob] = []

    async def ok(job: ClaimedJob, ctx: JobContext) -> None:
        seen.append(job)

    async def broken(job: ClaimedJob, ctx: JobContext) -> None:
        raise ValueError("bad payload")

    context = build_job_context(settings, db, None, None)
    runner = JobRunner(settings, db, "runner-1", context, handlers={"turn.extract": ok})
    assert await runner.run_one() is False
    async with db() as session, session.begin():
        await enqueue_job(session, kinds.turn_extract(uuid4(), uuid4(), {"n": 1}))
        await enqueue_job(session, kinds.session_checkpoint(uuid4(), uuid4(), {}))
    assert await runner.run_one() is True
    assert [j.payload for j in seen] == [{"n": 1}]
    assert await runner.run_one() is False  # no handler for session.checkpoint
    statuses = {r["kind"]: r["status"] for r in await _q(db, "SELECT kind, status FROM ops.jobs")}
    assert statuses == {"turn.extract": "done", "session.checkpoint": "queued"}

    failing = JobRunner(settings, db, "runner-2", context, handlers={"session.checkpoint": broken})
    assert await failing.run_one() is True
    [row] = await _q(
        db, "SELECT status, attempt, last_error FROM ops.jobs WHERE kind = 'session.checkpoint'"
    )
    assert row["status"] == "queued" and row["attempt"] == 1 and "bad payload" in row["last_error"]


def test_register_job_handler() -> None:
    registry: dict = {}

    @register_job_handler("test.kind", registry=registry)
    async def handler(job: ClaimedJob, ctx: JobContext) -> None:
        return None

    assert registry == {"test.kind": handler}
    with pytest.raises(ValueError):
        register_job_handler("test.kind", registry=registry)(handler)
