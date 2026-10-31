"""Job queue on ops.jobs (spec §7, Т-§13.1): enqueue, claim, heartbeat, complete, retry/DLQ.

All functions run inside the caller's transaction; `enqueue_job` can share the transaction
of `append_events` (transactional outbox).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import orjson
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

type JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]

DEFAULT_BACKOFF_BASE_SEC = 5
DEFAULT_BACKOFF_MAX_SEC = 300

_JOB_COLUMNS = (
    "id, kind, learner_id, lock_key, coalesce_key, priority, payload, attempt, max_attempts"
)


class JobDeferred(Exception):
    """Raised by a handler: run again in `delay_sec`, without spending an attempt (WP3.2)."""

    def __init__(self, delay_sec: float, reason: str) -> None:
        super().__init__(reason)
        self.delay_sec = delay_sec
        self.reason = reason


class JobFailedPermanently(Exception):
    """Raised by a handler or the runner: failed, into ops.dlq, attempts are not spent (WP3.4)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class JobSpec:
    kind: str
    lock_key: str
    priority: int
    coalesce_key: str | None = None
    learner_id: UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    max_attempts: int = 5
    run_after: datetime | None = None
    # WP3.1 (Т-§13.1): seq of the trigger event; None = the job does not read the graph.
    required_seq: int | None = None


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    kind: str
    learner_id: UUID | None
    lock_key: str
    coalesce_key: str | None
    priority: int
    payload: dict[str, Any]
    attempt: int
    max_attempts: int


@dataclass(frozen=True)
class RetryDecision:
    status: JobStatus
    attempt: int
    run_after: datetime | None
    dlq_id: UUID | None


def backoff_seconds(
    attempt: int, *, base: int = DEFAULT_BACKOFF_BASE_SEC, cap: int = DEFAULT_BACKOFF_MAX_SEC
) -> int:
    """min(cap, 2 ** attempt * base) (spec §7.2)."""
    return min(cap, 2**attempt * base)


async def enqueue_job(session: AsyncSession, spec: JobSpec) -> UUID:
    """Insert, or coalesce into the queued job with the same coalesce_key."""
    result = await session.execute(
        text(
            "INSERT INTO ops.jobs (id, kind, learner_id, lock_key, coalesce_key, priority, status, "
            "payload, attempt, max_attempts, run_after, created_at, updated_at) VALUES "
            "(:id, :kind, :learner_id, :lock_key, :coalesce_key, :priority, 'queued', "
            "CAST(:payload AS JSONB), 0, :max_attempts, "
            "COALESCE(CAST(:run_after AS TIMESTAMPTZ), now()), clock_timestamp(), now()) "
            "ON CONFLICT (coalesce_key) WHERE status = 'queued' "
            "DO UPDATE SET payload = EXCLUDED.payload, updated_at = now() RETURNING id"
        ),
        {
            "id": uuid4(),
            "kind": spec.kind,
            "learner_id": spec.learner_id,
            "lock_key": spec.lock_key,
            "coalesce_key": spec.coalesce_key,
            "priority": spec.priority,
            "payload": orjson.dumps(spec.payload).decode(),
            "max_attempts": spec.max_attempts,
            "run_after": spec.run_after,
        },
    )
    return result.scalar_one()


async def claim_next_job(
    session: AsyncSession, worker_id: str, kinds: list[str] | None = None
) -> ClaimedJob | None:
    """Take the best queued job whose lock_key has no running job (FOR UPDATE SKIP LOCKED)."""
    sql = text(
        "UPDATE ops.jobs j SET status = 'running', locked_by = :worker, heartbeat_at = now(), "
        "updated_at = now() FROM (SELECT q.id FROM ops.jobs q WHERE q.status = 'queued' "
        "AND q.run_after <= now() "
        "AND (CAST(:kinds AS TEXT[]) IS NULL OR q.kind = ANY(CAST(:kinds AS TEXT[]))) "
        "AND NOT EXISTS (SELECT 1 FROM ops.jobs r WHERE r.lock_key = q.lock_key "
        "AND r.status = 'running') "
        "ORDER BY q.priority, q.created_at, q.id FOR UPDATE SKIP LOCKED LIMIT 1) c "
        f"WHERE j.id = c.id RETURNING {', '.join('j.' + c for c in _JOB_COLUMNS.split(', '))}"
    )
    try:
        async with session.begin_nested():
            row = (await session.execute(sql, {"worker": worker_id, "kinds": kinds})).first()
    except IntegrityError:
        # A concurrent claimer started a job with the same lock_key (uq_jobs_running_lock).
        return None
    if row is None:
        return None
    m = row._mapping
    return ClaimedJob(
        id=m["id"],
        kind=m["kind"],
        learner_id=m["learner_id"],
        lock_key=m["lock_key"],
        coalesce_key=m["coalesce_key"],
        priority=int(m["priority"]),
        payload=dict(m["payload"]),
        attempt=int(m["attempt"]),
        max_attempts=int(m["max_attempts"]),
    )


async def heartbeat_job(session: AsyncSession, job_id: UUID, worker_id: str) -> bool:
    result = await session.execute(
        text(
            "UPDATE ops.jobs SET heartbeat_at = now(), updated_at = now() "
            "WHERE id = :id AND status = 'running' AND locked_by = :worker RETURNING id"
        ),
        {"id": job_id, "worker": worker_id},
    )
    return result.first() is not None


