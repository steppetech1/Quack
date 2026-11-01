"""Static contracts of the code base (ТЗ Фазы 3 §8.2): package layers (T-A6, §4.1)."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = BACKEND_DIR / "quack"

# §4.1: layers of top-level packages.
CORE = {
    "config",
    "logging",
    "db",
    # ТЗ Ф4 §4.1: new core packages of stream C (WP19, WP20).
    "storage",
    "embeddings",
    "events",
    "graph",
    "jobs",
    "llm",
    "prompts",
    "compute_client",
}
PRODUCT = {"domains", "session", "artifacts", "practice"}
ASSEMBLY_MODULES = ("quack.api.app", "quack.runtime", "quack.cli", "quack.ops")

# Imports that still violate §4.1 because the importing file belongs to stream B and stream C
# does not touch it (ТЗ §7.3, PHASE3_NOTES D-C-014). Stream B moves them to
# `quack.api.resources` in its own PRs (`artifacts/practice.py` goes away with WP6.2) and
# deletes the entries; `test_layer_exceptions_are_still_needed` fails for a stale entry.
PENDING_IMPORTS: dict[str, frozenset[str]] = {}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(BACKEND_DIR).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@cache
def _modules() -> dict[str, Path]:
    return {_module_name(p): p for p in sorted(PACKAGE_DIR.rglob("*.py"))}


def _is_type_checking(node: ast.If) -> bool:
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _import_nodes(
    tree: ast.Module, *, module_level_only: bool
) -> Iterator[ast.Import | ast.ImportFrom]:
    if not module_level_only:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                yield node
        return

    def visit(nodes: list[ast.stmt]) -> Iterator[ast.Import | ast.ImportFrom]:
        for node in nodes:
            if isinstance(node, ast.Import | ast.ImportFrom):
                yield node
            elif isinstance(node, ast.If):
                if not _is_type_checking(node):
                    yield from visit(node.body)
                yield from visit(node.orelse)
            elif isinstance(node, ast.Try):
                for block in (node.body, node.orelse, node.finalbody):
                    yield from visit(block)
                for handler in node.handlers:
                    yield from visit(handler.body)
            elif isinstance(node, ast.With):
                yield from visit(node.body)

    yield from visit(tree.body)


def _imports(path: Path, module: str, *, module_level_only: bool = False) -> Iterator[str]:
    """Dotted names of the `quack.*` modules imported by `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    modules = _modules()
    for node in _import_nodes(tree, module_level_only=module_level_only):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                source = ".".join([*base, *([node.module] if node.module else [])])
            else:
                source = node.module or ""
            # `from pkg import submodule` imports the submodule, not only the package.
            names = [
                source,
                *(f"{source}.{a.name}" for a in node.names if f"{source}.{a.name}" in modules),
            ]
        for name in names:
            if name.split(".")[0] == "quack":
                yield name


def _layer(module: str) -> str:
    if module.startswith(ASSEMBLY_MODULES):
        return "assembly"
    if module == "quack":
        return "core"
    top = module.split(".")[1]
    if top == "api":
        return "foundation"
    if top in PRODUCT:
        return "product"
    return "core" if top in CORE else "unknown"


def _allowed(importer: str, imported: str) -> bool:
    importer_layer, imported_layer = _layer(importer), _layer(imported)
    if importer_layer == "assembly":
        return True
    if importer_layer == "core":
        return imported_layer == "core"
    if importer_layer == "foundation":
        return imported_layer in ("core", "foundation")
    if importer_layer == "product":
        top = imported.split(".")[1] if imported != "quack" else ""
        return imported_layer in ("core", "foundation") or top in (
            "practice",
            importer.split(".")[1],
        )
    return False


def _violations() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for module, path in _modules().items():
        for imported in _imports(path, module):
            if not _allowed(module, imported):
                found.setdefault(module, set()).add(imported)
    return found


def test_all_packages_have_a_layer() -> None:
    unknown = [m for m in _modules() if _layer(m) == "unknown"]
    assert unknown == [], f"add the package to a layer of ТЗ §4.1: {unknown}"


def test_package_layer_dependencies() -> None:
    """T-A6: imports follow the layer table of §4.1 (the pending ones are listed above)."""
    unexpected = {
        module: sorted(imported - PENDING_IMPORTS.get(module, frozenset()))
        for module, imported in _violations().items()
        if imported - PENDING_IMPORTS.get(module, frozenset())
    }
    assert unexpected == {}


def test_layer_exceptions_are_still_needed() -> None:
    violations = _violations()
    stale = {
        module: sorted(imported - violations.get(module, set()))
        for module, imported in PENDING_IMPORTS.items()
        if imported - violations.get(module, set())
    }
    assert stale == {}, "remove these entries from PENDING_IMPORTS"


def test_no_import_cycles_at_module_level() -> None:
    """T-A6: no cycles in the graph of module-level imports (function-level imports and
    `TYPE_CHECKING` blocks are not edges)."""
    modules = _modules()
    graph = {
        module: {
            i
            for i in _imports(path, module, module_level_only=True)
            if i in modules and not module.startswith(f"{i}.")  # a package is already loading
        }
        for module, path in modules.items()
    }
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in sorted(graph[node]):
            if state.get(nxt) == 1:
                return [*stack[stack.index(nxt) :], nxt]
            if nxt not in state and (cycle := visit(nxt)):
                return cycle
        stack.pop()
        state[node] = 2
        return None

    for module in graph:
        if module not in state:
            cycle = visit(module)
            assert cycle is None, " -> ".join(cycle or [])


def _import_in_clean_interpreter(module: str) -> tuple[str, str]:
    done = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env=os.environ | {"PYTHONPATH": str(BACKEND_DIR)},
        check=False,
    )
    lines = done.stderr.strip().splitlines()
    return module, lines[-1] if done.returncode and lines else ("failed" if done.returncode else "")


def test_every_module_imports_in_a_clean_interpreter() -> None:
    """T-A6: each module is imported first in a fresh interpreter (no import-order luck)."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        failures = [r for r in pool.map(_import_in_clean_interpreter, _modules()) if r[1]]
    assert failures == []


# --- WP4.1: one writer in Neo4j (T-A9a) --------------------------------------------------

# Calls that open a Neo4j session or a write transaction on the driver.
NEO4J_SESSION_CALLS = frozenset({"session", "execute_write", "begin_transaction"})
# Modules allowed to write (WP4.1): the projector store, rebuild, and in `quack.db.neo4j`
# only `verify_schema` and the implementation of `read_session`.
NEO4J_WRITERS: dict[str, frozenset[str] | None] = {
    "quack.graph.neo4j_store": None,
    "quack.graph.rebuild": None,
    "quack.db.neo4j": frozenset({"verify_schema", "read_session"}),
}
# Files of stream B that still open sessions on the driver themselves (ТЗ §7.3: stream C does
# not edit them; PHASE3_NOTES D-C-016). B moves them to `read_session` and deletes the entry;
# `test_neo4j_session_exceptions_are_still_needed` fails for a stale entry.
PENDING_NEO4J_SESSIONS: frozenset[str] = frozenset()


def _neo4j_session_calls(path: Path) -> list[tuple[str, int, str | None]]:
    """`(call, line, enclosing top-level function)` of every `.session(` / `.execute_write(` /
    `.begin_transaction(` call in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int, str | None]] = []

    def walk(node: ast.AST, function: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            scope = function
            if function is None and isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                scope = child.name
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr in NEO4J_SESSION_CALLS
            ):
                found.append((child.func.attr, child.lineno, scope))
            walk(child, scope)

    walk(tree, None)
    return found


def _neo4j_session_violations() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for module, path in _modules().items():
        allowed = NEO4J_WRITERS.get(module, frozenset())
        for call, line, function in _neo4j_session_calls(path):
            if module in NEO4J_WRITERS and (allowed is None or function in allowed):
                continue
            found.setdefault(module, []).append(f"{call}() at line {line}")
    return found


def test_neo4j_writes_only_from_projector_static() -> None:
    """T-A9a: outside the writer modules of WP4.1 no code opens a Neo4j session or a write
    transaction on the driver; everything else reads through `quack.db.neo4j.read_session`."""
    unexpected = {
        module: calls
        for module, calls in _neo4j_session_violations().items()
        if module not in PENDING_NEO4J_SESSIONS
    }
    assert unexpected == {}


def test_neo4j_session_exceptions_are_still_needed() -> None:
    stale = PENDING_NEO4J_SESSIONS - set(_neo4j_session_violations())
    assert stale == set(), "remove these entries from PENDING_NEO4J_SESSIONS"
