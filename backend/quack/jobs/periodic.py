"""Periodic jobs (ТЗ Фазы 3 WP3.3): the scheduler of `workers` and `account.purge_deleted`."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.jobs.queue import ClaimedJob
from quack.jobs.runner import JobContext


class PeriodicScheduler:
    """Every `scheduler_tick_s` enqueues `session.sweep_inactive` and `account.purge_deleted`
    with `run_after` at the start of the next time bucket (WP3.3)."""

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
        raise NotImplementedError("WP3.3: stage III")

    async def run_forever(self, stop: asyncio.Event) -> None:
        raise NotImplementedError("WP3.3: stage III")


async def handle_purge_deleted(job: ClaimedJob, ctx: JobContext) -> None:
    raise NotImplementedError("WP3.3: stage III")
