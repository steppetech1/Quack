"""Job queue of Phase 4 on `Db` and `ops.jobs` (ТЗ Ф4 WP7.4; Т-§13.1, Т-§13.2, Т-§13.7–13.9).

The fields and statuses are exactly `Т-§13.1`. This module is the queue `workers` of Phase 4
use (the Phase 3 queue on SQLAlchemy was removed in WP1.3, D-A-234). Every function runs in the
caller's transaction when there is one (`Db.transaction()` nests), so the dispatcher inserts jobs
in the transaction of its offset.

* **Insert** (`enqueue`): `job_key` is unique — a repeat is dropped; a job with a `coalesce_key`
  raises `required_seq` of the waiting job with the same key (`GREATEST`) and leaves its payload
  alone (Т-§13.1: the payload of a coalescing job carries nothing about one event).
* **Claim** (`claim_next`): `queued`, `run_at <= now()`, the projector has applied `required_seq`
  (`ops.projector_offsets.seq`), only the allowed kinds, `ORDER BY priority_class, created_at`,
  `FOR UPDATE SKIP LOCKED`; one running job per `lock_key`; at most `max_jobs_per_learner` jobs of a
  learner in work; P2/P3 jobs of a session over `session_agent_budget_usd` become `skipped_budget`.
* **Attempts**: a claim counts an attempt (`attempts += 1`), so a worker that dies mid-job spends
  one too and a job that kills its worker does not loop; `attempts` is also the fence of the
  claim: a worker that lost its job (reaped and claimed again) completes nothing (D-A-161).
* **Plaque signal**: every status change of a `visible` job of a session publishes, after the
  commit, `{"kind": "jobs_changed", "session_id"}` on `learner:{id}` (Т-§13.8, D-C-141, #183).
* **Cancel** (`request_cancel`): queued → `cancelled`, running → `cancel_requested`; the executor
  sees the status when it beats (`heartbeat`) and cancels the task (Т-§13.1, Т-§13.7).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import orjson
import psycopg.errors
from psycopg.types.json import Jsonb
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.redis import learner_signal_channel
from quack.events.log import LearnerNotFoundError, NewEvent, append_events
from quack.jobs.rules_p4 import JobPlan
from quack.llm.access import access_mode, budgets_for
from quack.logging import get_logger

log = get_logger(__name__)

type JobStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancel_requested",
    "cancelled",
    "skipped_budget",
]

# Т-§13.2, Т-§11.4: extraction and the analyst do not count against the session budget.
BUDGET_EXEMPT_KINDS: frozenset[str] = frozenset({"extract.episode", "analyst.run", "analyst.deep"})
# Statuses in which a job occupies its `lock_key` and a place of its learner.
_IN_WORK = ("running", "cancel_requested")
_CANDIDATE_TRIES = 25
_ERROR_LIMIT = 500


@dataclass(frozen=True)
class ClaimedJob:
    """The job as the handler gets it (D-A-116): the fields of `Т-§13.1` it needs."""

    job_key: str
    kind: str
    learner_id: UUID | None
    session_id: UUID | None
    priority_class: str
    lock_key: str | None
    coalesce_key: str | None
    required_seq: int | None
    attempts: int
    visible: bool
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisibleJob:
    """A row of the learner's plaque (Т-§13.8)."""

    job_key: str
    kind: str
    status: JobStatus
    created_at: datetime


@dataclass(frozen=True)
class RetryDecision:
    status: JobStatus
    attempts: int
    run_at: datetime | None


type EnqueueResult = Literal["inserted", "coalesced", "duplicate"]

_JOB_COLUMNS = (
    "job_key, kind, learner_id, session_id, priority_class, lock_key, coalesce_key, "
    "required_seq, attempts, visible, payload"
)


