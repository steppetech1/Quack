"""The e2e stand `system` (ТЗ Ф4 WP26.1, WP1.3; contract of the stand's behaviour).

- `create_app` with all routers over real Postgres (the database of Phase 4, `pg_db`), Neo4j and
  Redis; the LLM is a `ScriptedLLMProvider`, compute is `httpx.MockTransport` with scripted
  verdicts;
- the processes of Phase 4 run as asyncio tasks in the same event loop and stop when the test
  ends: `quack.runtime.phase4.projector_process` (the projector `rules@3` and the dispatcher) and
  `workers_process` (the executor of the queue of Phase 4 and the tick of the periodic jobs) over
  one `JobContext` built from the stand's resources;
- `await system.settle()` waits until the projector and the dispatcher caught up with the log of
  every learner and no claimable job is left: a deterministic sync point, not `sleep`;
- `pause_background()` / `resume_background()` stop and start the processes (law 9).

Handlers come from `quack.runtime` (WP2.2). The stand has no Phase 3 database: the learner of
the stand is a `core.learners` row and a session of Phase 4.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.api.app import create_app
from quack.api.auth import SESSION_COOKIE
from quack.api.resources import AppResources
from quack.auth.sessions import open_session
from quack.config import Settings
from quack.db import Db
from quack.jobs.runner import JobContext, JobHandler
from quack.jobs.types import ClaimedJob
from quack.runtime import register_all_handlers
from quack.runtime.phase4 import projector_process, workers_process
from tests.canvas.steps_support import SPACE
from tests.db.fixtures import create_learner
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources, build_job_context

SETTLE_POLL_SEC = 0.05
SETTLE_STABLE_ROUNDS = 2

# Learners whose projection or dispatch lags, and jobs that can still be claimed now.
_BUSY_SQL = """
    SELECT
      (SELECT count(*) FROM core.learners l
         LEFT JOIN ops.projector_offsets p ON p.learner_id = l.learner_id
        WHERE l.seq > coalesce(p.seq, 0)) AS projector_lagging,
      (SELECT count(*) FROM core.learners l
         LEFT JOIN ops.dispatcher_offsets d ON d.learner_id = l.learner_id
        WHERE l.seq > coalesce(d.last_dispatched_seq, 0)) AS dispatcher_lagging,
      (SELECT count(*) FROM ops.jobs
        WHERE kind = ANY(%(kinds)s)
          AND (status = 'running' OR (status = 'queued' AND run_at <= now()))) AS jobs_ready
"""

_READY_JOBS_SQL = (
    "SELECT kind, status, attempts, last_error FROM ops.jobs "
    "WHERE kind = ANY(%(kinds)s) "
    "AND (status = 'running' OR (status = 'queued' AND run_at <= now()))"
)

QUIET_ANALYST = {
    "frontier": [],
    "gap_hypotheses": [],
    "uncertainty": None,
    "plan_divergence": False,
    "moves": [
        {"move": "continue", "reason": "nothing to change"},
        {"move": "check", "reason": "nothing to change"},
    ],
    "forecast": [],
    "manner": "calm",
    "text": "Nothing new.",
}

# The loops poll the log and the queue this often (production: `consumer_poll_ms` = 500).
STAND_POLL_MS = 100


@dataclass(frozen=True)
class JobStart:
    """Stand log: a job handler started while the learner's projection stood at `applied_seq`."""

    job_id: UUID
    kind: str
    learner_id: UUID | None
    applied_seq: int


_APPLIED_SQL = "SELECT seq FROM ops.projector_offsets WHERE learner_id = %(learner_id)s"


def job_handlers() -> dict[str, JobHandler]:
    """All job handlers, registered as the `workers` process does (WP2.2). The completeness
    check is `T-Q7`'s; here a missing handler leaves its jobs queued and the test that needs
    them fails on its own assertion."""
    return register_all_handlers()


