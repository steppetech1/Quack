"""Handlers of the jobs on the queue of Phase 4 (ТЗ Ф4 WP1.3, D-A-116, D-A-200, D-A-230): the
registry `@register_job_handler` and the adapter `bind_handlers`, which gives a registered handler
the job in the shape it reads and the context of the process. The projector loop and the rebuild
are in `tests/graph/test_projection_runner.py`, the executor in `tests/jobs/test_executor_p4.py`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest

from quack.config import Settings
from quack.db import Db
from quack.jobs import queue_p4 as q
from quack.jobs.executor_p4 import JobWorker
from quack.jobs.rules_p4 import JobPlan
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.runtime.phase4 import as_phase3_job, bind_handlers
from tests.db.fixtures import create_learner


async def test_bound_handlers_run_on_the_worker_and_a_failure_is_retried(
    pg_db: Db, settings: Settings
) -> None:
    """The registered handler of a kind gets the payload and the context; a kind without a
    handler stays queued; a failing handler sends its job back to the queue with the error."""
    learner = await create_learner(pg_db)
    seen: list[tuple[ClaimedJob, JobContext]] = []

    async def ok(job: ClaimedJob, ctx: JobContext) -> None:
        seen.append((job, ctx))

    async def broken(job: ClaimedJob, ctx: JobContext) -> None:
        raise ValueError("bad payload")

    ctx = cast(JobContext, SimpleNamespace(settings=settings, db=pg_db))
    ok_worker = JobWorker(settings, pg_db, bind_handlers({"agent.viz": ok}, ctx))
    assert await ok_worker.run_one() is False  # an empty queue
    for key, kind in (("v", "agent.viz"), ("c", "check.model")):
        plan = JobPlan(
            kind=kind,
            job_key=key,
            priority_class="P2",
            learner_id=learner,
            payload={"n": 1},
        )
        await q.enqueue(pg_db, plan)
    assert await ok_worker.run_one() is True
    assert [(job.payload, job.kind) for job, _ in seen] == [({"n": 1}, "agent.viz")]
    assert seen[0][1] is ctx
    assert await ok_worker.run_one() is False  # no handler for check.model
    rows = await pg_db.fetch_all("SELECT kind, status FROM ops.jobs ORDER BY kind")
    assert {r["kind"]: r["status"] for r in rows} == {
        "agent.viz": "succeeded",
        "check.model": "queued",
    }

    failing = JobWorker(settings, pg_db, bind_handlers({"check.model": broken}, ctx))
    assert await failing.run_one() is True
    row = await pg_db.fetch_one(
        "SELECT status, attempts, last_error FROM ops.jobs WHERE job_key = 'c'"
    )
    assert row is not None
    assert (row["status"], row["attempts"]) == ("queued", 1) and "bad payload" in row["last_error"]


def test_a_job_of_the_new_queue_in_the_shape_the_handlers_read() -> None:
    """D-A-116: the id is derived from the `job_key`, the class becomes the number, the lock
    falls back to the key, the attempt is the claim's."""
    settings = Settings(job_max_attempts=4)
    learner, session = uuid4(), uuid4()

    def claimed(**changes: object) -> q.ClaimedJob:
        fields: dict[str, object] = {
            "kind": "analyst.run",
            "job_key": "k1",
            "priority_class": "P1",
            "learner_id": learner,
            "lock_key": None,
            "coalesce_key": "c1",
            "required_seq": 7,
            "attempts": 2,
            "visible": False,
            "session_id": session,
            "payload": {"a": 1},
        }
        return q.ClaimedJob(**{**fields, **changes})  # type: ignore[arg-type]

    job = as_phase3_job(claimed(), settings)
    assert job.id == uuid5(NAMESPACE_URL, "quack:job:k1")
    assert (job.kind, job.learner_id, job.lock_key, job.coalesce_key) == (
        "analyst.run",
        learner,
        "k1",
        "c1",
    )
    assert (job.priority, job.attempt, job.max_attempts, job.required_seq) == (1, 2, 4, 7)
    assert (job.job_key, job.session_id, job.payload) == ("k1", session, {"a": 1})
    other = as_phase3_job(claimed(priority_class="P3", lock_key="L", payload={}), settings)
    assert (other.priority, other.lock_key) == (3, "L")
    assert isinstance(other.id, UUID) and other.id == job.id


def test_register_job_handler() -> None:
    registry: dict = {}

    @register_job_handler("test.kind", registry=registry)
    async def handler(job: ClaimedJob, ctx: JobContext) -> None:
        return None

    assert registry == {"test.kind": handler}
    with pytest.raises(ValueError):
        register_job_handler("test.kind", registry=registry)(handler)
