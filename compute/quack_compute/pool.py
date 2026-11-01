"""Pool of heavy jobs: `pebble.ProcessPool` with a hard timeout per job (Т-§1.2, ТЗ Фазы 4 WP24).

Seam S10: endpoints run their CPU work through `TaskPool.run`; a job that outlives its timeout
is killed with its process and the pool starts a new one, so the next request is served
(`T-CM1`). A job runs in the main thread of a worker process, so `SIGALRM` budgets inside a
job (WP11.5) work.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from pebble import ProcessPool


class JobTimeout(Exception):
    """The job ran out of its hard timeout; its process was killed."""


class TaskPool:
    def __init__(self, workers: int) -> None:
        self._workers = workers
        self._pool: ProcessPool | None = None

    def start(self) -> None:
        if self._pool is None:
            self._pool = ProcessPool(max_workers=self._workers)

    async def run[R](self, fn: Callable[..., R], *args: Any, timeout_s: float) -> R:
        """Run `fn(*args)` in a worker process; `JobTimeout` after `timeout_s`."""
        if self._pool is None:
            raise RuntimeError("TaskPool is not started")
        future = self._pool.schedule(fn, args=args, timeout=timeout_s)
        try:
            return await asyncio.wrap_future(future)
        except FutureTimeout as exc:
            raise JobTimeout(f"job exceeded {timeout_s} s") from exc

    def stop(self) -> None:
        """Stop without waiting for running jobs (they are killed)."""
        if self._pool is not None:
            self._pool.stop()
            self._pool.join()
            self._pool = None
