"""Phase 3 job queue and workers (ТЗ Фазы 3 §8.3): T-Q1…T-Q4, T-Q8.

T-Q7 and T-Q9 are in `test_jobs_phase3_pending.py` until WP3.3 and WP11.4.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.app import create_app
from quack.api.auth import issue_access_token
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.events import append_events
from quack.events.models import NewEvent, StoredEvent
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.worker import ProjectorWorker
from quack.jobs import kinds
from quack.jobs.kinds import JobDeferred
from quack.jobs.periodic import PeriodicScheduler, handle_purge_deleted
from quack.jobs.queue import ClaimedJob, JobSpec, claim_next_job, enqueue_job
from quack.jobs.runner import JobContext, JobHandler, JobRunner
from quack.llm.errors import LLMBudgetExceededError
from quack.ops.commands import PURGE_TABLES
from tests.scenario import build_scenario
from tests.support.auth import ensure_user
from tests.support.resources import build_app_resources, build_job_context

QUACK_ROOT = Path(__file__).resolve().parents[1] / "quack"


async def _q(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def _exec(db: SessionFactory, sql: str, **params: Any) -> None:
    async with db() as session, session.begin():
        await session.execute(text(sql), params)


async def _enqueue(db: SessionFactory, spec: JobSpec) -> UUID:
    async with db() as session, session.begin():
        return await enqueue_job(session, spec)


async def _claim(db: SessionFactory, worker: str = "w1") -> ClaimedJob | None:
    async with db() as session, session.begin():
        return await claim_next_job(session, worker)


def _runner(settings: Settings, db: SessionFactory, handlers: dict[str, JobHandler]) -> JobRunner:
    return JobRunner(settings, db, "w-test", handlers=handlers)


# --- T-Q1, T-Q2: readiness of the projection --------------------------------------------


async def test_claim_waits_for_projection(db: SessionFactory) -> None:
    learner = uuid4()
    job_id = await _enqueue(db, kinds.graph_curate(learner, uuid4(), {}, required_seq=5))
    assert await _claim(db) is None
    await _exec(
        db, "INSERT INTO ops.projector_offsets (learner_id, applied_seq) VALUES (:l, 4)", l=learner
    )
    assert await _claim(db) is None
    await _exec(
        db, "UPDATE ops.projector_offsets SET applied_seq = 5 WHERE learner_id = :l", l=learner
    )
    got = await _claim(db)
    assert got is not None and got.id == job_id
    # required_seq IS NULL needs no offsets row.
    other = await _enqueue(db, kinds.graph_curate(uuid4(), uuid4(), {}))
    got = await _claim(db, "w2")
    assert got is not None and got.id == other


async def test_coalesce_keeps_max_required_seq(db: SessionFactory) -> None:
    learner, domain = uuid4(), uuid4()
    for seq in (3, 7, 5):
        await _enqueue(db, kinds.graph_curate(learner, domain, {}, required_seq=seq))
    rows = await _q(
        db, "SELECT status, required_seq FROM ops.jobs WHERE learner_id = :l", l=learner
    )
    assert rows == [{"status": "queued", "required_seq": 7}]
    learner2, domain2 = uuid4(), uuid4()  # coalesce_key is curate:{domain}, so a new domain
    await _enqueue(db, kinds.graph_curate(learner2, domain2, {}))
    await _enqueue(db, kinds.graph_curate(learner2, domain2, {}, required_seq=4))
    rows = await _q(db, "SELECT required_seq FROM ops.jobs WHERE learner_id = :l", l=learner2)
    assert rows == [{"required_seq": 4}]


# --- T-Q3, T-Q4: deferral --------------------------------------------------------------


async def test_job_deferred_requeues_without_attempt(
    db: SessionFactory, settings: Settings
) -> None:
    job_id = await _enqueue(db, kinds.session_sweep_inactive())

    async def handler(job: ClaimedJob, ctx: JobContext) -> None:
        raise JobDeferred(600, "x")

    assert await _runner(settings, db, {"session.sweep_inactive": handler}).run_one()
    (row,) = await _q(db, "SELECT * FROM ops.jobs WHERE id = :i", i=job_id)
    assert row["status"] == "queued" and row["attempt"] == 0
    delta = (row["run_after"] - datetime.now(UTC)).total_seconds()
    assert 595 <= delta <= 605
    assert row["locked_by"] is None and row["heartbeat_at"] is None
    assert row["last_error"] == "x"
    assert await _q(db, "SELECT 1 FROM ops.dlq WHERE job_id = :i", i=job_id) == []


async def test_job_deferred_merges_into_queued_duplicate(
    db: SessionFactory, settings: Settings
) -> None:
    learner, domain = uuid4(), uuid4()
    first = await _enqueue(db, kinds.graph_curate(learner, domain, {}))
    second: list[UUID] = []

    async def handler(job: ClaimedJob, ctx: JobContext) -> None:
        second.append(await _enqueue(db, kinds.graph_curate(learner, domain, {})))
        raise JobDeferred(600, "learner_present")

    assert await _runner(settings, db, {"graph.curate": handler}).run_one()
    (a,) = await _q(db, "SELECT status FROM ops.jobs WHERE id = :i", i=first)
    (b,) = await _q(db, "SELECT status, run_after FROM ops.jobs WHERE id = :i", i=second[0])
    assert a["status"] == "cancelled" and b["status"] == "queued"
    assert b["run_after"] >= datetime.now(UTC) + timedelta(seconds=595)


# --- T-Q8: budget errors ---------------------------------------------------------------


async def test_budget_errors_route_to_defer_or_dlq(db: SessionFactory, settings: Settings) -> None:
    daily = await _enqueue(db, kinds.turn_extract(uuid4(), uuid4(), {}))
    session_scope = await _enqueue(db, kinds.session_checkpoint(uuid4(), uuid4(), {}))

    async def over_daily(job: ClaimedJob, ctx: JobContext) -> None:
        raise LLMBudgetExceededError("daily", 10, 5)

    async def over_session(job: ClaimedJob, ctx: JobContext) -> None:
        raise LLMBudgetExceededError("session", 10, 5)

    runner = _runner(settings, db, {"turn.extract": over_daily, "session.checkpoint": over_session})
    assert await runner.run_one() and await runner.run_one()
    (d,) = await _q(db, "SELECT status, attempt, run_after FROM ops.jobs WHERE id = :i", i=daily)
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    assert d["status"] == "queued" and d["attempt"] == 0 and d["run_after"] >= tomorrow
    (s,) = await _q(db, "SELECT status, attempt FROM ops.jobs WHERE id = :i", i=session_scope)
    assert s["status"] == "failed" and s["attempt"] == 1
    assert await _q(db, "SELECT 1 FROM ops.dlq WHERE job_id = :i", i=session_scope)


# --- T-Q5: periodic jobs, once per bucket ----------------------------------------------


async def test_periodic_scheduler_runs_once_per_bucket(
    db: SessionFactory, settings: Settings
) -> None:
    now = [datetime(2030, 1, 1, 0, 0, 30, tzinfo=UTC)]
    schedulers = [PeriodicScheduler(settings, db, clock=lambda: now[0]) for _ in range(2)]
    for _ in range(5):
        for scheduler in schedulers:
            await scheduler.tick()

    async def queued() -> dict[str, datetime]:
        rows = await _q(
            db,
            "SELECT kind, run_after FROM ops.jobs WHERE status = 'queued' "
            "AND kind IN ('session.sweep_inactive', 'account.purge_deleted')",
        )
        assert len(rows) == 2, rows
        return {r["kind"]: r["run_after"] for r in rows}

    first = await queued()
    assert first["session.sweep_inactive"] == datetime(2030, 1, 1, 0, 1, 0, tzinfo=UTC)
    assert first["account.purge_deleted"] == datetime(2030, 1, 1, 0, 10, 0, tzinfo=UTC)

    await _exec(db, "UPDATE ops.jobs SET status = 'done' WHERE status = 'queued'")
    now[0] = datetime(2030, 1, 1, 0, 1, 5, tzinfo=UTC)
    for scheduler in schedulers:
        await scheduler.tick()
    rows = await _q(
        db, "SELECT kind, run_after FROM ops.jobs WHERE status = 'queued' ORDER BY kind"
    )
    by_kind = {r["kind"]: r["run_after"] for r in rows}
    assert by_kind["session.sweep_inactive"] == datetime(2030, 1, 1, 0, 2, 0, tzinfo=UTC)
    assert len(rows) == 2


# --- T-Q11: purge job removes the learner completely -----------------------------------


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


async def test_purge_job_removes_learner_completely(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    log, _ = build_scenario()
    learner = log.learner_id
    async with db() as session, session.begin():
        await append_events(session, learner, _as_new(log.events), False)
    await ensure_user(db, learner)
    worker = ProjectorWorker(settings, pg_engine, db, neo4j, redis)
    for _ in range(50):
        if await worker.run_once() == 0:
            break
    assert (await Neo4jGraphStore(neo4j, str(learner)).dump()).nodes != {}
    async with db() as session, session.begin():
        await enqueue_job(session, kinds.turn_extract(learner, uuid4(), {}))
        await session.execute(
            text(
                "INSERT INTO ops.dispatcher_offsets (learner_id, last_dispatched_seq) "
                "VALUES (:l, 3)"
            ),
            {"l": learner},
        )
    await redis.set(f"presence:learner:{learner}", "s", ex=300)
    await redis.set(f"ratelimit:actions:{learner}", "3", ex=60)
    await redis.set(f"ratelimit:turns:{learner}", "1", ex=60)

    app = create_app(
        settings,
        resources=build_app_resources(settings, pg_engine, neo4j, redis, session_factory=db),
    )
    token = issue_access_token(learner, settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/v1/learner", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    async with db() as session, session.begin():
        await enqueue_job(session, kinds.account_purge_deleted())
    runner = JobRunner(
        settings,
        db,
        "purge-worker",
        handlers={"account.purge_deleted": handle_purge_deleted},
        context=build_job_context(settings, db, neo4j, redis, engine=pg_engine),
    )
    assert await runner.run_one()

    for table in PURGE_TABLES:
        assert await _q(db, f"SELECT 1 FROM {table} WHERE learner_id = :l", l=learner) == [], table
    assert "ops.dispatcher_offsets" in PURGE_TABLES and "ops.turn_manifests" in PURGE_TABLES
    assert await _q(db, "SELECT 1 FROM auth.users WHERE id = :l", l=learner) == []
    assert (await Neo4jGraphStore(neo4j, str(learner)).dump()).nodes == {}
    assert [key async for key in redis.scan_iter(match=f"*{learner}*")] == []
    [tomb] = await _q(
        db, "SELECT status FROM ops.deletion_tombstones WHERE learner_id = :l", l=learner
    )
    assert tomb["status"] == "purged"
