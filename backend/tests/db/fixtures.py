"""Postgres of Phase 4 for tests (ТЗ Ф4 WP1.1–1.2; Phase 4 decision D-A-103).

Until the old stack goes away (WP1.3), Phase 4 tables live in a separate database
`<test db>_p4` on the same server, migrated by dbmate from `backend/db/migrations/`. The
Alembic database of Phases 1–3 stays as it is for the old tests. The fixtures are registered
once in `tests/conftest.py` (`pytest_plugins`), so every test directory can request them.
`pg_db` gives a `Db` over a database with every Phase 4 table truncated;
`create_learner(db)` inserts a `core.learners` row (append_events needs it, Т-§2.3).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import pytest
from psycopg import sql

from quack.config import Settings
from quack.db import Db, open_db
from tests.conftest import BACKEND_DIR, PG_DSN, _unavailable

MIGRATIONS_DIR: Path = BACKEND_DIR / "db" / "migrations"
PHASE4_SCHEMAS = ("core", "content", "ops")


def database_url(name: str, *, for_dbmate: bool = False) -> str:
    """PG_DSN with another database name; dbmate (lib/pq) also needs `sslmode=disable`."""
    parts = urlsplit(PG_DSN)
    query = "sslmode=disable" if for_dbmate else parts.query
    return urlunsplit(parts._replace(path=f"/{name}", query=query))


def p4_database_name() -> str:
    return urlsplit(PG_DSN).path.lstrip("/") + "_p4"


def run_dbmate(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the dbmate binary (Т-§1.2) against `url`; skip or fail like other infrastructure."""
    binary = shutil.which("dbmate")
    if binary is None:
        _unavailable("dbmate", FileNotFoundError("dbmate is not on PATH"))
        raise AssertionError("unreachable")
    command = [
        binary,
        "--url",
        url,
        "--migrations-dir",
        str(MIGRATIONS_DIR),
        "--no-dump-schema",
        *args,
    ]
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)


@pytest.fixture(scope="session")
def p4_database() -> str:
    """Re-create `<test db>_p4` and apply the migrations once per test session."""
    url = database_url(p4_database_name(), for_dbmate=True)
    try:
        run_dbmate(url, "drop")
        run_dbmate(url, "up")
    except subprocess.CalledProcessError as exc:
        _unavailable("Postgres for dbmate", RuntimeError(exc.stderr))
    return database_url(p4_database_name())


@pytest.fixture(scope="session")
async def p4_db(settings: Settings, p4_database: str) -> AsyncIterator[Db]:
    db = await open_db(settings.model_copy(update={"postgres_dsn": p4_database}))
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def pg_db(p4_db: Db) -> Db:
    """`Db` over the Phase 4 database with every table of core/content/ops emptied."""
    rows = await p4_db.fetch_all(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema = ANY(%(schemas)s) AND table_type = 'BASE TABLE'",
        {"schemas": list(PHASE4_SCHEMAS)},
    )
    tables = sql.SQL(", ").join(sql.Identifier(r["table_schema"], r["table_name"]) for r in rows)
    await p4_db.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY").format(tables))
    return p4_db


async def create_learner(db: Db, *, email: str | None = None) -> UUID:
    learner_id = uuid4()
    await db.execute(
        "INSERT INTO core.learners (learner_id, email) VALUES (%(learner_id)s, %(email)s)",
        {"learner_id": learner_id, "email": email or f"{learner_id}@test.invalid"},
    )
    return learner_id
