"""Executor of Phase 4: runs a claimed job, keeps its pulse, sees a cancel (ТЗ Ф4 WP7.4;
`Т-§13.1`, `Т-§13.2`, `Т-§13.7`, `Т-§13.9`).

`run_claimed` is one job: the handler runs as an `asyncio` task, the pulse goes every
`job_heartbeat_interval_sec`. The answer of the pulse is the channel of the cancel:
`cancel_requested`
cancels the task and the job ends `cancelled` (T-D6); no answer — the cleaner has taken the job
away — also cancels the task and writes nothing. The end of the handler decides the status:
`succeeded`; `JobDeferred` — again later, the attempt unspent; `JobFailedPermanently` — `failed`;
any other exception — a retry with a growing pause while attempts remain, then `failed` and
`agent.completed {ok: false}` (T-D8). The cancellation of the executor itself (shutdown after the
grace period) returns the job to the queue with the attempt unspent (WP3.5).

`JobWorker` is the loop: admission by the pressure flags of the models of each kind's roles
(`Т-§13.2`), `claim_next`, `run_claimed`, the cleaner every half of `job_stale_after_sec`. The
handlers are the same registry as in Phase 3 (`quack.jobs.runner.JOB_HANDLERS`, D-A-116).
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.jobs import queue_p4 as q
from quack.jobs.rules_p4 import KIND_LLM_ROLES
from quack.jobs.types import JobDeferred, JobFailedPermanently
from quack.llm.errors import LLMBudgetExceededError
from quack.logging import get_logger

log = get_logger(__name__)

type RunResult = Literal["succeeded", "cancelled", "deferred", "retried", "failed", "lost"]
type Work = Callable[[], Awaitable[None]]


async def run_claimed(
    db: Db, settings: Settings, job: q.ClaimedJob, work: Work, *, redis: Redis | None = None
) -> RunResult:
    task = asyncio.ensure_future(work())
    interval = settings.job_heartbeat_interval_sec
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if done:
                break
            try:
                status = await q.heartbeat(db, job)
            except Exception as exc:
                # Law 7/9: a blip of the database at the pulse does not orphan the handler; the
                # next pulse tries again, and if the database stays away the cleaner decides.
                log.warning(
                    "job_heartbeat_failed", kind=job.kind, job_key=job.job_key, error=repr(exc)
                )
                continue
            if status is None:
                await _stop(task)
                log.warning("job_lost", kind=job.kind, job_key=job.job_key)
                return "lost"
            if status == "cancel_requested":
                await _stop(task)
                await q.mark_cancelled(db, job, redis=redis)
                log.info("job_cancelled", kind=job.kind, job_key=job.job_key)
                return "cancelled"
    except asyncio.CancelledError:
        await _stop(task)
        await q.release(db, job, redis=redis)
        log.info("job_released_on_shutdown", kind=job.kind, job_key=job.job_key)
        raise
    return await _finish(db, settings, job, task, redis)


async def _stop(task: asyncio.Future[None]) -> None:
    task.cancel()
    with contextlib.suppress(BaseException):
        await task


def budget_outcome(
    exc: LLMBudgetExceededError, settings: Settings, now: datetime | None = None
) -> JobDeferred | JobFailedPermanently:
    """WP3.4 (seam S3): a session budget does not recover — the job fails without retries; the
    daily budget and the closed pool recover at the next UTC midnight — the job waits for it
    (plus a jitter, so that the day's jobs do not return in one second)."""
    if exc.scope == "session":
        return JobFailedPermanently(str(exc))
    now = now or datetime.now(UTC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    jitter = random.uniform(0, settings.daily_budget_defer_jitter_s)
    return JobDeferred((midnight - now).total_seconds() + jitter, str(exc))


async def _finish(
    db: Db, settings: Settings, job: q.ClaimedJob, task: asyncio.Future[None], redis: Redis | None
) -> RunResult:
    error: BaseException | None
    try:
        error = task.exception()
    except asyncio.CancelledError:  # the handler cancelled itself: an error of the job
        error = RuntimeError("the handler was cancelled")
    if error is None:
        await q.complete(db, job, redis=redis)
        return "succeeded"
    if isinstance(error, LLMBudgetExceededError):
        error = budget_outcome(error, settings)  # seam S3, WP3.4 (D-A-233)
    if isinstance(error, JobDeferred):
        log.info("job_deferred", kind=job.kind, job_key=job.job_key, reason=error.reason)
        decision = await q.defer(db, job, error.delay_sec, error.reason, redis=redis)
        return "lost" if decision is None else "deferred"
    permanent = isinstance(error, JobFailedPermanently)
    reason = error.reason if isinstance(error, JobFailedPermanently) else error
    log.warning("job_failed", kind=job.kind, job_key=job.job_key, error=repr(error))
    decision = await q.fail_or_retry(db, job, reason, settings, permanent=permanent, redis=redis)
    if decision is None:
        return "lost"
    if decision.status == "queued":
        return "retried"
    return "cancelled" if decision.status == "cancelled" else "failed"


type Admission = Callable[[], Awaitable[Sequence[str]]]
type Handler = Callable[[q.ClaimedJob], Awaitable[None]]


@dataclass
class JobWorker:
    """The executor of `workers` on `Db` (`handlers` take the job; the caller binds the
    context: `lambda job: handler(job, ctx)`)."""

    settings: Settings
    db: Db
    handlers: dict[str, Handler]
    redis: Redis | None = None
    admission: Admission | None = None

    async def claimable_kinds(self) -> list[str]:
        """Kinds with a handler whose roles' models are not under pressure (`Т-§13.2`)."""
        kinds = sorted(self.handlers)
        if self.admission is not None:
            allowed = set(await self.admission())
            return [k for k in kinds if k in allowed]
        from quack.llm.pressure import pressured_models  # the flags of Phase 3 (WP5.3)

        pressured = await pressured_models(self.redis)
        if not pressured:
            return kinds
        return [
            kind
            for kind in kinds
            if not any(
                self.settings.model_for_role(role) in pressured
                for role in KIND_LLM_ROLES.get(kind, ())
            )
        ]

    async def run_one(self) -> bool:
        """Claim and run one job; False when nothing was claimable."""
        kinds = await self.claimable_kinds()
        if not kinds:
            return False
        job = await q.claim_next(self.db, self.settings, kinds=kinds, redis=self.redis)
        if job is None:
            return False
        handler = self.handlers[job.kind]
        await run_claimed(self.db, self.settings, job, lambda: handler(job), redis=self.redis)
        return True

    async def _loop(self, stop: asyncio.Event) -> None:
        idle = self.settings.consumer_poll_ms / 1000
        while not stop.is_set():
            try:
                worked = await self.run_one()
            except Exception as exc:
                log.error("job_worker_error", error=repr(exc))
                worked = False
            if not worked:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=idle)

    async def _cleaner(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await q.reap_stale(self.db, self.settings, redis=self.redis)
            except Exception as exc:
                log.error("job_cleaner_error", error=repr(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.settings.job_stale_after_sec / 2)

    async def run_forever(self, stop: asyncio.Event) -> None:
        """On `stop` no new job is taken; running ones get `worker_shutdown_grace_s`, the rest
        are cancelled and returned to the queue (WP3.5)."""
        loops = [
            asyncio.create_task(self._loop(stop))
            for _ in range(max(1, self.settings.worker_concurrency))
        ]
        cleaner = asyncio.create_task(self._cleaner(stop))
        try:
            await stop.wait()
            _, pending = await asyncio.wait(loops, timeout=self.settings.worker_shutdown_grace_s)
            if pending:
                log.info("worker_shutdown_grace_expired", unfinished=len(pending))
        finally:
            for task in (*loops, cleaner):
                task.cancel()
            await asyncio.gather(*loops, cleaner, return_exceptions=True)