def job_context(resources: AppResources) -> JobContext:
    """The `workers` resources of §4.2 over the stand's ones: one process, one set (WP2.1)."""
    return build_job_context(
        resources.settings,
        resources.neo4j,
        resources.redis,
        db=resources.db,
        llm=resources.llm,
        compute=resources.compute,
        storage=resources.storage,
        embeddings=resources.embeddings,
        deletions=resources.deletions,
    )


def _logged(handler: JobHandler, log: list[JobStart]) -> JobHandler:
    async def run(job: ClaimedJob, ctx: JobContext) -> None:
        applied = 0
        if job.learner_id is not None:
            row = await ctx.db.fetch_one(_APPLIED_SQL, {"learner_id": job.learner_id})
            applied = 0 if row is None else int(row["seq"])
        log.append(JobStart(job.id, job.kind, job.learner_id, applied))
        await handler(job, ctx)

    return run


@dataclass
class System:
    settings: Settings
    neo4j: AsyncDriver
    redis: Redis | None
    llm: ScriptedLLMProvider
    compute: ComputeScenario
    resources: AppResources
    app: FastAPI
    client: httpx.AsyncClient
    job_starts: list[JobStart] = field(default_factory=list)
    # Kinds of jobs the workers have a handler for; `settle` waits for these only: a job of a
    # kind nobody handles yet (`canvas.rediagnose`, stream A) stays queued and is not a lag.
    handled_kinds: list[str] = field(default_factory=lambda: sorted(job_handlers()))
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _tasks: list[asyncio.Task[int]] = field(default_factory=list)

    # --- background ---------------------------------------------------------------------

    def _spawn(self, factory: Callable[[asyncio.Event], Coroutine[Any, Any, int]]) -> None:
        self._tasks.append(asyncio.create_task(factory(self._stop)))

    def start_background(self) -> None:
        if self._tasks:
            return
        self._stop = asyncio.Event()
        # The projector and the dispatcher of the log (WP11.1), as `quack.cli projector` runs them;
        # the process opens its own `Db`, Neo4j and Redis from the settings of the stand.
        self._spawn(lambda stop: projector_process(self.settings, stop))
        handlers = {k: _logged(h, self.job_starts) for k, h in job_handlers().items()}
        ctx = job_context(self.resources)
        self._spawn(lambda stop: workers_process(self.settings, stop, handlers=handlers, ctx=ctx))

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
        """Wait until the log is projected and dispatched and no claimable job of a handled kind
        is left."""
        deadline = time.monotonic() + timeout_s
        stable = 0
        last: tuple[int, int, int] = (-1, -1, -1)
        while True:
            for task in self._tasks:
                if task.done() and (task.exception() is not None or task.result() != 0):
                    raise AssertionError(
                        f"background task failed: {task.exception() or task.result()!r}"
                    )
            row = await self.resources.db.fetch_one(_BUSY_SQL, {"kinds": self.handled_kinds})
            assert row is not None
            last = (
                int(row["projector_lagging"]),
                int(row["dispatcher_lagging"]),
                int(row["jobs_ready"]),
            )
            stable = stable + 1 if last == (0, 0, 0) else 0
            if stable >= SETTLE_STABLE_ROUNDS:
                return
            if time.monotonic() > deadline:
                ready = await self.resources.db.fetch_all(
                    _READY_JOBS_SQL, {"kinds": self.handled_kinds}
                )
                raise AssertionError(
                    f"system did not settle in {timeout_s} s: projector lagging learners "
                    f"{last[0]}, dispatcher lagging learners {last[1]}, claimable jobs {last[2]}: "
                    f"{[(r['kind'], r['status'], r['attempts'], r['last_error']) for r in ready]}"
                )
            await asyncio.sleep(SETTLE_POLL_SEC)

    # --- HTTP helpers -------------------------------------------------------------------

    async def register(self) -> Learner:
        """A new learner signed in with a Phase 4 session (ТЗ Ф4 WP18.3, D-A-113).
        `/v1/auth/register` is removed: the account is the `core.learners` row."""
        email = f"e2e-{uuid4().hex[:12]}@example.com"
        learner_id = await create_learner(self.resources.db, email=email)
        cookie = await open_session(
            self.resources.db, self.settings, learner_id=learner_id, now=datetime.now(UTC)
        )
        return await self._accept_terms(learner_id, email, {"Cookie": f"{SESSION_COOKIE}={cookie}"})

    async def sign_in_by_link(self, email: str | None = None) -> Learner:
        """The way in of T-E1: `POST /auth/link`, the link of the mail, `GET /auth/link/verify`
        (the session cookie comes back in `set-cookie`), then the terms. `email` signs in an
        address again (T-E12: the same address after the account was deleted)."""
        email = email or f"e2e-{uuid4().hex[:12]}@example.com"
        sent = await self.client.post("/auth/link", json={"email": email})
        assert sent.status_code == 200, sent.text
        token = parse_qs(urlsplit(self.resources.email.last_link(email)).query)["token"][0]  # type: ignore[attr-defined]
        verified = await self.client.get("/auth/link/verify", params={"token": token})
        # Without `auth_success_redirect_url` the verification answers 200 with the learner.
        assert verified.status_code == 200, verified.text
        found = re.search(rf"{SESSION_COOKIE}=([^;]+)", verified.headers["set-cookie"])
        assert found is not None
        return await self._accept_terms(
            UUID(verified.json()["learner_id"]),
            email,
            {"Cookie": f"{SESSION_COOKIE}={found.group(1)}"},
        )

    async def _accept_terms(self, learner_id: UUID, email: str, headers: dict[str, str]) -> Learner:
        # WP18.4: a session starts only after `consent.given {kind: terms}` of the current
        # `terms_version`; the learner of the stand accepts it as the client would.
        consent = await self.client.post(
            "/v1/consents",
            json={
                "kind": "terms",
                "version": self.settings.terms_version,
                "idempotency_key": f"terms:{learner_id}",
            },
            headers=headers,
        )
        assert consent.status_code == 200, consent.text
        return Learner(learner_id, email, headers)


