"""Static checks of the Phase 4 data layer (ТЗ Ф4 §8.2; Т-§1.2, Т-§3.5).

T-DB2: no call of `execute`/`executemany`/`fetch_one`/`fetch_all` gets SQL built from an
f-string, `+` concatenation, `%` formatting or `str.format` — dynamic SQL goes through
`psycopg.sql`.

T-DB4: every repository function over a table with `learner_id` takes `learner_id` and every
statement over such a table uses `%(learner_id)s` (in `WHERE` for reads, updates and deletes).
A repository module is a module of `quack/` that works through `Db` (imports `quack.db`'s `Db`);
the SQLAlchemy modules of Phases 1–3 were removed in WP1.3 (T-DB3, D-A-234).
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
CROSS_LEARNER_ALLOWED: dict[str, str] = {
    # D-B-211: the money pool is one operational sum over every learner's calls.
    "quack/llm/access.py:_POOL_SPENT": "the pool of money for models is shared by all learners",
    # D-C-111 (#97): before login the learner is unknown, the row is found by its address.
    "quack/auth/links.py:_LEARNER_BY_EMAIL": "learner of a mail address, unknown before login",
    # D-C-111 (#97): the learner of a request is found by the token of its session.
    "quack/auth/sessions.py:_SESSION_BY_TOKEN": "learner of a session token, unknown before auth",
    # D-A-128 (#142): the phone has no session; the learner is found by the pairing token.
    "quack/auth/pairing.py:_REDEEM_TOKEN": "learner of a pairing token, unknown before pairing",
}
# D-A-164 (#182): the job queue and the dispatcher serve every learner (Т-§13.1–13.3): a job is
# found by its global `job_key` or `coalesce_key`, a periodic job has no learner, a claim picks
# the best job of any learner, the dispatcher walks the learners behind their mark.
CROSS_LEARNER_ALLOWED |= (
    {
        f"quack/jobs/queue_p4.py:{name}": "the job queue serves every learner (Т-§13.1, Т-§13.2)"
        for name in (
            "_JOB_EXISTS",
            "_COALESCE",
            "_INSERT",
            "_LEARNER_LOCK",
            "_LEARNER_LOAD",
            "_SKIP_BUDGET",
            "_SESSION_SPEND",
            "_HEARTBEAT",
            "_FINISH",
            "_REQUEUE",
            "_SUPERSEDE",
            "_CANCEL_SUPERSEDED",
        )
    }
    | {
        f"quack/jobs/dispatcher_p4.py:{name}": "the dispatcher walks every learner (Т-§13.3)"
        for name in ("_PENDING_LEARNERS", "_LAG", "_IDLE_LEARNERS")
    }
    | {
        # D-A-200: the projector process walks the learners behind their watermark (Т-§2.4).
        "quack/graph/projection/process.py:_PENDING": "the projector walks every learner",
        # D-A-204: `manifests.cleanup` deletes the old manifests of every learner (Т-§3.6).
        "quack/jobs/cleanup_p4.py:_DELETE_OLD": "the periodic cleanup of every learner",
        # D-A-215: `exports.cleanup` deletes the expired archives of every learner (Д-§8.4).
        "quack/jobs/cleanup_p4.py:_EXPIRED_EXPORTS": "the periodic cleanup of every learner",
        "quack/jobs/cleanup_p4.py:_MARK_EXPIRED": "a row found by its global export_id",
        # D-A-219: `calibration.weekly` measures the attempts of every learner (Т-§18.5).
        "quack/jobs/calibration_p4.py:_ATTEMPTS": "the weekly calibration over every learner",
    }
)
# D-B-195 (WP16.7): `ops metrics` measures the whole system over a window (Т-§18.2, Т-§18.3);
# D-B-194: the operator's `ops replay-session` names only the session.
CROSS_LEARNER_ALLOWED |= {
    f"quack/agents/metrics.py:{name}": "system metrics over every learner (Т-§18.2, Т-§18.3)"
    for name in (
        "_TURNS",
        "_VERDICTS",
        "_COUNT_OF_TYPE",
        "_DISPUTES",
        "_SECOND_OPINIONS",
        "_EXTRACTED",
        "_TEMPORARY_SKILLS",
        "_COST",
        "_HOURS",
        "_ATTEMPTS",
    )
} | {
    "quack/session/replay.py:_SESSION_LEARNER": "learner of a session named by the operator",
}

# D-C-181 (WP1.3 step 3): `ops status`, `ops restore-check` and `ops seed-learners` look at the
# whole system, not at one learner (Д-§9.1, Д-§8.3, Д-§11).
CROSS_LEARNER_ALLOWED |= {
    f"quack/ops/status.py:{name}": "the report of the whole system (Д-§9.1)"
    for name in (
        "_PROJECTOR",
        "_DISPATCHER",
        "_STOPPED",
        "_BY_STATUS",
        "_OLDEST_QUEUED",
        "_WAITING",
        "_FINISHED_HOUR",
        "_LLM",
    )
} | {
    "quack/ops/restore_check.py:_LOG_SEQS": "the log position of every learner (Д-§8.3)",
    "quack/ops/seed.py:_COUNT_REAL": "learners that are not seeded, over all of them (Д-§11)",
}


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