def _claimed(row: dict[str, Any]) -> ClaimedJob:
    return ClaimedJob(
        job_key=row["job_key"],
        kind=row["kind"],
        learner_id=row["learner_id"],
        session_id=row["session_id"],
        priority_class=row["priority_class"],
        lock_key=row["lock_key"],
        coalesce_key=row["coalesce_key"],
        required_seq=row["required_seq"],
        attempts=int(row["attempts"]),
        visible=bool(row["visible"]),
        payload=dict(row["payload"]),
    )


# --- the plaque signal (Т-§13.8; D-C-141, #183) -----------------------------------------

_pending_signals: set[asyncio.Task[None]] = set()


async def _publish_jobs_changed(redis: Redis, learner_id: UUID, session_id: UUID) -> None:
    try:
        await redis.publish(
            learner_signal_channel(learner_id),
            orjson.dumps({"kind": "jobs_changed", "session_id": str(session_id)}),
        )
    except Exception:  # law 9: a lost signal does not undo the transition
        log.warning("jobs_changed_publish_failed", learner_id=str(learner_id))


def _jobs_changed(
    db: Db, redis: Redis | None, learner_id: UUID | None, session_id: UUID | None, visible: bool
) -> None:
    """A job on the learner's plaque changed its status: after the commit, one signal; the
    client asks `GET /v1/sessions/{id}/jobs`. A job not `visible` or without a session — none."""
    if redis is None or learner_id is None or session_id is None or not visible:
        return
    learner, session = learner_id, session_id

    def schedule() -> None:
        task = asyncio.get_running_loop().create_task(
            _publish_jobs_changed(redis, learner, session)
        )
        _pending_signals.add(task)
        task.add_done_callback(_pending_signals.discard)

    db.on_commit(schedule)


def _job_changed(db: Db, redis: Redis | None, job: ClaimedJob) -> None:
    _jobs_changed(db, redis, job.learner_id, job.session_id, job.visible)


# --- insert -------------------------------------------------------------------------------

_JOB_EXISTS = "SELECT 1 FROM ops.jobs WHERE job_key = %(job_key)s"

_COALESCE = (
    "UPDATE ops.jobs SET required_seq = GREATEST(required_seq, %(required_seq)s) "
    "WHERE coalesce_key = %(coalesce_key)s AND status = 'queued' RETURNING job_key"
)

_INSERT = (
    "INSERT INTO ops.jobs (job_key, kind, learner_id, session_id, priority_class, lock_key, "
    "coalesce_key, required_seq, status, attempts, run_at, visible, payload, created_at) "
    "VALUES (%(job_key)s, %(kind)s, %(learner_id)s, %(session_id)s, %(priority_class)s, "
    "%(lock_key)s, %(coalesce_key)s, %(required_seq)s, 'queued', 0, "
    "COALESCE(%(run_at)s::timestamptz, now()), %(visible)s, %(payload)s, clock_timestamp()) "
    "ON CONFLICT (job_key) DO NOTHING RETURNING job_key"
)


async def enqueue(db: Db, plan: JobPlan, *, redis: Redis | None = None) -> EnqueueResult:
    """Put one job (WP7.1, Т-§13.1). Idempotent by `job_key`: the dispatcher replaying an event
    after a crash finds the job it already put, whatever its status, and does nothing."""
    params = {
        "job_key": plan.job_key,
        "kind": plan.kind,
        "learner_id": plan.learner_id,
        "session_id": plan.session_id,
        "priority_class": plan.priority_class,
        "lock_key": plan.lock_key,
        "coalesce_key": plan.coalesce_key,
        "required_seq": plan.required_seq,
        "run_at": plan.run_at,
        "visible": plan.visible,
        "payload": Jsonb(plan.payload),
    }
    async with db.transaction() as conn:
        if await db.fetch_one(_JOB_EXISTS, params) is not None:
            return "duplicate"
        for _ in range(2):
            if plan.coalesce_key is not None and await db.fetch_one(_COALESCE, params) is not None:
                return "coalesced"
            try:
                async with conn.transaction():  # a savepoint: the unique index may refuse
                    inserted = await db.fetch_one(_INSERT, params)
            except psycopg.errors.UniqueViolation:
                # A concurrent insert took the `coalesce_key` between our UPDATE and INSERT.
                continue
            if inserted is None:
                return "duplicate"
            _jobs_changed(db, redis, plan.learner_id, plan.session_id, plan.visible)
            return "inserted"
    raise RuntimeError(f"job {plan.job_key} lost the race for {plan.coalesce_key} twice")


