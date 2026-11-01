"""Shared fixtures. Integration fixtures need real Postgres 16, Neo4j and Redis.

Defaults match the local test containers (see backend/README.md); override with
QUACK_TEST_POSTGRES_DSN, QUACK_TEST_NEO4J_URI, QUACK_TEST_NEO4J_USER,
QUACK_TEST_NEO4J_PASSWORD, QUACK_TEST_REDIS_URL. When the infrastructure is unreachable
the integration tests are skipped, unless QUACK_TEST_REQUIRE_INFRA=1 (then they fail).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.neo4j import create_driver, verify_schema
from quack.db.postgres import SessionFactory, create_engine, create_session_factory
from quack.db.redis import create_redis

BACKEND_DIR = Path(__file__).resolve().parent.parent

# ТЗ Ф4 WP1.2, D-A-103: Phase 4 Postgres fixtures (`p4_db`, `pg_db`), registered once for all
# streams; a second registration would re-create the database under an open pool.
pytest_plugins = ["tests.db.fixtures"]

PG_DSN = os.environ.get(
    "QUACK_TEST_POSTGRES_DSN", "postgresql://quack:quack@localhost:55432/quack_test"
)
NEO4J_URI = os.environ.get("QUACK_TEST_NEO4J_URI", "bolt://localhost:57687")
NEO4J_USER = os.environ.get("QUACK_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("QUACK_TEST_NEO4J_PASSWORD", "quacktest123")
REDIS_URL = os.environ.get("QUACK_TEST_REDIS_URL", "redis://localhost:56379/0")
REQUIRE_INFRA = os.environ.get("QUACK_TEST_REQUIRE_INFRA") == "1"

ALL_TABLES = (
    "auth.users",
    "core.learner_seq",
    "core.events",
    "core.sessions",
    "core.artifacts",
    "core.domains",
    "core.task_bank",
    "core.llm_outbox",
    "ops.jobs",
    "ops.dlq",
    "ops.llm_ledger",
    "ops.deletion_tombstones",
    "ops.projector_offsets",
    "ops.event_types_seen",
    "ops.dispatcher_offsets",
    "ops.turn_manifests",
)


def _unavailable(what: str, exc: BaseException) -> None:
    message = f"{what} unavailable: {exc!r}"
    if REQUIRE_INFRA:
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        env="test",
        postgres_dsn=PG_DSN,
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,  # type: ignore[arg-type]
        redis_url=REDIS_URL,
        # Tests of Phases 1–2 inject one provider failure and expect the fallback at once;
        # the retries of WP5.2 are covered by T-L2/T-L3 with their own settings.
        llm_retry_attempts=0,
    )


def _migrate(dsn: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
async def pg_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings, pool_size=5)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on environment
        await engine.dispose()
        _unavailable("Postgres", exc)
    async with engine.begin() as conn:
        for schema in ("ops", "core", "auth"):
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
    await asyncio.to_thread(_migrate, settings.sqlalchemy_dsn)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(pg_engine: AsyncEngine) -> SessionFactory:
    async with pg_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(ALL_TABLES)}"))
    return create_session_factory(pg_engine)


@pytest.fixture(scope="session")
async def neo4j_driver(settings: Settings) -> AsyncIterator[AsyncDriver]:
    driver = create_driver(settings)
    try:
        await driver.verify_connectivity()
    except Exception as exc:  # pragma: no cover - depends on environment
        await driver.close()
        _unavailable("Neo4j", exc)
    missing = await verify_schema(driver)
    assert missing == []
    yield driver
    await driver.close()


@pytest.fixture
async def neo4j(neo4j_driver: AsyncDriver) -> AsyncDriver:
    async with neo4j_driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
    return neo4j_driver


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    client = create_redis(settings)
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - depends on environment
        await client.aclose()
        _unavailable("Redis", exc)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
def _restore_process_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """`cli.main` writes `PROCESS_ROLE` to the environment (WP8.1); `Settings` of later tests
    must not inherit it. setenv records the original state, delenv starts the test without it."""
    monkeypatch.setenv("PROCESS_ROLE", "api")
    monkeypatch.delenv("PROCESS_ROLE")


@pytest.fixture(autouse=True)
def _clear_local_rate_limits() -> None:
    from quack.api.ratelimit import LOCAL_LIMITS

    LOCAL_LIMITS.clear()
