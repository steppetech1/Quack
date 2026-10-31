"""Phase 3 job queue and workers (ТЗ Фазы 3 §8.3): T-Q1…T-Q4, T-Q8.

T-Q7 and T-Q9 are in `test_jobs_phase3_pending.py` until WP3.3 and WP11.4.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.jobs import kinds
from quack.jobs.kinds import JobDeferred
from quack.jobs.queue import ClaimedJob, JobSpec, claim_next_job, enqueue_job
from quack.jobs.runner import JobContext, JobHandler, JobRunner
from quack.llm.errors import LLMBudgetExceededError

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
