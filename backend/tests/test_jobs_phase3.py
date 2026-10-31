"""Phase 3 job queue and workers (ТЗ Фазы 3 §8.3): T-Q1…T-Q4, T-Q7…T-Q9.

Stage II of stream A: these tests are red until WP2 and WP3.1–3.4 are implemented.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.jobs import kinds
from quack.jobs.kinds import JOB_KINDS, JobDeferred
from quack.jobs.queue import ClaimedJob, JobSpec, claim_next_job, enqueue_job
from quack.jobs.runner import JobContext, JobHandler, JobRunner
from quack.llm.errors import LLMBudgetExceededError
from quack.runtime import (
    JobHandlersMismatchError,
    check_handlers_complete,
    register_all_handlers,
)

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
    return JobRunner(settings, db, "w-test", handlers=handlers)


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
    other = await _enqueue(db, kinds.graph_curate(uuid4(), uuid4(), {}))
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
    learner2 = uuid4()
    await _enqueue(db, kinds.graph_curate(learner2, domain, {}))
    await _enqueue(db, kinds.graph_curate(learner2, domain, {}, required_seq=4))
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
    first = await _enqueue(db, kinds.graph_curate(learner, domain, {}))
    second: list[UUID] = []

    async def handler(job: ClaimedJob, ctx: JobContext) -> None:
        second.append(await _enqueue(db, kinds.graph_curate(learner, domain, {})))
        raise JobDeferred(600, "learner_present")

    assert await _runner(settings, db, {"graph.curate": handler}).run_one()
    (a,) = await _q(db, "SELECT status FROM ops.jobs WHERE id = :i", i=first)
    (b,) = await _q(db, "SELECT status, run_after FROM ops.jobs WHERE id = :i", i=second[0])
    assert a["status"] == "cancelled" and b["status"] == "queued"
    assert b["run_after"] >= datetime.now(UTC) + timedelta(seconds=595)


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


# --- T-Q8: budget errors ---------------------------------------------------------------


async def test_budget_errors_route_to_defer_or_dlq(db: SessionFactory, settings: Settings) -> None:
    daily = await _enqueue(db, kinds.turn_extract(uuid4(), uuid4(), {}))
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