# A test value of `terms_version` (the real one is the owner's, D-C-105).
E2E_TERMS_VERSION = "e2e-terms"


@dataclass(frozen=True)
class Learner:
    learner_id: UUID
    email: str
    headers: dict[str, str]


async def build_system(
    settings: Settings,
    neo4j: AsyncDriver,
    redis: Redis | Any | None,
    db: Db,
    p4_dsn: str,
    **resources: Any,
) -> System:
    """The stand over `db` (`pg_db`); `p4_dsn` is its address, which the processes of Phase 4 open
    for themselves."""
    # D-C-105: `terms_version` is the owner's value and is unset by default; the stand needs
    # one so that its learners can accept the terms (WP18.4).
    settings = settings.model_copy(
        update={
            "postgres_dsn": p4_dsn,
            "consumer_poll_ms": STAND_POLL_MS,
            "terms_version": settings.terms_version or E2E_TERMS_VERSION,
        }
    )
    llm = ScriptedLLMProvider()
    # A task of the bank gets its solution space ahead in the background (`task.solution_space`,
    # WP12.4): the walk creates such tasks, and the job must succeed, not retry.
    llm.respond("SolutionSpace", lambda _request: SPACE.model_dump(mode="json"))
    # The jobs after a session (the analyst, the notes of the topic) must succeed, not retry.
    llm.respond("AnalystOutput", lambda _request: QUIET_ANALYST)
    llm.respond("ArtifactWriterOutput", lambda _request: {"blocks": [], "cards": []})
    compute = ComputeScenario()
    resources = build_app_resources(
        settings,
        neo4j,
        redis,
        db=db,
        provider=llm,
        compute=scripted_compute(compute),
        **resources,
    )
    app = create_app(settings, resources=resources)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return System(
        settings=settings,
        neo4j=neo4j,
        redis=redis,
        llm=llm,
        compute=compute,
        resources=resources,
        app=app,
        client=client,
    )