async def complete_job(session: AsyncSession, job_id: UUID, worker_id: str) -> bool:
    result = await session.execute(
        text(
            "UPDATE ops.jobs SET status = 'done', heartbeat_at = now(), updated_at = now() "
            "WHERE id = :id AND status = 'running' AND locked_by = :worker RETURNING id"
        ),
        {"id": job_id, "worker": worker_id},
    )
    return result.first() is not None


def _error_parts(error: BaseException | str) -> tuple[str, str]:
    if isinstance(error, BaseException):
        return type(error).__name__, str(error)
    return "Error", error


async def _retry_or_dlq(
    session: AsyncSession,
    row: Any,
    error: BaseException | str,
    stacktrace: str | None,
    *,
    backoff: bool,
) -> RetryDecision:
    m = row._mapping
    attempt = int(m["attempt"]) + 1
    error_type, message = _error_parts(error)
    last_error = f"{error_type}: {message}"
    if attempt >= int(m["max_attempts"]):
        await session.execute(
            text(
                "UPDATE ops.jobs SET status = 'failed', attempt = :attempt, last_error = :err, "
                "locked_by = NULL, updated_at = now() WHERE id = :id"
            ),
            {"attempt": attempt, "err": last_error, "id": m["id"]},
        )
        dlq_id = uuid4()
        snapshot = {
            "kind": m["kind"],
            "learner_id": str(m["learner_id"]) if m["learner_id"] else None,
            "lock_key": m["lock_key"],
            "coalesce_key": m["coalesce_key"],
            "priority": int(m["priority"]),
            "max_attempts": int(m["max_attempts"]),
            "payload": m["payload"],
        }
        await session.execute(
            text(
                "INSERT INTO ops.dlq (id, source, kind, learner_id, seq, job_id, payload, "
                "error_type, error_message, stacktrace, created_at) VALUES (:id, 'job', :kind, "
                ":learner_id, NULL, :job_id, CAST(:payload AS JSONB), :etype, :emsg, :stack, now())"
            ),
            {
                "id": dlq_id,
                "kind": m["kind"],
                "learner_id": m["learner_id"],
                "job_id": m["id"],
                "payload": orjson.dumps(snapshot).decode(),
                "etype": error_type,
                "emsg": message,
                "stack": stacktrace,
            },
        )
        return RetryDecision("failed", attempt, None, dlq_id)
    delay = backoff_seconds(attempt) if backoff else 0
    try:
        async with session.begin_nested():
            run_after = (
                await session.execute(
                    text(
                        "UPDATE ops.jobs SET status = 'queued', attempt = :attempt, "
                        "last_error = :err, locked_by = NULL, heartbeat_at = NULL, "
                        "run_after = now() + make_interval(secs => :delay), updated_at = now() "
                        "WHERE id = :id RETURNING run_after"
                    ),
                    {"attempt": attempt, "err": last_error, "delay": delay, "id": m["id"]},
                )
            ).scalar_one()
    except IntegrityError:
        # A newer job with the same coalesce_key is already queued: it supersedes this one.
        await session.execute(
            text(
                "UPDATE ops.jobs SET status = 'cancelled', attempt = :attempt, last_error = :err, "
                "locked_by = NULL, updated_at = now() WHERE id = :id"
            ),
            {"attempt": attempt, "err": last_error, "id": m["id"]},
        )
        return RetryDecision("cancelled", attempt, None, None)
    return RetryDecision("queued", attempt, run_after, None)


_SELECT_FOR_UPDATE = text(
    f"SELECT {_JOB_COLUMNS}, status, locked_by FROM ops.jobs WHERE id = :id FOR UPDATE"
)


async def fail_or_retry_job(
    session: AsyncSession,
    job_id: UUID,
    worker_id: str | None,
    error: BaseException | str,
    *,
    stacktrace: str | None = None,
) -> RetryDecision:
    """attempt += 1; requeue with backoff, or mark failed and copy into ops.dlq."""
    row = (await session.execute(_SELECT_FOR_UPDATE, {"id": job_id})).first()
    if row is None:
        raise LookupError(f"job {job_id} not found")
    m = row._mapping
    if m["status"] != "running" or (worker_id is not None and m["locked_by"] != worker_id):
        raise ValueError(f"job {job_id} is not running for worker {worker_id!r}")
    return await _retry_or_dlq(session, row, error, stacktrace, backoff=True)


async def reap_stale_jobs(session: AsyncSession, stale_after_sec: int = 60) -> int:
    """Return running jobs without heartbeat for stale_after_sec to the queue (or DLQ)."""
    rows = (
        await session.execute(
            text(
                f"SELECT {_JOB_COLUMNS}, status, locked_by FROM ops.jobs WHERE status = 'running' "
                "AND heartbeat_at < now() - make_interval(secs => :stale) "
                "ORDER BY id FOR UPDATE SKIP LOCKED"
            ),
            {"stale": stale_after_sec},
        )
    ).all()
    for row in rows:
        await _retry_or_dlq(session, row, "heartbeat timeout", None, backoff=False)
    return len(rows)
