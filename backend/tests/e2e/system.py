"""The e2e stand `system` (ТЗ Фазы 3 WP10.1, contract of the stand's behaviour).

- `create_app` with all routers over real Postgres, Neo4j and Redis; the LLM is a
  `ScriptedLLMProvider`, compute is `httpx.MockTransport` with scripted verdicts;
- projector, dispatcher, job workers and scheduler run as asyncio tasks in the same event loop
  and stop when the test ends;
- `await system.settle()` waits until the projector (and the dispatcher) caught up with the
  log of every learner and no claimable job is left: a deterministic sync point, not `sleep`;
- `pause_background()` / `resume_background()` stop and start projector and workers (law 9);
- `system.clock` is the replaceable time source of the scheduler.

Wiring points owned by stream A are marked `# S-A:`. Handlers come from `quack.runtime`
(WP2.2) and get one `JobContext` over the stand's resources (WP2.1). Until the dispatcher
(WP11) and the scheduler (WP3.3) land, derived jobs are still enqueued in the event
transaction and there are no periodic jobs; the e2e tests that need them stay red (§7.1).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.app import create_app
from quack.api.resources import AppResources
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.graph.worker import ProjectorWorker
from quack.jobs.periodic import PeriodicScheduler
from quack.jobs.queue import ClaimedJob
from quack.jobs.runner import JobContext, JobHandler, JobRunner
from quack.runtime import register_all_handlers
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources

SETTLE_POLL_SEC = 0.05
SETTLE_STABLE_ROUNDS = 2

# Learners whose projection or dispatch lags, and jobs that can still be claimed now.
_BUSY_SQL = text(
    """
    SELECT
      (SELECT count(*) FROM core.learner_seq s
         LEFT JOIN ops.projector_offsets p ON p.learner_id = s.learner_id
        WHERE s.last_seq > coalesce(p.applied_seq, 0)) AS projector_lagging,
      (SELECT count(*) FROM ops.jobs
        WHERE status = 'running' OR (status = 'queued' AND run_after <= now())) AS jobs_ready
    """
)


@dataclass
class SystemClock:
    """Replaceable time source of the scheduler (`T-Q5`, `T-E10`)."""

    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass(frozen=True)
class JobStart:
    """Stand log: a job handler started while the learner's projection stood at `applied_seq`."""

    job_id: UUID
    kind: str
    learner_id: UUID | None
    applied_seq: int


_APPLIED_SQL = text("SELECT applied_seq FROM ops.projector_offsets WHERE learner_id = :l")


def job_handlers() -> dict[str, JobHandler]:
    """All job handlers, registered as the `workers` process does (WP2.2). The completeness
    check is `T-Q7`'s; here a missing handler leaves its jobs queued and the test that needs
    them fails on its own assertion (S-A: `account.purge_deleted`, WP3.3)."""
    return register_all_handlers()


def job_context(resources: AppResources) -> JobContext:
    """The `workers` resources of §4.2 over the stand's ones: one process, one set (WP2.1)."""
    return JobContext(
        settings=resources.settings,
        session_factory=resources.session_factory,
        engine=resources.engine,
        neo4j=resources.neo4j,
        redis=resources.redis,
        llm=resources.llm,
        compute=resources.compute,
    )


def _logged(handler: JobHandler, log: list[JobStart]) -> JobHandler:
    async def run(job: ClaimedJob, ctx: JobContext) -> None:
        applied = 0
        if job.learner_id is not None:
            async with ctx.session_factory() as session:
                applied = int(await session.scalar(_APPLIED_SQL, {"l": job.learner_id}) or 0)
        log.append(JobStart(job.id, job.kind, job.learner_id, applied))
        await handler(job, ctx)

    return run


