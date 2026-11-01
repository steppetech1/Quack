"""Phase 3 job queue and workers (ТЗ Фазы 3 §8.3): T-Q1…T-Q5, T-Q7…T-Q11."""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack import cli
from quack.api.app import create_app
from quack.api.auth import issue_access_token
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import llm_pressure_key
from quack.events import append_events
from quack.events.models import NewEvent, StoredEvent
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.worker import ProjectorWorker
from quack.jobs import kinds
from quack.jobs.kinds import JOB_KINDS, KIND_LLM_ROLES, JobDeferred
from quack.jobs.periodic import PeriodicScheduler
from quack.jobs.queue import ClaimedJob, JobSpec, claim_next_job, enqueue_job
from quack.jobs.runner import JobContext, JobHandler, JobRunner
from quack.llm.errors import LLMBudgetExceededError
from quack.ops.commands import PURGE_TABLES
from quack.ops.purge_job import handle_purge_deleted
from quack.runtime import (
    JobHandlersMismatchError,
    check_handlers_complete,
    register_all_handlers,
)
from tests.scenario import build_scenario
from tests.support.auth import ensure_user
from tests.support.resources import build_app_resources, build_job_context

QUACK_ROOT = Path(__file__).resolve().parents[1] / "quack"


async def _q(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def _exec(db: SessionFactory, sql: str, **params: Any) -> None:
    async with db() as session, session.begin():
        await session.execute(text(sql), params)


async def _enqueue(db: SessionFactory, spec: JobSpec) -> UUID:
    async with db() as session, session.begin():
        return await enqueue_job(session, spec)


async def _claim(db: SessionFactory, worker: str = "w1") -> ClaimedJob | None:
    async with db() as session, session.begin():
        return await claim_next_job(session, worker)


def _runner(settings: Settings, db: SessionFactory, handlers: dict[str, JobHandler]) -> JobRunner:
    context = build_job_context(settings, db, None, None)
    return JobRunner(settings, db, "w-test", context, handlers=handlers)


# --- T-Q1, T-Q2: readiness of the projection --------------------------------------------


async def test_claim_waits_for_projection(db: SessionFactory) -> None:
    learner = uuid4()
    job_id = await _enqueue(db, kinds.graph_curate(learner, uuid4(), {}, required_seq=5))
    assert await _claim(db) is None
    await _exec(
        db, "INSERT INTO ops.projector_offsets (learner_id, applied_seq) VALUES (:l, 4)", l=learner
    )
    assert await _claim(db) is None
    await _exec(
        db, "UPDATE ops.projector_offsets SET applied_seq = 5 WHERE learner_id = :l", l=learner
    )
    got = await _claim(db)
    assert got is not None and got.id == job_id
    # required_seq IS NULL needs no offsets row.
    other = await _enqueue(db, kinds.graph_curate(uuid4(), uuid4(), {}, required_seq=None))
    got = await _claim(db, "w2")
    assert got is not None and got.id == other


async def test_coalesce_keeps_max_required_seq(db: SessionFactory) -> None:
    learner, domain = uuid4(), uuid4()
    for seq in (3, 7, 5):
        await _enqueue(db, kinds.graph_curate(learner, domain, {}, required_seq=seq))
    rows = await _q(
        db, "SELECT status, required_seq FROM ops.jobs WHERE learner_id = :l", l=learner
    )
    assert rows == [{"status": "queued", "required_seq": 7}]
    learner2, domain2 = uuid4(), uuid4()  # coalesce_key is curate:{domain}, so a new domain
    await _enqueue(db, kinds.graph_curate(learner2, domain2, {}, required_seq=None))
    await _enqueue(db, kinds.graph_curate(learner2, domain2, {}, required_seq=4))
    rows = await _q(db, "SELECT required_seq FROM ops.jobs WHERE learner_id = :l", l=learner2)
    assert rows == [{"required_seq": 4}]


# --- T-Q3, T-Q4: deferral --------------------------------------------------------------


async def test_job_deferred_requeues_without_attempt(
    db: SessionFactory, settings: Settings
) -> None:
    job_id = await _enqueue(db, kinds.session_sweep_inactive())

    async def handler(job: ClaimedJob, ctx: JobContext) -> None:
        raise JobDeferred(600, "x")

    assert await _runner(settings, db, {"session.sweep_inactive": handler}).run_one()
    (row,) = await _q(db, "SELECT * FROM ops.jobs WHERE id = :i", i=job_id)
    assert row["status"] == "queued" and row["attempt"] == 0
    delta = (row["run_after"] - datetime.now(UTC)).total_seconds()
    assert 595 <= delta <= 605
    assert row["locked_by"] is None and row["heartbeat_at"] is None
    assert row["last_error"] == "x"
    assert await _q(db, "SELECT 1 FROM ops.dlq WHERE job_id = :i", i=job_id) == []


async def test_job_deferred_merges_into_queued_duplicate(
    db: SessionFactory, settings: Settings
) -> None:
    learner, domain = uuid4(), uuid4()
    first = await _enqueue(db, kinds.graph_curate(learner, domain, {}, required_seq=None))
    second: list[UUID] = []

    async def handler(job: ClaimedJob, ctx: JobContext) -> None:
        second.append(
            await _enqueue(db, kinds.graph_curate(learner, domain, {}, required_seq=None))
        )
        raise JobDeferred(600, "learner_present")

    assert await _runner(settings, db, {"graph.curate": handler}).run_one()
    (a,) = await _q(db, "SELECT status FROM ops.jobs WHERE id = :i", i=first)
    (b,) = await _q(db, "SELECT status, run_after FROM ops.jobs WHERE id = :i", i=second[0])
    assert a["status"] == "cancelled" and b["status"] == "queued"
    assert b["run_after"] >= datetime.now(UTC) + timedelta(seconds=595)


# --- T-Q8: budget errors ---------------------------------------------------------------


async def test_budget_errors_route_to_defer_or_dlq(db: SessionFactory, settings: Settings) -> None:
    daily = await _enqueue(db, kinds.turn_extract(uuid4(), uuid4(), {}, required_seq=None))
    session_scope = await _enqueue(db, kinds.session_checkpoint(uuid4(), uuid4(), {}))

    async def over_daily(job: ClaimedJob, ctx: JobContext) -> None:
        raise LLMBudgetExceededError("daily", 10, 5)

    async def over_session(job: ClaimedJob, ctx: JobContext) -> None:
        raise LLMBudgetExceededError("session", 10, 5)

    runner = _runner(settings, db, {"turn.extract": over_daily, "session.checkpoint": over_session})
    assert await runner.run_one() and await runner.run_one()
    (d,) = await _q(db, "SELECT status, attempt, run_after FROM ops.jobs WHERE id = :i", i=daily)
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    assert d["status"] == "queued" and d["attempt"] == 0 and d["run_after"] >= tomorrow
    (s,) = await _q(db, "SELECT status, attempt FROM ops.jobs WHERE id = :i", i=session_scope)
    assert s["status"] == "failed" and s["attempt"] == 1
    assert await _q(db, "SELECT 1 FROM ops.dlq WHERE job_id = :i", i=session_scope)


# --- T-Q5: periodic jobs, once per bucket ----------------------------------------------


async def test_periodic_scheduler_runs_once_per_bucket(
    db: SessionFactory, settings: Settings
) -> None:
    now = [datetime(2030, 1, 1, 0, 0, 30, tzinfo=UTC)]
    schedulers = [PeriodicScheduler(settings, db, clock=lambda: now[0]) for _ in range(2)]
    for _ in range(5):
        for scheduler in schedulers:
            await scheduler.tick()

    async def queued() -> dict[str, datetime]:
        rows = await _q(
            db,
            "SELECT kind, run_after FROM ops.jobs WHERE status = 'queued' "
            "AND kind IN ('session.sweep_inactive', 'account.purge_deleted')",
        )
        assert len(rows) == 2, rows
        return {r["kind"]: r["run_after"] for r in rows}

    first = await queued()
    assert first["session.sweep_inactive"] == datetime(2030, 1, 1, 0, 1, 0, tzinfo=UTC)
    assert first["account.purge_deleted"] == datetime(2030, 1, 1, 0, 10, 0, tzinfo=UTC)

    await _exec(db, "UPDATE ops.jobs SET status = 'done' WHERE status = 'queued'")
    now[0] = datetime(2030, 1, 1, 0, 1, 5, tzinfo=UTC)
    for scheduler in schedulers:
        await scheduler.tick()
    rows = await _q(
        db, "SELECT kind, run_after FROM ops.jobs WHERE status = 'queued' ORDER BY kind"
    )
    by_kind = {r["kind"]: r["run_after"] for r in rows}
    assert by_kind["session.sweep_inactive"] == datetime(2030, 1, 1, 0, 2, 0, tzinfo=UTC)
    assert len(rows) == 2


# --- T-Q11: purge job removes the learner completely -----------------------------------


def _as_new(events: list[StoredEvent]) -> list[NewEvent]:
    return [
        NewEvent(
            type=e.type,
            payload=e.payload,
            actor=e.actor,
            idempotency_key=e.idempotency_key,
            occurred_at=e.occurred_at,
            session_id=e.session_id,
            domain_id=e.domain_id,
        )
        for e in events
    ]


async def test_purge_job_removes_learner_completely(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    log, _ = build_scenario()
    learner = log.learner_id
    async with db() as session, session.begin():
        await append_events(session, learner, _as_new(log.events), False)
    await ensure_user(db, learner)
    worker = ProjectorWorker(settings, pg_engine, db, neo4j, redis)
    for _ in range(50):
        if await worker.run_once() == 0:
            break
    assert (await Neo4jGraphStore(neo4j, str(learner)).dump()).nodes != {}
    async with db() as session, session.begin():
        await enqueue_job(session, kinds.turn_extract(learner, uuid4(), {}, required_seq=None))
        await session.execute(
            text(
                "INSERT INTO ops.dispatcher_offsets (learner_id, last_dispatched_seq) "
                "VALUES (:l, 3)"
            ),
            {"l": learner},
        )
    await redis.set(f"presence:learner:{learner}", "s", ex=300)
    await redis.set(f"ratelimit:actions:{learner}", "3", ex=60)
    await redis.set(f"ratelimit:turns:{learner}", "1", ex=60)

    app = create_app(
        settings,
        resources=build_app_resources(settings, pg_engine, neo4j, redis, session_factory=db),
    )
    token = issue_access_token(learner, settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/v1/learner", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    async with db() as session, session.begin():
        await enqueue_job(session, kinds.account_purge_deleted())
    runner = JobRunner(
        settings,
        db,
        "purge-worker",
        handlers={"account.purge_deleted": handle_purge_deleted},
        context=build_job_context(settings, db, neo4j, redis, engine=pg_engine),
    )
    assert await runner.run_one()

    for table in PURGE_TABLES:
        assert await _q(db, f"SELECT 1 FROM {table} WHERE learner_id = :l", l=learner) == [], table
    assert "ops.dispatcher_offsets" in PURGE_TABLES and "ops.turn_manifests" in PURGE_TABLES
    assert await _q(db, "SELECT 1 FROM auth.users WHERE id = :l", l=learner) == []
    assert (await Neo4jGraphStore(neo4j, str(learner)).dump()).nodes == {}
    assert [key async for key in redis.scan_iter(match=f"*{learner}*")] == []
    [tomb] = await _q(
        db, "SELECT status FROM ops.deletion_tombstones WHERE learner_id = :l", l=learner
    )
    assert tomb["status"] == "purged"


# --- T-Q7: handler catalog -------------------------------------------------------------


def test_all_job_kinds_have_handlers() -> None:
    handlers = register_all_handlers()
    assert set(handlers) == set(JOB_KINDS)
    check_handlers_complete(handlers)
    broken = dict(handlers)
    broken.pop("graph.curate")
    with pytest.raises(JobHandlersMismatchError) as info:
        check_handlers_complete(broken)
    assert info.value.missing == ["graph.curate"] and info.value.extra == []


async def test_worker_exits_with_code_1_on_handler_mismatch(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-Q7: `workers` started with a registry that lacks a kind logs `job_handlers_mismatch`
    and exits with code 1 before any resource is created."""
    registry = dict(register_all_handlers())
    registry.pop("graph.curate")
    monkeypatch.setattr(cli, "register_all_handlers", lambda: registry)
    assert await cli._worker(settings) == 1


# --- T-Q10: pressure flag of a model ---------------------------------------------------


async def test_pressure_flag_blocks_kinds_using_that_model(
    db: SessionFactory, settings: Settings, redis: Redis
) -> None:
    """WP5.3: while `llm:pressure:{model}` is set, kinds whose roles run on the model wait;
    kinds without a model (P1 maintenance) are still claimed. The flag expires → claimed."""
    model = settings.model_for_role("extractor")
    assert KIND_LLM_ROLES["turn.extract"] == ("extractor",)
    learner = uuid4()
    extract = await _enqueue(db, kinds.turn_extract(learner, uuid4(), {}, required_seq=None))
    sweep = await _enqueue(db, kinds.session_sweep_inactive())
    await _exec(
        db, "UPDATE ops.jobs SET priority = 0 WHERE id = :id", id=extract
    )  # first in line without the flag
    await redis.set(llm_pressure_key(model), "x", px=1000)

    claimed: list[UUID] = []

    async def handler(job: ClaimedJob, ctx: JobContext) -> None:
        claimed.append(job.id)

    context = build_job_context(settings, db, None, redis)
    runner = JobRunner(
        settings,
        db,
        "w-pressure",
        context,
        handlers={"turn.extract": handler, "session.sweep_inactive": handler},
    )
    assert await runner.run_one()
    assert claimed == [sweep]
    assert await runner.run_one() is False
    [row] = await _q(db, "SELECT status FROM ops.jobs WHERE id = :id", id=extract)
    assert row["status"] == "queued"

    await asyncio.sleep(1.1)  # the flag expires by its TTL
    assert await redis.exists(llm_pressure_key(model)) == 0
    assert await runner.run_one()
    assert claimed == [sweep, extract]


async def test_pressure_flags_without_redis_block_nothing(
    db: SessionFactory, settings: Settings
) -> None:
    """§4.2: a context without Redis claims every kind and raises nothing."""
    job_id = await _enqueue(db, kinds.turn_extract(uuid4(), uuid4(), {}, required_seq=None))
    seen: list[UUID] = []

    async def handler(job: ClaimedJob, ctx: JobContext) -> None:
        seen.append(job.id)

    assert await _runner(settings, db, {"turn.extract": handler}).run_one()
    assert seen == [job_id]


# --- T-Q9: product code takes resources from the context ------------------------------

_FORBIDDEN_CALLS = {
    "create_driver",
    "create_redis",
    "create_engine",
    "HttpLLMProvider",
    "ResponsesClient",
    "AsyncOpenAI",
    "ComputeClient",
}
_PRODUCT_PACKAGES = ("domains", "session", "artifacts", "practice")
# The only direct enqueue left after WP11.4 (WP11.2): the cheatsheet refresh endpoint.
_ENQUEUE_EXCEPTION = ("artifacts/cheatsheets.py", "request_cheatsheet_refresh")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _violations(path: Path) -> list[str]:
    rel = path.relative_to(QUACK_ROOT).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstring_nodes(tree)
    found: list[str] = []

    def visit(node: ast.AST, func: str | None) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            func = node.name
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _FORBIDDEN_CALLS:
                found.append(f"{rel}:{node.lineno} calls {name}")
            if name == "enqueue_job" and (rel, func) != _ENQUEUE_EXCEPTION:
                found.append(f"{rel}:{node.lineno} calls enqueue_job in {func}")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docs
            and "ops.jobs" in node.value
        ):
            found.append(f"{rel}:{node.lineno} has SQL on ops.jobs")
        for child in ast.iter_child_nodes(node):
            visit(child, func)

    visit(tree, None)
    return found


def test_product_code_uses_context_resources() -> None:
    problems: list[str] = []
    for package in _PRODUCT_PACKAGES:
        for path in sorted((QUACK_ROOT / package).rglob("*.py")):
            problems.extend(_violations(path))
    assert problems == []