# --- claim --------------------------------------------------------------------------------

_CANDIDATE = (
    f"SELECT {_JOB_COLUMNS} FROM ops.jobs q WHERE q.status = 'queued' AND q.run_at <= now() "
    # Т-§13.1: the graph the job reads has been applied by the projector.
    "AND (q.required_seq IS NULL OR EXISTS (SELECT 1 FROM ops.projector_offsets o "
    "WHERE o.learner_id = q.learner_id AND o.seq >= q.required_seq)) "
    "AND (%(kinds)s::text[] IS NULL OR q.kind = ANY(%(kinds)s::text[])) "
    "AND (q.lock_key IS NULL OR NOT EXISTS (SELECT 1 FROM ops.jobs r "
    "WHERE r.lock_key = q.lock_key AND r.status IN ('running', 'cancel_requested'))) "
    # Т-§13.2: a cheap pre-check of `max_jobs_per_learner`; the exact one is under the lock.
    "AND (q.learner_id IS NULL OR (SELECT count(*) FROM ops.jobs r WHERE r.learner_id = "
    "q.learner_id AND r.status IN ('running', 'cancel_requested')) < %(max_jobs)s) "
    "AND q.job_key <> ALL(%(tried)s::text[]) "
    "ORDER BY q.priority_class, q.created_at, q.job_key FOR UPDATE SKIP LOCKED LIMIT 1"
)

_LEARNER_LOCK = "SELECT pg_advisory_xact_lock(hashtext('ops.jobs.claim'), hashtext(%(learner)s))"

_LEARNER_LOAD = (
    "SELECT count(*) AS n FROM ops.jobs WHERE learner_id = %(learner_id)s "
    "AND status IN ('running', 'cancel_requested')"
)

_START = (
    "UPDATE ops.jobs SET status = 'running', attempts = attempts + 1, heartbeat_at = now() "
    f"WHERE job_key = %(job_key)s AND status = 'queued' RETURNING {_JOB_COLUMNS}"
)

_SKIP_BUDGET = (
    "UPDATE ops.jobs SET status = 'skipped_budget', last_error = %(reason)s "
    "WHERE job_key = %(job_key)s AND status = 'queued'"
)

_SESSION_SPEND = (
    "SELECT COALESCE(sum(cost_usd), 0)::float8 AS spent FROM ops.llm_usage "
    "WHERE session_id = %(session_id)s AND role = ANY(%(roles)s::text[])"
)


def budget_roles(settings: Settings) -> list[str]:
    """Roles whose calls count against `session_agent_budget_usd` (Т-§11.4): background roles of
    classes P2 and P3 minus extraction and the analyst, which the budget does not limit."""
    exempt = set(settings.session_budget_exempt_roles) | {"extractor", "analyst", "analyst_deep"}
    return sorted(
        role
        for role, cfg in settings.llm_roles.items()
        if cfg.priority_class in ("P2", "P3") and role not in exempt
    )


async def _over_budget(db: Db, settings: Settings, job: ClaimedJob) -> bool:
    if (
        job.session_id is None
        or job.priority_class not in ("P2", "P3")
        or job.kind in BUDGET_EXEMPT_KINDS
    ):
        return False
    row = await db.fetch_one(
        _SESSION_SPEND, {"session_id": job.session_id, "roles": budget_roles(settings)}
    )
    if row is None:
        return False
    # D-B-211 (#236, D-A-222): the session limit of the pool mode (`tight` — the lower one).
    limit = budgets_for(settings, await access_mode(db, settings)).session_usd
    return float(row["spent"]) > limit


