"""Job executor: claims jobs of registered kinds, keeps the heartbeat, completes or retries
(spec §7, Т-§13.1). Phase 2 streams register handlers with `register_job_handler`."""

from __future__ import annotations

import asyncio
import contextlib
import random
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.jobs.queue import (
    ClaimedJob,
    JobDeferred,
    JobFailedPermanently,
    claim_next_job,
    complete_job,
    defer_job,
    fail_job_permanently,
    fail_or_retry_job,
    heartbeat_job,
    reap_stale_jobs,
)
from quack.llm.errors import LLMBudgetExceededError
from quack.llm.gateway import LLMGateway
from quack.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class JobContext:
    """Per-process resources of `workers` (ТЗ Фазы 3 §4.2, seam S1).

    The field set is a contract. Handlers take the heavy objects only from here and never
    create them. `redis = None` means work without Redis (§4.2). Tests build the context with
    `tests.support.resources.build_job_context`.
    """

    settings: Settings
    session_factory: SessionFactory
    engine: AsyncEngine
    neo4j: AsyncDriver
    redis: Redis | None
    llm: LLMGateway
    compute: ComputeClient


type JobHandler = Callable[[ClaimedJob, JobContext], Awaitable[None]]

JOB_HANDLERS: dict[str, JobHandler] = {}


def register_job_handler(
    kind: str, *, registry: dict[str, JobHandler] | None = None
) -> Callable[[JobHandler], JobHandler]:
    target = JOB_HANDLERS if registry is None else registry

    def decorator(handler: JobHandler) -> JobHandler:
        if kind in target:
            raise ValueError(f"handler for job kind {kind!r} is already registered")
        target[kind] = handler
        return handler

    return decorator


@dataclass
class JobRunner:
    settings: Settings
    session_factory: SessionFactory
    worker_id: str
    # The process-wide resources handed to every handler (WP2.1).
    context: JobContext
    handlers: dict[str, JobHandler] = field(default_factory=lambda: JOB_HANDLERS)

    async def _heartbeat(self, job: ClaimedJob) -> None:
        while True:
            await asyncio.sleep(self.settings.job_heartbeat_interval_sec)
            async with self.session_factory() as session, session.begin():
                if not await heartbeat_job(session, job.id, self.worker_id):
                    return

    def _budget_outcome(self, exc: LLMBudgetExceededError) -> JobDeferred | JobFailedPermanently:
        """WP3.4: the session budget does not recover, the daily one does at UTC midnight."""
        if exc.scope == "session":
            return JobFailedPermanently(str(exc))
        now = datetime.now(UTC)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        jitter = random.uniform(0, self.settings.daily_budget_defer_jitter_s)
        return JobDeferred((midnight - now).total_seconds() + jitter, str(exc))

    async def run_one(self) -> bool:
        """Claim and execute one job; False when nothing was claimable."""
        if not self.handlers:
            return False
        async with self.session_factory() as session, session.begin():
            job = await claim_next_job(session, self.worker_id, sorted(self.handlers))
        if job is None:
            return False
        context = self.context
        beat = asyncio.create_task(self._heartbeat(job))
        try:
            try:
                await self.handlers[job.kind](job, context)
            except LLMBudgetExceededError as exc:
                raise self._budget_outcome(exc) from exc
        except JobDeferred as exc:
            log.info("job_deferred", kind=job.kind, job_id=str(job.id), reason=exc.reason)
            async with self.session_factory() as session, session.begin():
                await defer_job(session, job.id, self.worker_id, exc.delay_sec, exc.reason)
        except JobFailedPermanently as exc:
            log.warning(
                "job_failed_permanently", kind=job.kind, job_id=str(job.id), reason=exc.reason
            )
            async with self.session_factory() as session, session.begin():
                await fail_job_permanently(
                    session, job.id, self.worker_id, exc.reason, stacktrace=traceback.format_exc()
                )
        except Exception as exc:
            log.warning("job_failed", kind=job.kind, job_id=str(job.id), error=repr(exc))
            async with self.session_factory() as session, session.begin():
                await fail_or_retry_job(
                    session, job.id, self.worker_id, exc, stacktrace=traceback.format_exc()
                )
        else:
            async with self.session_factory() as session, session.begin():
                await complete_job(session, job.id, self.worker_id)
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
        return True

    async def _loop(self, stop: asyncio.Event) -> None:
        idle = self.settings.consumer_poll_ms / 1000
        while not stop.is_set():
            try:
                worked = await self.run_one()
            except Exception as exc:
                log.error("job_runner_error", error=repr(exc))
                worked = False
            if not worked:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=idle)

    async def _reaper(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with self.session_factory() as session, session.begin():
                    await reap_stale_jobs(session, self.settings.job_stale_after_sec)
            except Exception as exc:
                log.error("job_reaper_error", error=repr(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.settings.job_stale_after_sec / 2)

    async def run_forever(self, stop: asyncio.Event) -> None:
        loops = [self._loop(stop) for _ in range(max(1, self.settings.worker_concurrency))]
        await asyncio.gather(self._reaper(stop), *loops)
