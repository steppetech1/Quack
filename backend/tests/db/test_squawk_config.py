"""T-DB7 (ТЗ Ф4 WP1.4, Д-§6.2): the squawk config and the marker of a CONCURRENTLY migration."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = BACKEND_DIR / "db" / "migrations"


def _concurrent_problem(text: str) -> str | None:
    """A CONCURRENTLY index outside a transaction needs `transaction:false` for dbmate and
    `-- squawk-disable-assume-in-transaction` for squawk (Д-§6.2: «одно без другого»)."""
    up = text.split("-- migrate:down")[0]
    sql = "\n".join(line for line in up.splitlines() if not line.lstrip().startswith("--"))
    if not re.search(r"\bCONCURRENTLY\b", sql, re.IGNORECASE):
        return None
    if "-- migrate:up transaction:false" not in up:
        return "CONCURRENTLY without `-- migrate:up transaction:false`"
    if "-- squawk-disable-assume-in-transaction" not in up:
        return "transaction:false without `-- squawk-disable-assume-in-transaction`"
    return None


def test_squawk_config_and_concurrent_marker() -> None:
    config = tomllib.loads((BACKEND_DIR / ".squawk.toml").read_text(encoding="utf-8"))
    assert config["pg_version"].split(".")[0] == "16"
    assert config["assume_in_transaction"] is True
    problems = {
        path.name: problem
        for path in sorted(MIGRATIONS_DIR.glob("*.sql"))
        if (problem := _concurrent_problem(path.read_text(encoding="utf-8")))
    }
    assert problems == {}
    bad = "-- migrate:up\nCREATE INDEX CONCURRENTLY i ON t (c);\n"
    assert _concurrent_problem(bad) is not None
    half = "-- migrate:up transaction:false\nCREATE INDEX CONCURRENTLY i ON t (c);\n"
    assert _concurrent_problem(half) is not None