async def claim_next(
    db: Db, settings: Settings, *, kinds: Sequence[str] | None = None, redis: Redis | None = None
) -> ClaimedJob | None:
    """Take the best claimable job, or None. `kinds` — the kinds with a handler whose models
    have admission (Т-§13.2); None — any. Jobs over the session budget met on the way become
    `skipped_budget` and the next one is taken."""
    tried: list[str] = []
    for _ in range(_CANDIDATE_TRIES):
        async with db.transaction() as conn:
            candidate = await db.fetch_one(
                _CANDIDATE,
                {
                    "kinds": None if kinds is None else list(kinds),
                    "max_jobs": settings.max_jobs_per_learner,
                    "tried": tried,
                },
            )
            if candidate is None:
                return None
            job = _claimed(candidate)
            if job.learner_id is not None:
                # Claims of one learner go one at a time, so the count below cannot be
                # outrun by a concurrent claim of another job of the same learner.
                await db.fetch_one(_LEARNER_LOCK, {"learner": str(job.learner_id)})
                load = await db.fetch_one(_LEARNER_LOAD, {"learner_id": job.learner_id})
                if load is not None and int(load["n"]) >= settings.max_jobs_per_learner:
                    tried.append(job.job_key)
                    continue
            if await _over_budget(db, settings, job):
                await db.execute(
                    _SKIP_BUDGET,
                    {"job_key": job.job_key, "reason": "session_agent_budget_usd exceeded"},
                )
                log.info("job_skipped_budget", kind=job.kind, job_key=job.job_key)
                _job_changed(db, redis, job)
                continue
            try:
                async with conn.transaction():
                    started = await db.fetch_one(_START, {"job_key": job.job_key})
            except psycopg.errors.UniqueViolation:
                # Another worker started a job with this `lock_key` an instant ago.
                tried.append(job.job_key)
                continue
            if started is None:
                tried.append(job.job_key)
                continue
            _job_changed(db, redis, job)
            return _claimed(started)
    return None


# --- running ------------------------------------------------------------------------------

_HEARTBEAT = (
    "UPDATE ops.jobs SET heartbeat_at = now() WHERE job_key = %(job_key)s "
    "AND attempts = %(attempts)s AND status IN ('running', 'cancel_requested') RETURNING status"
)


async def heartbeat(db: Db, job: ClaimedJob) -> JobStatus | None:
    """The pulse (Т-§13.1). Returns the status the executor must act on: `running`, or
    `cancel_requested` — cancel the task; None — the job is no longer ours."""
    row = await db.fetch_one(_HEARTBEAT, {"job_key": job.job_key, "attempts": job.attempts})
    return None if row is None else row["status"]


_FINISH = (
    "UPDATE ops.jobs SET status = %(status)s, last_error = %(error)s "
    "WHERE job_key = %(job_key)s AND attempts = %(attempts)s "
    "AND status IN ('running', 'cancel_requested') RETURNING job_key"
)


async def _finish(
    db: Db, job: ClaimedJob, status: JobStatus, error: str | None, redis: Redis | None
) -> bool:
    async with db.transaction():
        row = await db.fetch_one(
            _FINISH,
            {"job_key": job.job_key, "attempts": job.attempts, "status": status, "error": error},
        )
        if row is not None:
            _job_changed(db, redis, job)
    return row is not None


async def complete(db: Db, job: ClaimedJob, *, redis: Redis | None = None) -> bool:
    """`succeeded`. A job whose cancel was requested but finished anyway keeps its result."""
    return await _finish(db, job, "succeeded", None, redis)


async def mark_cancelled(db: Db, job: ClaimedJob, *, redis: Redis | None = None) -> bool:
    """The executor cancelled the task after `cancel_requested` (Т-§13.7)."""
    return await _finish(db, job, "cancelled", None, redis)


# --- failure, retry, defer, release -------------------------------------------------------

# A kind may own state that must learn of the job's final failure (a material stays `uploaded`
# otherwise). The hook runs in the transaction that makes the job `failed`, however it got there:
# a handler error, a permanent error, a heartbeat timeout seen by the cleaner.
type FailureHook = Callable[[Db, ClaimedJob], Awaitable[None]]

