"""Phase 3 job queue (ТЗ Фазы 3 §8.3): T-Q7 and T-Q9, still red.

T-Q7 needs the `account.purge_deleted` handler (WP3.3); T-Q9 needs WP2.1 in zones B and C and
the switch of WP11.4. They move into `test_jobs_phase3.py` when they turn green.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

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
    broken.pop("graph.curate")
    with pytest.raises(JobHandlersMismatchError) as info:
        check_handlers_complete(broken)
    assert info.value.missing == ["graph.curate"] and info.value.extra == []


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