@dataclass
class System:
    settings: Settings
    engine: AsyncEngine
    session_factory: SessionFactory
    neo4j: AsyncDriver
    redis: Redis | None
    llm: ScriptedLLMProvider
    compute: ComputeScenario
    clock: SystemClock
    resources: AppResources
    app: FastAPI
    client: httpx.AsyncClient
    job_starts: list[JobStart] = field(default_factory=list)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    # --- background ---------------------------------------------------------------------

    def _spawn(self, factory: Callable[[asyncio.Event], Coroutine[Any, Any, None]]) -> None:
        self._tasks.append(asyncio.create_task(factory(self._stop)))

    def start_background(self) -> None:
        if self._tasks:
            return
        self._stop = asyncio.Event()
        projector = ProjectorWorker(
            self.settings, self.engine, self.session_factory, self.neo4j, self.redis
        )
        self._spawn(projector.run_forever)
        # S-A: dispatcher task in the projector process (WP11.1) starts here.
        handlers = {k: _logged(h, self.job_starts) for k, h in job_handlers().items()}
        runner = JobRunner(
            self.settings,
            self.session_factory,
            f"e2e-{uuid4().hex[:8]}",
            handlers=handlers,
            context=job_context(self.resources),
        )
        self._spawn(runner.run_forever)
        # Scheduler of periodic jobs (WP3.3) on the stand clock (D-C-026).
        scheduler = PeriodicScheduler(self.settings, self.session_factory, clock=self.clock)
        self._spawn(scheduler.run_forever)

    async def advance_time(self, seconds: float) -> None:
        """Time passes for the scheduler and for the queue (D-C-026): the stand clock moves and
        every waiting job comes `seconds` closer, as `JobRunner` claims by the database `now()`."""
        self.clock.advance(seconds)
        async with self.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE ops.jobs SET run_after = run_after - make_interval(secs => :s) "
                    "WHERE status = 'queued'"
                ),
                {"s": seconds},
            )

    async def stop_background(self) -> None:
        self._stop.set()
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def pause_background(self) -> None:
        await self.stop_background()

    def resume_background(self) -> None:
        self.start_background()

    async def settle(self, timeout_s: float = 30.0) -> None:
        """Wait until the log is projected (and dispatched) and no claimable job is left."""
        deadline = time.monotonic() + timeout_s
        stable = 0
        last: tuple[int, int] = (-1, -1)
        while True:
            for task in self._tasks:
                if task.done() and task.exception() is not None:
                    raise AssertionError(f"background task failed: {task.exception()!r}")
            async with self.session_factory() as session:
                row = (await session.execute(_BUSY_SQL)).one()
            last = (int(row.projector_lagging), int(row.jobs_ready))
            # S-A: dispatcher lag (`ops.dispatcher_offsets` vs `core.learner_seq`) joins here.
            stable = stable + 1 if last == (0, 0) else 0
            if stable >= SETTLE_STABLE_ROUNDS:
                return
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"system did not settle in {timeout_s} s: projector lagging learners "
                    f"{last[0]}, claimable jobs {last[1]}"
                )
            await asyncio.sleep(SETTLE_POLL_SEC)

    # --- HTTP helpers -------------------------------------------------------------------

    async def register(self, *, password: str = "correct-horse-battery") -> Learner:
        email = f"e2e-{uuid4().hex[:12]}@example.com"
        response = await self.client.post(
            "/v1/auth/register", json={"email": email, "password": password}
        )
        assert response.status_code == 201, response.text
        body = response.json()
        return Learner(
            UUID(body["learner_id"]), email, password, {"Authorization": f"Bearer {body['token']}"}
        )


@dataclass(frozen=True)
class Learner:
    learner_id: UUID
    email: str
    password: str
    headers: dict[str, str]


async def build_system(
    settings: Settings,
    engine: AsyncEngine,
    session_factory: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis | Any | None,
) -> System:
    llm = ScriptedLLMProvider()
    compute = ComputeScenario()
    resources = build_app_resources(
        settings,
        engine,
        neo4j,
        redis,
        session_factory=session_factory,
        provider=llm,
        compute=scripted_compute(compute),
    )
    app = create_app(settings, resources=resources)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return System(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        neo4j=neo4j,
        redis=redis,
        llm=llm,
        compute=compute,
        clock=SystemClock(),
        resources=resources,
        app=app,
        client=client,
    )
