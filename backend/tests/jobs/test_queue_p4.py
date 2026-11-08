"""ТЗ Ф4 WP7.4 (`Т-§13.1`–`Т-§13.2`, `Т-§13.7`–`Т-§13.9`): the queue on `Db` — insert and
coalescing, claim (class order, `required_seq` against the projector, `lock_key`,
`max_jobs_per_learner`, budget), cancel, failure with `agent.completed`, the plaque.
T-D4, T-D5, T-D6, T-D7, T-D8, T-D10 (the queue part). Needs Postgres only."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import orjson
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.redis import learner_signal_channel
from quack.jobs import queue_p4 as q
from quack.jobs.rules_p4 import JobPlan, PriorityClass
from tests.db.fixtures import create_learner

SETTINGS = Settings()


def _plan(
    learner: UUID,
    key: str,
    *,
    kind: str = "check.model",
    cls: PriorityClass = "P1",
    lock_key: str | None = None,
    coalesce_key: str | None = None,
    required_seq: int | None = None,
    run_at: datetime | None = None,
    session_id: UUID | None = None,
    visible: bool = False,
    payload: dict[str, object] | None = None,
) -> JobPlan:
    return JobPlan(
        kind=kind,
        job_key=key,
        priority_class=cls,
        learner_id=learner,
        lock_key=lock_key,
        coalesce_key=coalesce_key,
        required_seq=required_seq,
        run_at=run_at,
        session_id=session_id,
        visible=visible,
        payload=payload or {},
    )


async def _status(db: Db, key: str) -> str:
    row = await db.fetch_one("SELECT status FROM ops.jobs WHERE job_key = %(k)s", {"k": key})
    assert row is not None
    return row["status"]


async def _rows(db: Db) -> list[dict[str, Any]]:
    return await db.fetch_all("SELECT * FROM ops.jobs ORDER BY created_at, job_key")


async def _projected(db: Db, learner: UUID, seq: int) -> None:
    await db.execute(
        "INSERT INTO ops.projector_offsets (learner_id, seq) VALUES (%(l)s, %(s)s) "
        "ON CONFLICT (learner_id) DO UPDATE SET seq = EXCLUDED.seq",
        {"l": learner, "s": seq},
    )


# --- insert -------------------------------------------------------------------------------


async def test_enqueue_is_idempotent_by_job_key(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    plan = _plan(learner, "k1")
    assert await q.enqueue(jobs_db, plan) == "inserted"
    assert await q.enqueue(jobs_db, plan) == "duplicate"
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None
    await q.complete(jobs_db, job)
    assert await q.enqueue(jobs_db, plan) == "duplicate"  # whatever the status of the first
    assert len(await _rows(jobs_db)) == 1


async def test_coalesce_greatest(jobs_db: Db) -> None:
    """T-D4: the second close of a task while the analyst waits lifts its `required_seq`,
    there is no second job; after the start the next event puts a new one."""
    learner = await create_learner(jobs_db)
    key = f"analyst:{learner}"

    def plan(seq: int) -> JobPlan:
        return _plan(
            learner,
            f"{key}:{seq}",
            kind="analyst.run",
            cls="P2",
            lock_key=key,
            coalesce_key=key,
            required_seq=seq,
            payload={"trigger_seq": seq},
        )

    assert await q.enqueue(jobs_db, plan(5)) == "inserted"
    assert await q.enqueue(jobs_db, plan(9)) == "coalesced"
    assert await q.enqueue(jobs_db, plan(7)) == "coalesced"  # GREATEST, never lowers
    (row,) = await _rows(jobs_db)
    assert row["required_seq"] == 9
    assert row["payload"] == {"trigger_seq": 5}  # the payload of a coalescing job is not merged

    await _projected(jobs_db, learner, 9)
    started = await q.claim_next(jobs_db, SETTINGS)
    assert started is not None and started.required_seq == 9
    assert await q.enqueue(jobs_db, plan(12)) == "inserted"  # the running one is out of the index
    assert [r["status"] for r in await _rows(jobs_db)] == ["running", "queued"]
    # A replay of an event whose job is already there does nothing, waiting or not.
    assert await q.enqueue(jobs_db, plan(12)) == "duplicate"
    assert await q.enqueue(jobs_db, plan(5)) == "duplicate"
    assert len(await _rows(jobs_db)) == 2


async def test_coalesce_null_required_seq_stays_null(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    for seq in (1, 2):
        await q.enqueue(jobs_db, _plan(learner, f"j:{seq}", coalesce_key=f"c:{learner}"))
    (row,) = await _rows(jobs_db)
    assert row["required_seq"] is None


async def test_concurrent_coalescing_inserts_leave_one_waiting_job(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    key = f"analyst:{learner}"
    plans = [
        _plan(learner, f"{key}:{i}", coalesce_key=key, required_seq=i, cls="P2")
        for i in range(1, 11)
    ]
    results = await asyncio.gather(*(q.enqueue(jobs_db, p) for p in plans))
    assert results.count("inserted") == 1 and results.count("coalesced") == 9
    (row,) = await _rows(jobs_db)
    assert row["required_seq"] == 10


# --- claim --------------------------------------------------------------------------------


async def test_claim_orders_by_class_then_age(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    for key, cls in (("a", "P3"), ("b", "P2"), ("c", "P1"), ("d", "P2")):
        await q.enqueue(jobs_db, _plan(learner, key, cls=cls))  # type: ignore[arg-type]
    claimed = []
    for _ in range(4):
        job = await q.claim_next(jobs_db, SETTINGS.model_copy(update={"max_jobs_per_learner": 9}))
        assert job is not None
        claimed.append(job.job_key)
    assert claimed == ["c", "b", "d", "a"]


async def test_claim_counts_the_attempt_and_sets_the_pulse(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    await q.enqueue(jobs_db, _plan(learner, "a", visible=True, payload={"x": 1}))
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None
    assert (job.attempts, job.visible, job.payload) == (1, True, {"x": 1})
    (row,) = await _rows(jobs_db)
    assert row["status"] == "running" and row["heartbeat_at"] is not None
    assert await q.claim_next(jobs_db, SETTINGS) is None


async def test_job_waits_for_the_projector(jobs_db: Db) -> None:
    """`projector_offsets.seq >= required_seq` is in the claim (Т-§13.1)."""
    learner = await create_learner(jobs_db)
    await q.enqueue(jobs_db, _plan(learner, "graph", required_seq=5))
    assert await q.claim_next(jobs_db, SETTINGS) is None  # the projector has not started
    await _projected(jobs_db, learner, 4)
    assert await q.claim_next(jobs_db, SETTINGS) is None
    await _projected(jobs_db, learner, 5)
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None and job.job_key == "graph"


async def test_projector_of_another_learner_does_not_release_the_job(jobs_db: Db) -> None:
    mine, other = await create_learner(jobs_db), await create_learner(jobs_db)
    await q.enqueue(jobs_db, _plan(mine, "graph", required_seq=5))
    await _projected(jobs_db, other, 100)
    assert await q.claim_next(jobs_db, SETTINGS) is None


async def test_job_that_reads_only_the_log_does_not_wait(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    await q.enqueue(jobs_db, _plan(learner, "log"))
    assert await q.claim_next(jobs_db, SETTINGS) is not None


async def test_future_run_at_is_not_claimed(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    await q.enqueue(jobs_db, _plan(learner, "later", run_at=datetime.now(UTC) + timedelta(hours=1)))
    await q.enqueue(jobs_db, _plan(learner, "past", run_at=datetime.now(UTC) - timedelta(hours=1)))
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None and job.job_key == "past"
    assert await q.claim_next(jobs_db, SETTINGS) is None


async def test_one_running_job_per_lock_key(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    for key in ("a", "b"):
        await q.enqueue(jobs_db, _plan(learner, key, lock_key="seq:1"))
    first = await q.claim_next(jobs_db, SETTINGS)
    assert first is not None
    assert await q.claim_next(jobs_db, SETTINGS) is None  # waits for the next round
    await q.complete(jobs_db, first)
    second = await q.claim_next(jobs_db, SETTINGS)
    assert second is not None and second.job_key != first.job_key


async def test_lock_is_held_while_cancel_is_requested(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    request = uuid4()
    await q.enqueue(
        jobs_db, _plan(learner, "a", lock_key="seq:1", payload={"request_id": str(request)})
    )
    await q.enqueue(jobs_db, _plan(learner, "b", lock_key="seq:1"))
    assert await q.claim_next(jobs_db, SETTINGS) is not None
    await q.request_cancel(jobs_db, learner, [request])
    assert await _status(jobs_db, "a") == "cancel_requested"
    assert await q.claim_next(jobs_db, SETTINGS) is None  # the executor still works on `a`


async def test_kinds_filter(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    await q.enqueue(jobs_db, _plan(learner, "a", kind="check.model"))
    await q.enqueue(jobs_db, _plan(learner, "b", kind="embedding.compute", cls="P2"))
    assert await q.claim_next(jobs_db, SETTINGS, kinds=["task.check"]) is None
    assert await q.claim_next(jobs_db, SETTINGS, kinds=[]) is None
    job = await q.claim_next(jobs_db, SETTINGS, kinds=["embedding.compute"])
    assert job is not None and job.job_key == "b"


async def test_max_jobs_per_learner(jobs_db: Db) -> None:
    """T-D5: the fifth job of a learner is not taken while four work; others' jobs are."""
    learner, other = await create_learner(jobs_db), await create_learner(jobs_db)
    for i in range(5):
        await q.enqueue(jobs_db, _plan(learner, f"m{i}", lock_key=f"l:{i}"))
    taken = [await q.claim_next(jobs_db, SETTINGS) for _ in range(4)]
    assert all(job is not None for job in taken)
    assert await q.claim_next(jobs_db, SETTINGS) is None
    await q.enqueue(jobs_db, _plan(other, "o0"))
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None and job.learner_id == other
    first = taken[0]
    assert first is not None
    await q.complete(jobs_db, first)
    fifth = await q.claim_next(jobs_db, SETTINGS)
    assert fifth is not None and fifth.job_key == "m4"