FAILURE_HOOKS: dict[str, FailureHook] = {}


def register_failure_hook(kind: str) -> Callable[[FailureHook], FailureHook]:
    def decorator(hook: FailureHook) -> FailureHook:
        if kind in FAILURE_HOOKS:
            raise ValueError(f"failure hook for job kind {kind!r} is already registered")
        FAILURE_HOOKS[kind] = hook
        return hook

    return decorator


async def _run_failure_hook(db: Db, job: ClaimedJob) -> None:
    """A hook that raises must not undo `failed`: it gets its own savepoint and a log line."""
    hook = FAILURE_HOOKS.get(job.kind)
    if hook is None:
        return
    try:
        async with db.transaction() as conn, conn.transaction():
            await hook(db, job)
    except Exception as exc:
        log.error("job_failure_hook_failed", kind=job.kind, job_key=job.job_key, error=repr(exc))


def backoff_seconds(
    attempts: int, settings: Settings, *, rand: Callable[[], float] = random.random
) -> float:
    """`min(cap, base * 2 ** attempts)` with a spread of ±25 % (Т-§13.1: a growing pause with a
    random spread), never above the cap."""
    delay = min(settings.job_backoff_max_sec, settings.job_backoff_base_sec * 2**attempts)
    return min(settings.job_backoff_max_sec, delay * (0.75 + 0.5 * rand()))


_LOCK_JOB = (
    f"SELECT {_JOB_COLUMNS}, status FROM ops.jobs WHERE job_key = %(job_key)s "
    "AND attempts = %(attempts)s AND status IN ('running', 'cancel_requested') FOR UPDATE"
)

_REQUEUE = (
    "UPDATE ops.jobs SET status = 'queued', heartbeat_at = NULL, last_error = %(error)s, "
    "attempts = %(attempts)s, run_at = now() + make_interval(secs => %(delay)s) "
    "WHERE job_key = %(job_key)s RETURNING run_at"
)

_SUPERSEDE = (
    "UPDATE ops.jobs SET required_seq = GREATEST(required_seq, %(required_seq)s), "
    "run_at = GREATEST(run_at, now() + make_interval(secs => %(delay)s)) "
    "WHERE coalesce_key = %(coalesce_key)s AND status = 'queued' RETURNING job_key"
)


_CANCEL_SUPERSEDED = (
    "UPDATE ops.jobs SET status = 'cancelled', last_error = %(error)s, "
    "attempts = %(attempts)s WHERE job_key = %(job_key)s"
)


async def _requeue(
    db: Db,
    job: ClaimedJob,
    *,
    attempts: int,
    delay_s: float,
    error: str | None,
    redis: Redis | None,
) -> RetryDecision:
    """Back to `queued` after `delay_s`. A newer job with the same `coalesce_key` already waits
    (the unique index refuses a second one): this job is superseded — its `required_seq` goes to
    the waiting one, and it becomes `cancelled`."""
    params = {"job_key": job.job_key, "attempts": attempts, "delay": delay_s, "error": error}
    async with db.transaction() as conn:
        try:
            async with conn.transaction():
                row = await db.fetch_one(_REQUEUE, params)
        except psycopg.errors.UniqueViolation:
            await db.fetch_one(
                _SUPERSEDE,
                {
                    "required_seq": job.required_seq,
                    "delay": delay_s,
                    "coalesce_key": job.coalesce_key,
                },
            )
            await db.execute(
                _CANCEL_SUPERSEDED,
                {**params, "error": "superseded by a waiting job with the same coalesce_key"},
            )
            _job_changed(db, redis, job)
            return RetryDecision("cancelled", attempts, None)
        _job_changed(db, redis, job)
    assert row is not None
    return RetryDecision("queued", attempts, row["run_at"])


def _short(error: BaseException | str) -> str:
    text = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else error
    return text[:_ERROR_LIMIT]


