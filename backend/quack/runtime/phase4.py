"""The processes of Phase 4 on `Db` (ТЗ Ф4 WP1.3; Т-§2.4, Т-§13.1–13.3, Т-§22.4).

`projector` — the loop of `rules@3` (`ProjectorP4`) and the dispatcher (`DispatcherP4`), two
supervised tasks of one process (WP11.1, as in Phase 3). `workers` — the executor on the queue of
Phase 4 (`JobWorker`) with the handlers of every stream, and the tick of the periodic jobs of
WP7.5. The handlers keep their registration of Phase 3 (`@register_job_handler`, D-A-116): the
adapter gives each the job in the shape it reads and the context of the process (§4.2).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from quack.config import Settings
from quack.db import open_db
from quack.db.neo4j import create_driver, verify_schema
from quack.db.redis import create_redis
from quack.graph.projection.process import ProjectorP4
from quack.jobs import queue_p4
from quack.jobs.dispatcher_p4 import DispatcherP4
from quack.jobs.executor_p4 import JobWorker
from quack.jobs.periodic_p4 import schedule_periodic
from quack.jobs.runner import JobContext, JobHandler
from quack.jobs.types import ClaimedJob as Phase3Job
from quack.logging import get_logger

log = get_logger(__name__)

type Bound = Callable[[queue_p4.ClaimedJob], Awaitable[None]]

# Т-§13.1 classes as the numeric priority the handlers of Phase 3 may read (P0 first).
_PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


async def supervise(
    name: str,
    task: Callable[[asyncio.Event], Awaitable[None]],
    stop: asyncio.Event,
    *,
    restart_pause_s: float,
) -> None:
    """WP11.1: run `task(stop)` until `stop`; an exception is logged and the task restarts after
    `restart_pause_s`, so a crash of the dispatcher does not stop the projector and back."""
    while not stop.is_set():
        try:
            await task(stop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("supervised_task_crashed", task=name, error=repr(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=restart_pause_s)
        else:
            return


def as_phase3_job(job: queue_p4.ClaimedJob, settings: Settings) -> Phase3Job:
    """The job of the Phase 4 queue in the shape the registered handlers read (D-A-116): the id
    is derived from the `job_key`, the attempt is the claim's."""
    return Phase3Job(
        id=uuid5(NAMESPACE_URL, f"quack:job:{job.job_key}"),
        kind=job.kind,
        learner_id=job.learner_id,
        lock_key=job.lock_key or job.job_key,
        coalesce_key=job.coalesce_key,
        priority=_PRIORITY.get(job.priority_class, 3),
        payload=dict(job.payload),
        attempt=job.attempts,
        max_attempts=settings.job_max_attempts,
        required_seq=job.required_seq,
        job_key=job.job_key,
        session_id=job.session_id,
    )


def bind_handlers(handlers: dict[str, JobHandler], ctx: JobContext) -> dict[str, Bound]:
    """`kind -> handler(job)` for `JobWorker`, each with the context of the process."""

    def bind(handler: JobHandler) -> Bound:
        async def run(job: queue_p4.ClaimedJob) -> None:
            await handler(as_phase3_job(job, ctx.settings), ctx)

        return run

    return {kind: bind(handler) for kind, handler in handlers.items()}


async def periodic_tick(ctx: JobContext, stop: asyncio.Event) -> None:
    """WP7.5: every `scheduler_tick_s` the jobs of the current periods (one per period)."""
    while not stop.is_set():
        await schedule_periodic(
            ctx.db,
            datetime.now(UTC),
            redis=ctx.redis,
            sweep_interval_s=ctx.settings.session_sweep_interval_s,
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=ctx.settings.scheduler_tick_s)


async def projector_process(settings: Settings, stop: asyncio.Event) -> int:
    """`projector` of Phase 4: the Neo4j schema, then the projector and the dispatcher."""
    db = await open_db(settings)
    neo4j = create_driver(settings)
    redis = create_redis(settings)
    try:
        missing = await verify_schema(neo4j)
        if missing:
            log.error("neo4j_schema_missing", missing=missing)
            return 1
        projector = ProjectorP4(settings, db, neo4j, redis)
        dispatcher = DispatcherP4(settings, db, redis)
        pause = settings.dispatcher_restart_pause_s
        log.info("projector_started", rules="rules@3")
        await asyncio.gather(
            supervise("projector", projector.run_forever, stop, restart_pause_s=pause),
            supervise("dispatcher", dispatcher.run_forever, stop, restart_pause_s=pause),
        )
        return 0
    finally:
        await redis.aclose()
        await neo4j.close()
        await db.close()


async def workers_process(
    settings: Settings,
    stop: asyncio.Event,
    *,
    handlers: dict[str, JobHandler],
    ctx: JobContext,
) -> int:
    """`workers` of Phase 4 on the resources `ctx` of the caller (built and closed there)."""
    worker = JobWorker(settings, ctx.db, bind_handlers(handlers, ctx), redis=ctx.redis)
    pause = settings.dispatcher_restart_pause_s
    log.info("workers_started", queue="phase4", kinds=len(handlers))
    await asyncio.gather(
        supervise("job_worker", worker.run_forever, stop, restart_pause_s=pause),
        supervise("periodic", lambda s: periodic_tick(ctx, s), stop, restart_pause_s=pause),
    )
    return 0
