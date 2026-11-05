"""The handlers of the jobs and the product code around the queue (ТЗ Ф4 §4.2, WP2.2, D-A-231):
T-Q7 — `workers` start only with a handler for every kind; T-Q9 — product code takes the heavy
resources from the context and puts no job itself. The queue and the executor of Phase 4 are in
`tests/jobs/test_queue_p4.py`, `test_dispatcher_p4.py`, `test_executor_p4.py` (T-Q1…T-Q6, T-Q8,
T-Q10 of Phase 3 went there, D-A-231)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from quack import cli
from quack.config import Settings
from quack.jobs.kinds import JOB_KINDS
from quack.runtime import (
    JobHandlersMismatchError,
    check_handlers_complete,
    register_all_handlers,
)

QUACK_ROOT = Path(__file__).resolve().parents[1] / "quack"


# --- T-Q7: handler catalog -------------------------------------------------------------


def test_all_job_kinds_have_handlers() -> None:
    handlers = register_all_handlers()
    assert set(handlers) == set(JOB_KINDS)
    check_handlers_complete(handlers)
    broken = dict(handlers)
    broken.pop("agent.viz")
    with pytest.raises(JobHandlersMismatchError) as info:
        check_handlers_complete(broken)
    assert info.value.missing == ["agent.viz"] and info.value.extra == []


async def test_worker_exits_with_code_1_on_handler_mismatch(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-Q7: `workers` started with a registry that lacks a kind logs `job_handlers_mismatch`
    and exits with code 1 before any resource is created."""
    registry = dict(register_all_handlers())
    registry.pop("agent.viz")
    monkeypatch.setattr(cli, "register_all_handlers", lambda: registry)
    assert await cli._worker(settings) == 1


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
# Jobs are put by the dispatcher from the log (Т-§13.3); the only direct enqueue left in product
# code is the cheatsheet refresh endpoint of Phase 3 (WP11.2), until stream C replaces it.
_ENQUEUE_CALLS = {"enqueue_job", "enqueue"}  # the queue of Phase 3 and `queue_p4.enqueue`
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
            if name in _ENQUEUE_CALLS and (rel, func) != _ENQUEUE_EXCEPTION:
                found.append(f"{rel}:{node.lineno} calls {name} in {func}")
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
