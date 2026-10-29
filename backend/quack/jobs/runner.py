"""Job executor: claims jobs of registered kinds, keeps the heartbeat, completes or retries
(spec §7, Т-§13.1). Phase 2 streams register handlers with `register_job_handler`."""

from __future__ import annotations

import asyncio
import contextlib
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.jobs.queue import (
    ClaimedJob,
    claim_next_job,
    complete_job,
    fail_or_retry_job,
    heartbeat_job,
    reap_stale_jobs,
)
from quack.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class JobContext:
    settings: Settings
    session_factory: SessionFactory


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
    handlers: dict[str, JobHandler] = field(default_factory=lambda: JOB_HANDLERS)

    async def _heartbeat(self, job: ClaimedJob) -> None:
        while True:
            await asyncio.sleep(self.settings.job_heartbeat_interval_sec)
            async with self.session_factory() as session, session.begin():
                if not await heartbeat_job(session, job.id, self.worker_id):
                    return

    async def run_one(self) -> bool:
        """Claim and execute one job; False when nothing was claimable."""
        if not self.handlers:
            return False
        async with self.session_factory() as session, session.begin():
            job = await claim_next_job(session, self.worker_id, sorted(self.handlers))
        if job is None:
            return False
        context = JobContext(self.settings, self.session_factory)
        beat = asyncio.create_task(self._heartbeat(job))
        try:
            await self.handlers[job.kind](job, context)
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
