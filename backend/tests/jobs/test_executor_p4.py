"""ТЗ Ф4 WP7.4: the executor on the queue of Phase 4 — the pulse and the cancel (T-D6), the end of
a handler (success, retry, defer, permanent failure — T-D8), shutdown, a lost job, the worker loop,
the pressure flags of the models (T-Q10, WP5.3), the grace period of a stop (WP3.5). Postgres;
Redis for the flags (D-A-231)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.redis import llm_pressure_key
from quack.jobs import queue_p4 as q
from quack.jobs.executor_p4 import JobWorker, budget_outcome, run_claimed
from quack.jobs.rules_p4 import KIND_LLM_ROLES, JobPlan
from quack.jobs.types import JobDeferred, JobFailedPermanently
from quack.llm.errors import LLMBudgetExceededError
from tests.db.fixtures import create_learner

SETTINGS = Settings(job_heartbeat_interval_sec=1, job_max_attempts=2, consumer_poll_ms=50)


async def _put(db: Db, learner: UUID, key: str = "a", **extra: object) -> q.ClaimedJob:
    plan = JobPlan(
        kind="agent.viz",
        job_key=key,
        priority_class="P2",
        learner_id=learner,
        **extra,  # type: ignore[arg-type]
    )
    await q.enqueue(db, plan)
    job = await q.claim_next(db, SETTINGS)
    assert job is not None
    return job


async def _status(db: Db, key: str = "a") -> str:
    row = await db.fetch_one("SELECT status FROM ops.jobs WHERE job_key = %(k)s", {"k": key})
    assert row is not None
    return row["status"]


async def test_a_finished_handler_succeeds(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    job = await _put(jobs_db, learner)
    ran = []

    async def work() -> None:
        ran.append(1)

    assert await run_claimed(jobs_db, SETTINGS, job, work) == "succeeded"
    assert ran and await _status(jobs_db) == "succeeded"


async def test_cancel_requested_is_seen_at_the_pulse_and_stops_the_task(jobs_db: Db) -> None:
    """T-D6: a running job gets `cancel_requested`, sees it at the pulse, the task is
    cancelled, the job ends `cancelled`."""
    learner = await create_learner(jobs_db)
    request = uuid4()
    job = await _put(jobs_db, learner, payload={"request_id": str(request)})
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def work() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    running = asyncio.create_task(run_claimed(jobs_db, SETTINGS, job, work))
    await started.wait()
    outcome = await q.request_cancel(jobs_db, learner, [request])
    assert outcome.requested == 1
    assert await asyncio.wait_for(running, timeout=5) == "cancelled"
    assert cancelled.is_set()
    assert await _status(jobs_db) == "cancelled"


async def test_a_failed_pulse_does_not_orphan_the_handler(
    jobs_db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Law 7/9: the database failing at one pulse is logged; the handler goes on and the job
    ends `succeeded`."""
    learner = await create_learner(jobs_db)
    job = await _put(jobs_db, learner)
    real, calls = q.heartbeat, []

    async def flaky(db: Db, claimed: q.ClaimedJob) -> q.JobStatus | None:
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("database blip")
        return await real(db, claimed)

    monkeypatch.setattr(q, "heartbeat", flaky)

    async def work() -> None:
        await asyncio.sleep(2.5)  # three pulses of 1 s

    assert await run_claimed(jobs_db, SETTINGS, job, work) == "succeeded"
    assert len(calls) >= 2 and await _status(jobs_db) == "succeeded"


