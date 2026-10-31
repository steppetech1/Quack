"""Periodic jobs (ТЗ Фазы 3 WP3.3, Т-§13.1): the scheduler of `workers`. The handler of
`account.purge_deleted` is `quack.ops.purge_job` (assembly layer, §4.1).

A periodic job is a job whose `coalesce_key` and `run_after` are tied to time: `run_after` is
the start of the NEXT bucket `(floor(now / interval) + 1) * interval`. Coalescing keeps one
waiting job, `lock_key` one running job, so with any number of `workers` processes the job runs
at most once per bucket.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.jobs import kinds
from quack.jobs.queue import JobSpec, enqueue_job
from quack.logging import get_logger

log = get_logger(__name__)

# (factory of the job, name of the `Settings` parameter with its interval in seconds)
PERIODIC_JOBS: tuple[tuple[Callable[[], JobSpec], str], ...] = (
    (kinds.session_sweep_inactive, "session_sweep_interval_s"),
    (kinds.account_purge_deleted, "purge_deleted_interval_s"),
)


def next_bucket_start(now: datetime, interval_s: int) -> datetime:
    """Start of the bucket after the one that contains `now`."""
    return datetime.fromtimestamp((int(now.timestamp()) // interval_s + 1) * interval_s, tz=UTC)


class PeriodicScheduler:
    """Every `scheduler_tick_s` enqueues the periodic jobs; `clock` is replaceable for tests."""

    def __init__(
        self,
        settings: Settings,
        session_factory: SessionFactory,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.clock = clock

    async def tick(self) -> None:
        now = self.clock()
        async with self.session_factory() as session, session.begin():
            for factory, interval_name in PERIODIC_JOBS:
                interval = int(getattr(self.settings, interval_name))
                spec = replace(factory(), run_after=next_bucket_start(now, interval))
                await enqueue_job(session, spec)

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.tick()
            except Exception as exc:
                log.warning("periodic_schedule_failed", error=repr(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.settings.scheduler_tick_s)
