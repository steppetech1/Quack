"""`ops migrate` (Д-§6, ТЗ Ф4 WP1.1, WP9): dbmate up. The commands over the tables of Phases 1-3
(`replay-dlq`, `purge-deleted`) are gone with them (ТЗ Ф4 WP1.3, D-C-180)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg

from quack.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[2]

DBMATE_MIGRATIONS_DIR = BACKEND_DIR / "db" / "migrations"


class AlembicSchemaFound(RuntimeError):
    """The database holds the Alembic schema of Phases 1-3; Phase 4 starts clean (ТЗ Ф4 §2.3)."""


def dbmate_url(dsn: str) -> str:
    """POSTGRES_DSN as a dbmate URL: lib/pq asks for TLS unless `sslmode` is given (D-C-160)."""
    parts = urlsplit(dsn)
    query = parts.query
    if "sslmode=" not in query:
        query = f"{query}&sslmode=disable" if query else "sslmode=disable"
    return urlunsplit(("postgres", parts.netloc, parts.path, query, ""))


def role_dsn(dsn: str, role: str, password: str) -> str:
    """`dsn` (a URL) with another user and password, both percent-encoded: the DSN the migrator
    of `ops migrate --with-roles` connects with (Д-§7). Host, port, database and query stay."""
    parts = urlsplit(dsn)
    if not parts.scheme.startswith("postgres") or not parts.hostname:
        raise ValueError("POSTGRES_DSN must be a postgresql:// URL")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _has_alembic_schema(dsn: str) -> bool:
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        row = conn.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL").fetchone()
    return bool(row and row[0])


def ensure_clean_start(
    dsn: str, *, has_alembic_schema: Callable[[str], bool] = _has_alembic_schema
) -> None:
    """Refuse a database of Phases 1-3 (ТЗ Ф4 §2.3) before anything is changed in it."""
    if has_alembic_schema(dsn):
        raise AlembicSchemaFound(
            "the database has the Alembic schema of Phases 1-3 (public.alembic_version): "
            "Phase 4 needs a clean start, recreate the database (ТЗ Ф4 §2.3)"
        )


def migrate(
    settings: Settings,
    *,
    run: Callable[..., object] = subprocess.run,
    has_alembic_schema: Callable[[str], bool] = _has_alembic_schema,
) -> None:
    """`dbmate up` on `backend/db/migrations` (ТЗ Ф4 WP1.1, WP9; D-C-160)."""
    ensure_clean_start(settings.postgres_dsn, has_alembic_schema=has_alembic_schema)
    args = [
        "dbmate",
        "--migrations-dir",
        str(DBMATE_MIGRATIONS_DIR),
        "--no-dump-schema",
        "up",
    ]
    # The password stays out of the process list: the URL goes through the environment.
    run(args, env={**os.environ, "DATABASE_URL": dbmate_url(settings.postgres_dsn)}, check=True)