async def _write_failure_event(db: Db, job: ClaimedJob, error: str, redis: Redis | None) -> None:
    """`agent.completed {ok: false, error}` — for a request by `request_id`, for a job by
    `job_key` (Т-§13.9, WP7.4). Same transaction as `failed`; the key is the job's."""
    if job.learner_id is None:
        return  # periodic jobs belong to no learner
    request_id = job.payload.get("request_id")
    trigger = job.payload.get("trigger_seq")
    event = NewEvent(
        type="agent.completed",
        payload={
            "request_id": request_id,
            "job_key": None if request_id else job.job_key,
            "kind": str(job.payload.get("kind") or job.kind),
            "ok": False,
            "error": error,
        },
        actor="system",
        idempotency_key=f"agent_completed:{job.job_key}",
        session_id=job.session_id,
        causation_seq=trigger if isinstance(trigger, int) else None,
    )
    try:
        await append_events(db, job.learner_id, [event], redis=redis)
    except LearnerNotFoundError:
        # The account was deleted under the job: nobody to answer, and the failure must not
        # undo the transition (`reap_stale` handles every stale job in one transaction).
        log.warning("job_failure_of_deleted_learner", job_key=job.job_key)


async def fail_or_retry(
    db: Db,
    job: ClaimedJob,
    error: BaseException | str,
    settings: Settings,
    *,
    permanent: bool = False,
    redis: Redis | None = None,
    rand: Callable[[], float] = random.random,
    retry_delay_s: float | None = None,
) -> RetryDecision | None:
    """A temporary error: back to `queued` after a growing pause while `attempts <
    job_max_attempts`; then `failed` and `agent.completed {ok: false}` in one transaction
    (Т-§13.1, Т-§13.9; no `ops.dlq`). `permanent` — `failed` at once; `retry_delay_s` replaces the
    growing pause. None — the job is not ours any more."""
    message = _short(error)
    async with db.transaction():
        locked = await db.fetch_one(_LOCK_JOB, {"job_key": job.job_key, "attempts": job.attempts})
        if locked is None:
            return None
        if locked["status"] == "cancel_requested":
            await mark_cancelled(db, job, redis=redis)
            return RetryDecision("cancelled", job.attempts, None)
        if permanent or job.attempts >= settings.job_max_attempts:
            await _finish(db, job, "failed", message, redis)
            await _write_failure_event(db, job, message, redis)
            await _run_failure_hook(db, job)
            return RetryDecision("failed", job.attempts, None)
        delay = (
            backoff_seconds(job.attempts, settings, rand=rand)
            if retry_delay_s is None
            else retry_delay_s
        )
        return await _requeue(
            db, job, attempts=job.attempts, delay_s=delay, error=message, redis=redis
        )


async def defer(
    db: Db, job: ClaimedJob, delay_s: float, reason: str, *, redis: Redis | None = None
) -> RetryDecision | None:
    """Run again in `delay_s` without spending the attempt (the handler is not ready: the
    model is under pressure, the daily budget is out)."""
    async with db.transaction():
        locked = await db.fetch_one(_LOCK_JOB, {"job_key": job.job_key, "attempts": job.attempts})
        if locked is None:
            return None
        if locked["status"] == "cancel_requested":
            await mark_cancelled(db, job, redis=redis)
            return RetryDecision("cancelled", job.attempts, None)
        return await _requeue(
            db, job, attempts=job.attempts - 1, delay_s=delay_s, error=_short(reason), redis=redis
        )


async def release(db: Db, job: ClaimedJob, *, redis: Redis | None = None) -> RetryDecision | None:
    """Shutdown after the grace period: back to `queued` at once, the attempt unspent."""
    return await defer(db, job, 0.0, "released on shutdown", redis=redis)


_STALE = (
    f"SELECT {_JOB_COLUMNS}, status FROM ops.jobs "
    "WHERE status IN ('running', 'cancel_requested') "
    "AND heartbeat_at < now() - make_interval(secs => %(stale)s) "
    "ORDER BY job_key FOR UPDATE SKIP LOCKED"
)


