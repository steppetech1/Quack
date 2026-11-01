"""Static checks of the Phase 4 data layer (ТЗ Ф4 §8.2; Т-§1.2, Т-§3.5).

T-DB2: no call of `execute`/`executemany`/`fetch_one`/`fetch_all` gets SQL built from an
f-string, `+` concatenation, `%` formatting or `str.format` — dynamic SQL goes through
`psycopg.sql`.

T-DB4: every repository function over a table with `learner_id` takes `learner_id` and every
statement over such a table uses `%(learner_id)s` (in `WHERE` for reads, updates and deletes).
A repository module is a module of `quack/` that works through `Db` (imports `quack.db`'s `Db`);
the SQLAlchemy modules of Phases 1–3 leave with WP1.3 (T-DB3).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

from tests.conftest import BACKEND_DIR
from tests.db.fixtures import MIGRATIONS_DIR

QUACK_DIR = BACKEND_DIR / "quack"
SQL_METHODS = frozenset({"execute", "executemany", "fetch_one", "fetch_all"})
_SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", re.IGNORECASE)
_TABLE_REF = re.compile(r"\b(core|content|ops)\.([a-z_]+)\b")
_WHERE_LEARNER = re.compile(r"\bWHERE\b[\s\S]*\blearner_id\s*=\s*%\(learner_id\)s", re.I)

# Statements over learner tables that are cross-learner by design. Each entry needs a reason.
CROSS_LEARNER_ALLOWED: dict[str, str] = {}


def _modules() -> Iterator[tuple[Path, ast.Module]]:
    for path in sorted(QUACK_DIR.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_dynamic_sql(node: ast.expr) -> bool:
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and isinstance(node.func.value, ast.Constant | ast.JoinedStr)
    )


def test_no_fstring_sql() -> None:
    offenders: list[str] = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in SQL_METHODS
                and node.args
                and _is_dynamic_sql(node.args[0])
            ):
                offenders.append(f"{path.relative_to(BACKEND_DIR)}:{node.lineno}")
    assert offenders == []


def test_dynamic_sql_detector_catches_known_shapes() -> None:
    """The check above must fail on the forbidden shapes (ТЗ Ф4 §8.15 п.4)."""
    for source in (
        'db.execute(f"SELECT {x}")',
        'db.fetch_one("SELECT " + x)',
        'db.fetch_all("SELECT %s" % x)',
        'db.execute("SELECT {}".format(x))',
    ):
        call = ast.parse(source).body[0]
        assert isinstance(call, ast.Expr) and isinstance(call.value, ast.Call)
        assert _is_dynamic_sql(call.value.args[0]), source
    assert not _is_dynamic_sql(ast.parse('"SELECT 1"').body[0].value)  # type: ignore[attr-defined]


def _learner_tables() -> set[str]:
    """Tables with a `learner_id` column, from the migrations (the schema source, Т-§0.3)."""
    tables: set[str] = set()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        up = path.read_text(encoding="utf-8").split("-- migrate:down")[0]
        for match in re.finditer(r"CREATE TABLE (\w+\.\w+) \(([\s\S]*?)\n\);", up):
            if re.search(r"^\s*learner_id\b", match.group(2), re.MULTILINE):
                tables.add(match.group(1))
    return tables


def _uses_db(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module in {"quack.db", "quack.db.db"}
            and any(alias.name == "Db" for alias in node.names)
        ):
            return True
    return False


def _sql_strings(tree: ast.Module) -> dict[str, str]:
    """Module-level SQL constants by name, plus inline SQL literals keyed by line."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
            if (
                isinstance(target, ast.Name)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and _SQL_START.match(value.value)
            ):
                found[target.id] = value.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in SQL_METHODS
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found[f"<line {node.lineno}>"] = node.args[0].value
    return found


def _learner_statement_problem(statement: str, learner_tables: set[str]) -> str | None:
    touched = {f"{s}.{t}" for s, t in _TABLE_REF.findall(statement)} & learner_tables
    if not touched:
        return None
    if "%(learner_id)s" not in statement:
        return f"no %(learner_id)s for {sorted(touched)}"
    is_insert = re.match(r"^\s*INSERT\b", statement, re.IGNORECASE)
    if not is_insert and not _WHERE_LEARNER.search(statement):
        return "learner_id is not used in WHERE"
    return None


def test_repository_functions_take_learner_id() -> None:
    learner_tables = _learner_tables()
    assert {"core.events", "core.learners", "ops.jobs"} <= learner_tables
    problems: list[str] = []
    for path, tree in _modules():
        if not _uses_db(tree):
            continue
        rel = str(path.relative_to(BACKEND_DIR))
        statements = _sql_strings(tree)
        learner_sql = {
            name
            for name, text in statements.items()
            if {f"{s}.{t}" for s, t in _TABLE_REF.findall(text)} & learner_tables
        }
        for name, text in statements.items():
            if f"{rel}:{name}" in CROSS_LEARNER_ALLOWED:
                continue
            problem = _learner_statement_problem(text, learner_tables)
            if problem:
                problems.append(f"{rel}:{name}: {problem}")
        required = {name for name in learner_sql if f"{rel}:{name}" not in CROSS_LEARNER_ALLOWED}
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            refs = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            refs |= {f"<line {n.lineno}>" for n in ast.walk(node) if isinstance(n, ast.Call)}
            params = {a.arg for a in [*node.args.args, *node.args.kwonlyargs]}
            if refs & required and "learner_id" not in params:
                problems.append(f"{rel}:{node.name}: no learner_id parameter")
    assert problems == []


def test_learner_statement_check_catches_missing_filter() -> None:
    """The checks above must fail on statements without the learner filter (§8.15 п.4)."""
    tables = {"core.events"}
    assert _learner_statement_problem("SELECT seq FROM core.events WHERE seq = 1", tables)
    assert _learner_statement_problem(
        "SELECT seq FROM core.events WHERE seq > 0 AND session_id = %(learner_id)s", tables
    )
    assert (
        _learner_statement_problem(
            "SELECT seq FROM core.events WHERE learner_id = %(learner_id)s", tables
        )
        is None
    )
    assert (
        _learner_statement_problem(
            "INSERT INTO core.events (learner_id) VALUES (%(learner_id)s)", tables
        )
        is None
    )