async def test_cancel_requested_job_still_counts_against_the_learner(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    settings = SETTINGS.model_copy(update={"max_jobs_per_learner": 1})
    request = uuid4()
    await q.enqueue(jobs_db, _plan(learner, "a", payload={"request_id": str(request)}))
    await q.enqueue(jobs_db, _plan(learner, "b"))
    assert await q.claim_next(jobs_db, settings) is not None
    await q.request_cancel(jobs_db, learner, [request])
    assert await q.claim_next(jobs_db, settings) is None


async def _warm_pool(db: Db) -> None:
    """Open the pool's connections first, so that the claims below really run at once."""
    await asyncio.gather(*(db.fetch_one("SELECT pg_sleep(0.3)") for _ in range(10)))


async def test_concurrent_claims_never_exceed_the_cap_nor_double_claim(jobs_db: Db) -> None:
    """The race of two workers: SKIP LOCKED gives each job to one, the cap holds under load."""
    await _warm_pool(jobs_db)
    learner = await create_learner(jobs_db)
    for i in range(12):
        await q.enqueue(jobs_db, _plan(learner, f"r{i}", lock_key=f"l:{i}"))
    got = await asyncio.gather(*(q.claim_next(jobs_db, SETTINGS) for _ in range(20)))
    taken = [j.job_key for j in got if j is not None]
    assert len(taken) == len(set(taken)) == SETTINGS.max_jobs_per_learner
    running = await jobs_db.fetch_one("SELECT count(*) AS n FROM ops.jobs WHERE status = 'running'")
    assert running is not None and running["n"] == SETTINGS.max_jobs_per_learner


async def test_concurrent_claims_of_many_learners_take_every_job_once(jobs_db: Db) -> None:
    await _warm_pool(jobs_db)
    learners = [await create_learner(jobs_db) for _ in range(6)]
    for n, learner in enumerate(learners):
        for i in range(3):
            await q.enqueue(jobs_db, _plan(learner, f"{n}-{i}", lock_key=f"{n}:{i}"))
    got = await asyncio.gather(*(q.claim_next(jobs_db, SETTINGS) for _ in range(30)))
    taken = [j.job_key for j in got if j is not None]
    assert len(taken) == len(set(taken)) == 18


async def test_concurrent_claims_respect_one_lock_key(jobs_db: Db) -> None:
    await _warm_pool(jobs_db)
    learners = [await create_learner(jobs_db) for _ in range(5)]
    for i, learner in enumerate(learners):
        await q.enqueue(jobs_db, _plan(learner, f"s{i}", lock_key="global:one"))
    got = await asyncio.gather(*(q.claim_next(jobs_db, SETTINGS) for _ in range(10)))
    assert len([j for j in got if j is not None]) == 1


# --- budget -------------------------------------------------------------------------------


async def _spend(db: Db, learner: UUID, session: UUID, role: str, cost: float) -> None:
    await db.execute(
        "INSERT INTO ops.llm_usage (learner_id, session_id, role, model, status, cost_usd) "
        "VALUES (%(l)s, %(s)s, %(r)s, 'm', 'ok', %(c)s)",
        {"l": learner, "s": session, "r": role, "c": cost},
    )


async def test_skipped_budget(jobs_db: Db) -> None:
    """T-D7: a session over `session_agent_budget_usd` — P2/P3 get `skipped_budget`;
    extraction, the analyst, P1 and other sessions run; the teacher's turns do not count."""
    learner = await create_learner(jobs_db)
    over, fine = uuid4(), uuid4()
    await _spend(jobs_db, learner, over, "task_generator", SETTINGS.session_agent_budget_usd + 0.1)
    await _spend(jobs_db, learner, fine, "teacher", 50.0)
    await _spend(jobs_db, learner, fine, "extractor", 50.0)
    settings = SETTINGS.model_copy(update={"max_jobs_per_learner": 20})
    plans = [
        _plan(learner, "p2", kind="agent.task_generation", cls="P2", session_id=over),
        _plan(learner, "p3", kind="graph.hygiene", cls="P3", session_id=over),
        _plan(learner, "ext", kind="extract.episode", cls="P2", session_id=over),
        _plan(learner, "ana", kind="analyst.run", cls="P2", session_id=over),
        _plan(learner, "p1", kind="check.model", cls="P1", session_id=over),
        _plan(learner, "other", kind="agent.viz", cls="P2", session_id=fine),
        _plan(learner, "nosession", kind="today.prepare", cls="P2"),
    ]
    for plan in plans:
        await q.enqueue(jobs_db, plan)
    claimed = set()
    while (job := await q.claim_next(jobs_db, settings)) is not None:
        claimed.add(job.job_key)
    assert claimed == {"ext", "ana", "p1", "other", "nosession"}
    assert await _status(jobs_db, "p2") == "skipped_budget"
    assert await _status(jobs_db, "p3") == "skipped_budget"


async def test_budget_is_per_session_and_inclusive_of_the_limit(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    session = uuid4()
    await _spend(jobs_db, learner, session, "artifact_writer", SETTINGS.session_agent_budget_usd)
    await q.enqueue(
        jobs_db, _plan(learner, "a", kind="agent.artifact", cls="P2", session_id=session)
    )
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None  # exactly at the limit is not "beyond"


async def test_budget_follows_the_pool_mode(jobs_db: Db) -> None:
    """D-B-211 (#236, D-A-222): in `tight` the session limit is `tight_session_agent_budget_usd`;
    the same spend under the normal limit is claimed."""
    from quack.llm.access import reset_pool_cache

    learner = await create_learner(jobs_db)
    session = uuid4()
    spend = (SETTINGS.tight_session_agent_budget_usd + SETTINGS.session_agent_budget_usd) / 2
    await _spend(jobs_db, learner, session, "task_generator", spend)
    row = await jobs_db.fetch_one("SELECT sum(cost_usd)::float8 AS s FROM ops.llm_usage")
    assert row is not None
    tight = SETTINGS.model_copy(
        update={"llm_pool_budget_usd": row["s"] + SETTINGS.llm_pool_tight_remaining_usd / 2}
    )

    def plan(key: str) -> JobPlan:
        return _plan(learner, key, kind="agent.task_generation", cls="P2", session_id=session)

    await q.enqueue(jobs_db, plan("tight"))
    reset_pool_cache()
    assert await q.claim_next(jobs_db, tight) is None
    assert await _status(jobs_db, "tight") == "skipped_budget"
    await q.enqueue(jobs_db, plan("normal"))
    reset_pool_cache()
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None and job.job_key == "normal"
    reset_pool_cache()


# --- cancel -------------------------------------------------------------------------------


async def test_cancel_requested(jobs_db: Db) -> None:
    """T-D6: `planUpdate` cancels: a waiting job → `cancelled`; a running one is told at the
    pulse and the executor ends it as `cancelled`."""
    learner, stranger = await create_learner(jobs_db), await create_learner(jobs_db)
    waiting, running, kept = uuid4(), uuid4(), uuid4()
    await q.enqueue(
        jobs_db, _plan(learner, "w", lock_key="w", payload={"request_id": str(waiting)})
    )
    await q.enqueue(
        jobs_db, _plan(learner, "r", lock_key="r", payload={"request_id": str(running)})
    )
    await q.enqueue(jobs_db, _plan(learner, "k", lock_key="k", payload={"request_id": str(kept)}))
    await q.enqueue(
        jobs_db, _plan(stranger, "s", lock_key="s", payload={"request_id": str(waiting)})
    )
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None and job.job_key == "w"  # oldest first; make `r` the running one:
    await q.release(jobs_db, job)
    await jobs_db.execute(
        "UPDATE ops.jobs SET status = 'running', attempts = 1, heartbeat_at = now() "
        "WHERE job_key = 'r'"
    )
    running_job = q.ClaimedJob(
        "r", "check.model", learner, None, "P1", "r", None, None, 1, False, {}
    )
    assert await q.heartbeat(jobs_db, running_job) == "running"

    outcome = await q.request_cancel(jobs_db, learner, [waiting, running])
    assert (outcome.cancelled, outcome.requested) == (1, 1)
    assert await _status(jobs_db, "w") == "cancelled"
    assert await _status(jobs_db, "r") == "cancel_requested"
    assert await _status(jobs_db, "k") == "queued"
    assert await _status(jobs_db, "s") == "queued"  # a request id of another learner

    assert await q.heartbeat(jobs_db, running_job) == "cancel_requested"
    assert await q.mark_cancelled(jobs_db, running_job)
    assert await _status(jobs_db, "r") == "cancelled"
    assert await q.request_cancel(jobs_db, learner, [waiting, running]) == q.CancelOutcome(0, 0)


async def test_cancel_with_no_ids_touches_nothing(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    await q.enqueue(jobs_db, _plan(learner, "a", payload={"request_id": str(uuid4())}))
    assert await q.request_cancel(jobs_db, learner, []) == q.CancelOutcome(0, 0)
    assert await _status(jobs_db, "a") == "queued"


async def test_cancel_request_does_not_stop_a_finished_job(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    request = uuid4()
    await q.enqueue(jobs_db, _plan(learner, "a", payload={"request_id": str(request)}))
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None
    await q.complete(jobs_db, job)
    assert await q.request_cancel(jobs_db, learner, [request]) == q.CancelOutcome(0, 0)
    assert await _status(jobs_db, "a") == "succeeded"


# --- failure ------------------------------------------------------------------------------


async def test_failed_writes_agent_completed(jobs_db: Db) -> None:
    """T-D8: attempts out → `failed` and `agent.completed {ok: false}`; there is no DLQ."""
    learner = await create_learner(jobs_db)
    session = uuid4()
    settings = SETTINGS.model_copy(update={"job_max_attempts": 3})
    await q.enqueue(
        jobs_db,
        _plan(learner, "a", kind="agent.viz", cls="P2", session_id=session, payload={"x": 1}),
    )
    pauses = []
    for attempt in (1, 2):
        job = await q.claim_next(jobs_db, settings)
        assert job is not None and job.attempts == attempt
        decision = await q.fail_or_retry(
            jobs_db, job, RuntimeError("boom"), settings, rand=lambda: 0.5
        )
        assert decision is not None and decision.status == "queued" and decision.run_at is not None
        pauses.append((decision.run_at - datetime.now(UTC)).total_seconds())
        await jobs_db.execute("UPDATE ops.jobs SET run_at = now()")  # skip the wait
    assert pauses[0] < pauses[1]  # the pause grows
    job = await q.claim_next(jobs_db, settings)
    assert job is not None and job.attempts == 3
    decision = await q.fail_or_retry(jobs_db, job, RuntimeError("boom"), settings)
    assert decision is not None and decision.status == "failed"
    assert await _status(jobs_db, "a") == "failed"

    (event,) = await jobs_db.fetch_all(
        "SELECT type, actor, session_id, payload FROM core.events WHERE learner_id = %(l)s",
        {"l": learner},
    )
    assert event["type"] == "agent.completed" and event["session_id"] == session
    assert event["payload"]["ok"] is False and event["payload"]["job_key"] == "a"
    assert event["payload"]["error"].startswith("RuntimeError: boom")
    assert event["payload"]["kind"] == "agent.viz"
    dlq = await jobs_db.fetch_one("SELECT to_regclass('ops.dlq') AS t")
    assert dlq is not None and dlq["t"] is None


async def test_failed_request_job_names_the_request(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    request = uuid4()
    await q.enqueue(
        jobs_db,
        _plan(learner, "a", kind="agent.viz", payload={"request_id": str(request), "kind": "viz"}),
    )
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None
    await q.fail_or_retry(jobs_db, job, "no luck", SETTINGS, permanent=True)
    (event,) = await jobs_db.fetch_all("SELECT payload FROM core.events")
    assert event["payload"]["request_id"] == str(request)
    assert event["payload"]["job_key"] is None and event["payload"]["kind"] == "viz"


async def test_failure_writes_the_event_once(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    await q.enqueue(jobs_db, _plan(learner, "a"))
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None
    first = await q.fail_or_retry(jobs_db, job, "e", SETTINGS, permanent=True)
    assert first is not None and first.status == "failed"
    assert await q.fail_or_retry(jobs_db, job, "e", SETTINGS, permanent=True) is None
    events = await jobs_db.fetch_all("SELECT 1 FROM core.events")
    assert len(events) == 1


async def test_failure_commits_nothing_when_the_event_fails(jobs_db: Db) -> None:
    """Law 7: `failed` and its event are one transaction."""
    ghost = uuid4()  # no `core.learners` row: append_events refuses
    await q.enqueue(jobs_db, _plan(ghost, "a"))
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None
    try:
        await q.fail_or_retry(jobs_db, job, "e", SETTINGS, permanent=True)
    except LookupError:
        pass
    else:  # pragma: no cover
        raise AssertionError("append_events must have refused")
    assert await _status(jobs_db, "a") == "running"


async def test_stale_worker_cannot_finish_a_job_it_lost(jobs_db: Db) -> None:
    """The fence: after the cleaner and a new claim the old worker's `complete` does nothing."""
    learner = await create_learner(jobs_db)
    await q.enqueue(jobs_db, _plan(learner, "a", lock_key="l"))
    old = await q.claim_next(jobs_db, SETTINGS)
    assert old is not None
    await jobs_db.execute("UPDATE ops.jobs SET heartbeat_at = now() - interval '5 minutes'")
    assert await q.reap_stale(jobs_db, SETTINGS) == 1
    assert await _status(jobs_db, "a") == "queued"
    new = await q.claim_next(jobs_db, SETTINGS)
    assert new is not None and new.attempts == 2
    assert not await q.complete(jobs_db, old)
    assert await q.heartbeat(jobs_db, old) is None
    assert await q.fail_or_retry(jobs_db, old, "late", SETTINGS) is None
    assert await q.defer(jobs_db, old, 60, "late") is None
    assert await q.release(jobs_db, old) is None
    assert await _status(jobs_db, "a") == "running"
    assert await q.complete(jobs_db, new)


async def test_reaper_fails_a_job_out_of_attempts_and_cancels_a_stale_cancel(
    jobs_db: Db,
) -> None:
    learner = await create_learner(jobs_db)
    settings = SETTINGS.model_copy(update={"job_max_attempts": 1})
    request = uuid4()
    await q.enqueue(jobs_db, _plan(learner, "a", lock_key="a"))
    await q.enqueue(
        jobs_db, _plan(learner, "b", lock_key="b", payload={"request_id": str(request)})
    )
    assert await q.claim_next(jobs_db, settings) is not None
    assert await q.claim_next(jobs_db, settings) is not None
    await q.request_cancel(jobs_db, learner, [request])
    await jobs_db.execute("UPDATE ops.jobs SET heartbeat_at = now() - interval '5 minutes'")
    assert await q.reap_stale(jobs_db, settings) == 2
    assert await _status(jobs_db, "a") == "failed"
    assert await _status(jobs_db, "b") == "cancelled"
    assert len(await jobs_db.fetch_all("SELECT 1 FROM core.events")) == 1  # only for `a`


async def test_fresh_pulse_is_not_reaped(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    await q.enqueue(jobs_db, _plan(learner, "a"))
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None
    assert await q.heartbeat(jobs_db, job) == "running"
    assert await q.reap_stale(jobs_db, SETTINGS) == 0


async def test_defer_and_release_do_not_spend_the_attempt(jobs_db: Db) -> None:
    learner = await create_learner(jobs_db)
    await q.enqueue(jobs_db, _plan(learner, "a"))
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None
    decision = await q.defer(jobs_db, job, 3600, "model under pressure")
    assert decision is not None and decision.status == "queued"
    (row,) = await _rows(jobs_db)
    assert row["attempts"] == 0 and row["run_at"] > datetime.now(UTC) + timedelta(minutes=59)
    assert await q.claim_next(jobs_db, SETTINGS) is None
    await jobs_db.execute("UPDATE ops.jobs SET run_at = now()")
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None and job.attempts == 1
    assert await q.release(jobs_db, job) is not None
    (row,) = await _rows(jobs_db)
    assert row["attempts"] == 0 and row["status"] == "queued"


async def test_requeue_of_a_coalescing_job_yields_to_a_waiting_one(jobs_db: Db) -> None:
    """A failed analyst is not requeued beside a newer waiting one: it is superseded and its
    `required_seq` goes to the waiting job."""
    learner = await create_learner(jobs_db)
    key = f"analyst:{learner}"
    await q.enqueue(jobs_db, _plan(learner, f"{key}:5", coalesce_key=key, required_seq=5))
    await _projected(jobs_db, learner, 5)
    job = await q.claim_next(jobs_db, SETTINGS)
    assert job is not None
    await q.enqueue(jobs_db, _plan(learner, f"{key}:9", coalesce_key=key, required_seq=9))
    decision = await q.fail_or_retry(jobs_db, job, "boom", SETTINGS)
    assert decision is not None and decision.status == "cancelled"
    assert await _status(jobs_db, f"{key}:5") == "cancelled"
    waiting = await jobs_db.fetch_one(
        "SELECT required_seq, status FROM ops.jobs WHERE job_key = %(k)s", {"k": f"{key}:9"}
    )
    assert waiting is not None and waiting["status"] == "queued" and waiting["required_seq"] == 9


# --- the plaque ---------------------------------------------------------------------------


async def test_visible_jobs_of_the_session_only(jobs_db: Db) -> None:
    """T-D10 (the query): visible jobs in `queued` and `running` of this learner's session."""
    learner, other = await create_learner(jobs_db), await create_learner(jobs_db)
    session, another = uuid4(), uuid4()
    await q.enqueue(
        jobs_db, _plan(learner, "viz", kind="agent.viz", session_id=session, visible=True)
    )
    await q.enqueue(jobs_db, _plan(learner, "hid", kind="embedding.compute", session_id=session))
    await q.enqueue(jobs_db, _plan(learner, "done", session_id=session, visible=True))
    await q.enqueue(jobs_db, _plan(learner, "else", session_id=another, visible=True))
    await q.enqueue(jobs_db, _plan(other, "theirs", session_id=session, visible=True))
    await jobs_db.execute("UPDATE ops.jobs SET status = 'succeeded' WHERE job_key = 'done'")
    seen = await q.visible_jobs(jobs_db, learner, session)
    assert [(j.job_key, j.kind, j.status) for j in seen] == [("viz", "agent.viz", "queued")]
    assert await q.visible_jobs(jobs_db, other, another) == []


async def test_visible_jobs_keep_finished_for_a_few_minutes(jobs_db: Db) -> None:
    """A `failed` or `succeeded` visible job stays on the plaque for the window, then leaves."""
    learner, session = await create_learner(jobs_db), uuid4()
    for key in ("bad", "good", "old"):
        await q.enqueue(jobs_db, _plan(learner, key, session_id=session, visible=True))
    await jobs_db.execute(
        "UPDATE ops.jobs SET status = 'failed', heartbeat_at = now() WHERE job_key = 'bad'"
    )
    await jobs_db.execute(
        "UPDATE ops.jobs SET status = 'succeeded', heartbeat_at = now() WHERE job_key = 'good'"
    )
    await jobs_db.execute(
        "UPDATE ops.jobs SET status = 'failed', heartbeat_at = now() - interval '1 hour' "
        "WHERE job_key = 'old'"
    )
    seen = await q.visible_jobs(jobs_db, learner, session)
    assert sorted((j.job_key, j.status) for j in seen) == [("bad", "failed"), ("good", "succeeded")]


# --- the plaque signal (T-D10; Т-§13.8, D-C-141) ------------------------------------------


async def test_visible_and_plaque(jobs_db: Db, redis: Redis) -> None:
    learner, session = await create_learner(jobs_db), uuid4()
    pubsub = redis.pubsub()
    await pubsub.subscribe(learner_signal_channel(learner))

    async def next_message(wait_s: float) -> object:
        return await pubsub.get_message(ignore_subscribe_messages=True, timeout=wait_s)

    expected = orjson.dumps({"kind": "jobs_changed", "session_id": str(session)}).decode()
    try:
        async with jobs_db.transaction():
            await q.enqueue(
                jobs_db, _plan(learner, "v", session_id=session, visible=True), redis=redis
            )
            assert await next_message(0.2) is None  # not before the commit
        assert (await next_message(5.0))["data"] == expected  # type: ignore[index]
        await q.enqueue(jobs_db, _plan(learner, "hidden", session_id=session), redis=redis)
        await q.enqueue(jobs_db, _plan(learner, "no-session", visible=True), redis=redis)
        assert await next_message(0.3) is None
        job = await q.claim_next(jobs_db, Settings(), kinds=["check.model"], redis=redis)
        assert job is not None and job.job_key == "v"
        assert (await next_message(5.0))["data"] == expected  # type: ignore[index]
        await q.complete(jobs_db, job, redis=redis)
        assert (await next_message(5.0))["data"] == expected  # type: ignore[index]
        assert await next_message(0.3) is None  # one publication per change
    finally:
        await pubsub.aclose()


# --- periodic jobs (T-D9; WP7.5) ----------------------------------------------------------


async def test_periodic_keys(jobs_db: Db) -> None:
    """T-D9: the key names the period — ticks within one day put each daily job once, the
    weekly one once a week; the next day puts the daily ones again."""
    from quack.jobs.periodic_p4 import schedule_periodic

    monday = datetime(2026, 11, 2, 0, 5, tzinfo=UTC)
    assert await schedule_periodic(jobs_db, monday) == 5
    assert await schedule_periodic(jobs_db, monday + timedelta(seconds=59)) == 0  # one minute
    assert await schedule_periodic(jobs_db, monday + timedelta(hours=23)) == 1  # sweep only
    assert await schedule_periodic(jobs_db, monday + timedelta(days=1)) == 4  # same week
    rows = await _rows(jobs_db)
    keys = sorted(r["job_key"] for r in rows if r["kind"] != "session.sweep_inactive")
    assert keys == [
        "calibration:2026-45",
        "deletions-reconcile:2026-11-02",
        "deletions-reconcile:2026-11-03",
        "exports-cleanup:2026-11-02",
        "exports-cleanup:2026-11-03",
        "manifests-cleanup:2026-11-02",
        "manifests-cleanup:2026-11-03",
    ]
    minute = int(monday.timestamp()) // 60
    sweeps = sorted(r["job_key"] for r in rows if r["kind"] == "session.sweep_inactive")
    assert sweeps == [
        f"sweep-inactive:{minute}",
        f"sweep-inactive:{minute + 23 * 60}",
        f"sweep-inactive:{minute + 24 * 60}",
    ]


# --- the failure hook of `material.parse`: a job the cleaner gives up on fails its material ---


async def _stale_parse_job(db: Db, material_status: str, settings: Settings) -> tuple[str, UUID]:
    import quack.ops.material_parse  # noqa: F401 - registers the failure hook

    learner = await create_learner(db)
    material = uuid4()
    await db.execute(
        "INSERT INTO content.materials (material_id, learner_id, object_key, mime, status) "
        "VALUES (%(m)s, %(l)s, 'k', 'application/pdf', %(s)s)",
        {"m": material, "l": learner, "s": material_status},
    )
    await q.enqueue(
        db, _plan(learner, "parse", kind="material.parse", payload={"material_id": str(material)})
    )
    assert await q.claim_next(db, settings) is not None
    await db.execute("UPDATE ops.jobs SET heartbeat_at = now() - interval '5 minutes'")
    return "parse", material


async def _material_status(db: Db, material: UUID) -> str:
    row = await db.fetch_one(
        "SELECT status FROM content.materials WHERE material_id = %(m)s", {"m": material}
    )
    assert row is not None
    return row["status"]


async def test_heartbeat_timeout_on_the_last_attempt_fails_the_material(jobs_db: Db) -> None:
    settings = SETTINGS.model_copy(update={"job_max_attempts": 1})
    key, material = await _stale_parse_job(jobs_db, "uploaded", settings)
    assert await q.reap_stale(jobs_db, settings) == 1
    assert await _status(jobs_db, key) == "failed"
    assert await _material_status(jobs_db, material) == "failed"


async def test_a_retried_parse_does_not_fail_the_material(jobs_db: Db) -> None:
    key, material = await _stale_parse_job(jobs_db, "uploaded", SETTINGS)
    assert await q.reap_stale(jobs_db, SETTINGS) == 1
    assert await _status(jobs_db, key) == "queued"
    assert await _material_status(jobs_db, material) == "uploaded"


async def test_a_parsed_material_is_not_overwritten_by_a_failed_job(jobs_db: Db) -> None:
    settings = SETTINGS.model_copy(update={"job_max_attempts": 1})
    key, material = await _stale_parse_job(jobs_db, "parsed", settings)
    assert await q.reap_stale(jobs_db, settings) == 1
    assert await _status(jobs_db, key) == "failed"
    assert await _material_status(jobs_db, material) == "parsed"


async def test_a_raising_failure_hook_does_not_undo_failed(jobs_db: Db) -> None:
    async def broken(db: Db, job: q.ClaimedJob) -> None:
        await db.execute("SELECT 1/0")

    settings = SETTINGS.model_copy(update={"job_max_attempts": 1})
    learner = await create_learner(jobs_db)
    await q.enqueue(jobs_db, _plan(learner, "a", kind="hooked.kind"))
    assert await q.claim_next(jobs_db, settings) is not None
    await jobs_db.execute("UPDATE ops.jobs SET heartbeat_at = now() - interval '5 minutes'")
    q.FAILURE_HOOKS["hooked.kind"] = broken
    try:
        assert await q.reap_stale(jobs_db, settings) == 1
    finally:
        del q.FAILURE_HOOKS["hooked.kind"]
    assert await _status(jobs_db, "a") == "failed"