async def test_a_job_the_cleaner_took_away_stops_without_writing(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    job = await _put(jobs_db, learner)
    started = asyncio.Event()

    async def work() -> None:
        started.set()
        await asyncio.Event().wait()

    running = asyncio.create_task(run_claimed(jobs_db, SETTINGS, job, work))
    await started.wait()
    await jobs_db.execute("UPDATE ops.jobs SET heartbeat_at = now() - interval '5 minutes'")
    assert await q.reap_stale(jobs_db, SETTINGS) == 1
    assert await asyncio.wait_for(running, timeout=5) == "lost"
    assert await _status(jobs_db) == "queued"  # the cleaner's decision stands


async def test_a_failing_handler_is_retried_then_failed_with_the_event(jobs_db: Db) -> None:
    """T-D8 through the executor: `job_max_attempts` = 2."""
    learner = await create_learner(jobs_db)
    session = uuid4()
    first = await _put(jobs_db, learner, session_id=session)

    async def broken() -> None:
        raise RuntimeError("model said no")

    assert await run_claimed(jobs_db, SETTINGS, first, broken) == "retried"
    assert await _status(jobs_db) == "queued"
    await jobs_db.execute("UPDATE ops.jobs SET run_at = now()")
    second = await q.claim_next(jobs_db, SETTINGS)
    assert second is not None and second.attempts == 2
    assert await run_claimed(jobs_db, SETTINGS, second, broken) == "failed"
    assert await _status(jobs_db) == "failed"
    (event,) = await jobs_db.fetch_all(
        "SELECT type, session_id, payload FROM core.events WHERE learner_id = %(l)s",
        {"l": learner},
    )
    assert event["type"] == "agent.completed" and event["session_id"] == session
    assert event["payload"]["ok"] is False and "model said no" in event["payload"]["error"]


async def test_permanent_failure_and_deferral(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    job = await _put(jobs_db, learner)

    async def later() -> None:
        raise JobDeferred(3600, "daily budget")

    assert await run_claimed(jobs_db, SETTINGS, job, later) == "deferred"
    row = await jobs_db.fetch_one("SELECT status, attempts, last_error FROM ops.jobs")
    assert row is not None
    assert (row["status"], row["attempts"], row["last_error"]) == ("queued", 0, "daily budget")

    await jobs_db.execute("UPDATE ops.jobs SET run_at = now()")
    again = await q.claim_next(jobs_db, SETTINGS)
    assert again is not None

    async def never() -> None:
        raise JobFailedPermanently("session budget")

    assert await run_claimed(jobs_db, SETTINGS, again, never) == "failed"
    assert await _status(jobs_db) == "failed"
    (event,) = await jobs_db.fetch_all("SELECT payload FROM core.events")
    assert event["payload"]["error"] == "session budget"


async def test_budget_errors_route_to_defer_or_failure(jobs_db: Db) -> None:
    """WP3.4, seam S3 (D-A-233): a session budget fails the job at once; a daily or pool budget
    defers it to the next UTC midnight plus a jitter, the attempt unspent."""
    learner = await create_learner(jobs_db)
    session_job = await _put(jobs_db, learner, "s")

    async def over_session() -> None:
        raise LLMBudgetExceededError("session", 1.0, 0.5, "usd")

    assert await run_claimed(jobs_db, SETTINGS, session_job, over_session) == "failed"

    daily_job = await _put(jobs_db, learner, "d")

    async def over_daily() -> None:
        raise LLMBudgetExceededError("daily", 3.0, 2.0, "usd")

    assert await run_claimed(jobs_db, SETTINGS, daily_job, over_daily) == "deferred"
    row = await jobs_db.fetch_one(
        "SELECT status, attempts, run_at FROM ops.jobs WHERE job_key = 'd'"
    )
    assert row is not None and (row["status"], row["attempts"]) == ("queued", 0)
    now = datetime.now(UTC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    assert (
        midnight - timedelta(minutes=1)
        <= row["run_at"]
        <= midnight + timedelta(seconds=SETTINGS.daily_budget_defer_jitter_s + 60)
    )

    pool = budget_outcome(LLMBudgetExceededError("pool", 0, 0, "usd"), SETTINGS, now)
    assert isinstance(pool, JobDeferred) and pool.delay_sec >= (midnight - now).total_seconds()


async def test_shutdown_returns_the_job_to_the_queue(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    job = await _put(jobs_db, learner)
    started = asyncio.Event()

    async def work() -> None:
        started.set()
        await asyncio.Event().wait()

    running = asyncio.create_task(run_claimed(jobs_db, SETTINGS, job, work))
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    row = await jobs_db.fetch_one("SELECT status, attempts FROM ops.jobs")
    assert row is not None and (row["status"], row["attempts"]) == ("queued", 0)


async def test_worker_runs_only_kinds_with_a_handler_and_admission(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    for key, kind in (("v", "agent.viz"), ("t", "task.check"), ("n", "embedding.compute")):
        await q.enqueue(
            jobs_db, JobPlan(kind=kind, job_key=key, priority_class="P2", learner_id=learner)
        )
    seen: list[str] = []

    async def handler(job: q.ClaimedJob) -> None:
        seen.append(job.kind)

    async def admission() -> list[str]:
        return ["agent.viz", "embedding.compute"]  # task.check's model is under pressure

    worker = JobWorker(
        SETTINGS,
        jobs_db,
        {"agent.viz": handler, "task.check": handler},
        admission=admission,
    )
    assert await worker.run_one() is True
    assert await worker.run_one() is False  # embedding.compute has no handler here
    assert seen == ["agent.viz"]
    assert await _status(jobs_db, "t") == "queued" and await _status(jobs_db, "n") == "queued"


async def test_worker_loop_runs_jobs_and_stops_on_the_event(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    for i in range(3):
        await q.enqueue(
            jobs_db,
            JobPlan(kind="agent.viz", job_key=f"j{i}", priority_class="P2", learner_id=learner),
        )
    done = asyncio.Event()
    count = 0

    async def handler(_job: q.ClaimedJob) -> None:
        nonlocal count
        count += 1
        if count == 3:
            done.set()

    async def admission() -> list[str]:
        return ["agent.viz"]

    worker = JobWorker(
        SETTINGS.model_copy(update={"worker_concurrency": 2}),
        jobs_db,
        {"agent.viz": handler},
        admission=admission,
    )
    stop = asyncio.Event()
    loop = asyncio.create_task(worker.run_forever(stop))
    await asyncio.wait_for(done.wait(), timeout=10)
    stop.set()
    await asyncio.wait_for(loop, timeout=10)
    rows = await jobs_db.fetch_all("SELECT status FROM ops.jobs")
    assert [r["status"] for r in rows] == ["succeeded"] * 3


async def test_a_deferred_job_yields_to_a_waiting_duplicate(jobs_db: Db) -> None:
    """A job deferred while a newer one with its `coalesce_key` waits is superseded: it becomes
    `cancelled`, the waiting one stays `queued` and is held back by the same delay."""
    learner = await create_learner(jobs_db)
    key = f"topic:{learner}"
    job = await _put(jobs_db, learner, "a", coalesce_key=key, lock_key=key)  # running
    waiting = JobPlan(
        kind="agent.viz",
        job_key="b",
        priority_class="P2",
        learner_id=learner,
        lock_key=key,
        coalesce_key=key,
    )
    assert await q.enqueue(jobs_db, waiting) == "inserted"  # the running one is out of the index

    async def later() -> None:
        raise JobDeferred(600, "learner_present")

    assert await run_claimed(jobs_db, SETTINGS, job, later) == "deferred"
    assert await _status(jobs_db, "a") == "cancelled"
    row = await jobs_db.fetch_one("SELECT status, run_at FROM ops.jobs WHERE job_key = 'b'")
    assert row is not None and row["status"] == "queued"
    assert row["run_at"] >= datetime.now(UTC) + timedelta(seconds=595)


async def test_pressure_flag_holds_back_the_kinds_of_that_model(jobs_db: Db, redis: Redis) -> None:
    """WP5.3, T-Q10: while `llm:pressure:{model}` is set, kinds whose roles run on the model
    wait; a kind without a model is still claimed. The flag expires — the kind is claimed."""
    learner = await create_learner(jobs_db)
    model = SETTINGS.model_for_role("extractor")
    assert KIND_LLM_ROLES["extract.episode"] == ("extractor",)
    assert KIND_LLM_ROLES["session.sweep_inactive"] == ()
    for key, kind, cls in (("e", "extract.episode", "P2"), ("s", "session.sweep_inactive", "P3")):
        plan = JobPlan(kind=kind, job_key=key, priority_class=cls, learner_id=learner)  # type: ignore[arg-type]
        await q.enqueue(jobs_db, plan)
    await redis.set(llm_pressure_key(model), "x", px=1000)
    claimed: list[str] = []

    async def handler(job: q.ClaimedJob) -> None:
        claimed.append(job.job_key)

    worker = JobWorker(
        SETTINGS,
        jobs_db,
        {"extract.episode": handler, "session.sweep_inactive": handler},
        redis=redis,
    )
    assert await worker.run_one()  # P2 would go first, but its model is under pressure
    assert claimed == ["s"]
    assert await worker.run_one() is False
    assert await _status(jobs_db, "e") == "queued"

    await asyncio.sleep(1.1)  # the flag expires by its TTL
    assert await redis.exists(llm_pressure_key(model)) == 0
    assert await worker.run_one()
    assert claimed == ["s", "e"]


async def test_without_redis_no_kind_is_held_back(jobs_db: Db) -> None:
    """§4.2: a worker without Redis claims every kind and raises nothing."""
    learner = await create_learner(jobs_db)
    await q.enqueue(
        jobs_db,
        JobPlan(kind="extract.episode", job_key="e", priority_class="P2", learner_id=learner),
    )
    seen: list[str] = []

    async def handler(job: q.ClaimedJob) -> None:
        seen.append(job.job_key)

    worker = JobWorker(SETTINGS, jobs_db, {"extract.episode": handler}, redis=None)
    assert await worker.run_one()
    assert seen == ["e"]


async def test_a_stop_waits_for_the_grace_period_then_returns_the_rest_to_the_queue(
    jobs_db: Db,
) -> None:
    """WP3.5: on stop the worker takes no new jobs, waits `worker_shutdown_grace_s` for the
    running ones and puts the rest back to `queued`: attempt unchanged, no pulse."""
    learner = await create_learner(jobs_db)
    for key in ("fast", "forever"):
        await q.enqueue(
            jobs_db, JobPlan(kind="agent.viz", job_key=key, priority_class="P2", learner_id=learner)
        )
    fast_done = asyncio.Event()

    async def handler(job: q.ClaimedJob) -> None:
        if job.job_key == "fast":
            await asyncio.sleep(0.05)
            fast_done.set()
        else:
            await asyncio.Event().wait()

    async def admission() -> list[str]:
        return ["agent.viz"]

    quick = SETTINGS.model_copy(
        update={"worker_shutdown_grace_s": 0.3, "worker_concurrency": 2, "consumer_poll_ms": 20}
    )
    worker = JobWorker(quick, jobs_db, {"agent.viz": handler}, admission=admission)
    stop = asyncio.Event()
    loop = asyncio.create_task(worker.run_forever(stop))
    for _ in range(100):
        if fast_done.is_set() and await _status(jobs_db, "forever") == "running":
            break
        await asyncio.sleep(0.02)
    clock = asyncio.get_running_loop()
    started = clock.time()
    stop.set()
    await asyncio.wait_for(loop, timeout=2)
    assert clock.time() - started < 2

    assert await _status(jobs_db, "fast") == "succeeded"
    row = await jobs_db.fetch_one(
        "SELECT status, attempts, heartbeat_at FROM ops.jobs WHERE job_key = 'forever'"
    )
    assert row == {"status": "queued", "attempts": 0, "heartbeat_at": None}
