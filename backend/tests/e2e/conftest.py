"""Fixtures of the e2e stand (ТЗ Фазы 3 WP10.1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.postgres import SessionFactory
from tests.e2e.system import System, build_system
from tests.support.broken import BrokenRedis
from tests.support.llm import ScriptedFailure

# A port nothing listens on: job handlers that build their own Redis client from `Settings`
# see the same outage as the API and the projector (T-E8).
DEAD_REDIS_URL = "redis://127.0.0.1:1/0"
# Enough failures for every call of a test: the provider is down for the whole test (T-E9).
ALWAYS = 1_000_000


@asynccontextmanager
async def _running(stand: System) -> AsyncIterator[System]:
    stand.start_background()
    try:
        yield stand
    finally:
        await stand.stop_background()
        await stand.client.aclose()
        await stand.resources.compute.aclose()


@pytest.fixture
async def system(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> AsyncIterator[System]:
    async with _running(await build_system(settings, pg_engine, db, neo4j, redis)) as stand:
        yield stand


@pytest.fixture
async def system_without_redis(
    settings: Settings, pg_engine: AsyncEngine, db: SessionFactory, neo4j: AsyncDriver
) -> AsyncIterator[System]:
    broken = settings.model_copy(update={"redis_url": DEAD_REDIS_URL})
    stand = await build_system(broken, pg_engine, db, neo4j, BrokenRedis())
    async with _running(stand) as running:
        yield running


@pytest.fixture
async def system_without_llm(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> AsyncIterator[System]:
    stand = await build_system(settings, pg_engine, db, neo4j, redis)
    stand.llm.fail(ScriptedFailure("unavailable", times=ALWAYS))
    async with _running(stand) as running:
        yield running