async def reap_stale(db: Db, settings: Settings, *, redis: Redis | None = None) -> int:
    """The cleaner (Т-§13.1): a job in work without a pulse for `job_stale_after_sec` returns to
    the queue — or is `failed` when its attempts are out; a stale `cancel_requested` is
    `cancelled`. Returns the number of jobs touched."""
    async with db.transaction() as conn:
        rows = await db.fetch_all(_STALE, {"stale": settings.job_stale_after_sec})
        touched = 0
        for row in rows:
            job = _claimed(row)
            try:
                async with conn.transaction():  # a savepoint: one job does not undo the others
                    if row["status"] == "cancel_requested":
                        await mark_cancelled(db, job, redis=redis)
                    else:
                        await fail_or_retry(
                            db, job, "heartbeat timeout", settings, retry_delay_s=0.0, redis=redis
                        )
            except Exception as exc:  # law 7: a job that cannot be returned stays for the next pass
                log.error("job_reap_failed", job_key=job.job_key, error=repr(exc))
                continue
            touched += 1
        return touched


# --- cancel and the plaque ----------------------------------------------------------------

_CANCEL = (
    "UPDATE ops.jobs SET status = CASE status WHEN 'queued' THEN 'cancelled' "
    "ELSE 'cancel_requested' END WHERE learner_id = %(learner_id)s "
    "AND status IN ('queued', 'running') AND payload->>'request_id' = ANY(%(ids)s::text[]) "
    "RETURNING status, session_id, visible"
)


@dataclass(frozen=True)
class CancelOutcome:
    cancelled: int  # waiting jobs, now `cancelled`
    requested: int  # running jobs, now `cancel_requested`


async def request_cancel(
    db: Db, learner_id: UUID, request_ids: Sequence[UUID], *, redis: Redis | None = None
) -> CancelOutcome:
    """The teacher's `planUpdate` no longer needs these requests (Т-§13.7): a job still in the
    queue is `cancelled`, a running one is `cancel_requested` and stops at its next pulse.
    Only the learner's own jobs (law 1)."""
    if not request_ids:
        return CancelOutcome(0, 0)
    async with db.transaction():
        rows = await db.fetch_all(
            _CANCEL, {"learner_id": learner_id, "ids": [str(i) for i in request_ids]}
        )
        for session_id in {r["session_id"] for r in rows if r["visible"]}:
            _jobs_changed(db, redis, learner_id, session_id, True)
    statuses = [r["status"] for r in rows]
    return CancelOutcome(statuses.count("cancelled"), statuses.count("cancel_requested"))


# How long a finished (`succeeded`/`failed`) visible job stays on the plaque, so the client can
# tell a failed background job from a finished one. The finish moment is the last pulse
# (`heartbeat_at`): `_FINISH` does not clear it.
FINISHED_JOB_WINDOW_SEC = 300


async def visible_jobs(db: Db, learner_id: UUID, session_id: UUID) -> list[VisibleJob]:
    """`GET /v1/sessions/{id}/jobs` (Т-§13.8): jobs of the session with `visible` in `queued`
    and `running`, plus those that `succeeded` or `failed` within `FINISHED_JOB_WINDOW_SEC`;
    the rows of another learner are never seen."""
    rows = await db.fetch_all(
        "SELECT job_key, kind, status, created_at FROM ops.jobs WHERE learner_id = %(learner_id)s "
        "AND session_id = %(session_id)s AND visible AND (status IN ('queued', 'running') "
        "OR (status IN ('succeeded', 'failed') "
        "AND heartbeat_at >= now() - make_interval(secs => %(window)s))) "
        "ORDER BY created_at, job_key",
        {
            "learner_id": learner_id,
            "session_id": session_id,
            "window": FINISHED_JOB_WINDOW_SEC,
        },
    )
    return [VisibleJob(r["job_key"], r["kind"], r["status"], r["created_at"]) for r in rows]
