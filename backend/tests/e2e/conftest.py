"""Fixtures of the e2e stand (ТЗ Ф4 WP26.1): the stand runs on the database of Phase 4."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from tests.e2e.system import System, build_system
from tests.support.broken import BrokenRedis
from tests.support.llm import ScriptedFailure

# A port nothing listens on: job handlers that build their own Redis client from `Settings`
# see the same outage as the API and the projector (T-E8).
DEAD_REDIS_URL = "redis://127.0.0.1:1/0"
# Enough failures for every call of a test: the provider is down for the whole test (T-E9).
ALWAYS = 1_000_000
# T-E2 walks every mutating endpoint three times as one learner: more than a minute's
# `learner_actions_per_min`. The rate limit is not what T-E2 checks.
WALK_ACTIONS_PER_MIN = 1_000


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
    neo4j: AsyncDriver,
    redis: Redis,
    pg_db: Db,
    p4_database: str,
) -> AsyncIterator[System]:
    async with _running(await build_system(settings, neo4j, redis, pg_db, p4_database)) as stand:
        yield stand


@pytest.fixture
async def system_for_walk(
    settings: Settings,
    neo4j: AsyncDriver,
    redis: Redis,
    pg_db: Db,
    p4_database: str,
) -> AsyncIterator[System]:
    walk = settings.model_copy(update={"learner_actions_per_min": WALK_ACTIONS_PER_MIN})
    async with _running(await build_system(walk, neo4j, redis, pg_db, p4_database)) as stand:
        yield stand


@pytest.fixture
async def system_without_redis(
    settings: Settings,
    neo4j: AsyncDriver,
    pg_db: Db,
    p4_database: str,
) -> AsyncIterator[System]:
    broken = settings.model_copy(update={"redis_url": DEAD_REDIS_URL})
    stand = await build_system(broken, neo4j, BrokenRedis(), pg_db, p4_database)
    async with _running(stand) as running:
        yield running


@pytest.fixture
async def system_without_llm(
    settings: Settings,
    neo4j: AsyncDriver,
    redis: Redis,
    pg_db: Db,
    p4_database: str,
) -> AsyncIterator[System]:
    stand = await build_system(settings, neo4j, redis, pg_db, p4_database)
    stand.llm.fail(ScriptedFailure("unavailable", times=ALWAYS))
    async with _running(stand) as running:
        yield running


type StandFactory = Callable[..., AbstractAsyncContextManager[System]]


@pytest.fixture
def stand(
    settings: Settings, neo4j: AsyncDriver, redis: Redis, pg_db: Db, p4_database: str
) -> StandFactory:
    """`async with stand(neo4j=..., redis=..., storage=..., deletions=..., email=...) as system`:
    a running stand with some of its resources replaced (T-E11); the processes of Phase 4 keep
    their own, working connections."""

    @asynccontextmanager
    async def make(
        *, neo4j_driver: Any = None, redis_client: Any = redis, **resources: Any
    ) -> AsyncIterator[System]:
        built = await build_system(
            settings,
            neo4j if neo4j_driver is None else neo4j_driver,
            redis_client,
            pg_db,
            p4_database,
            **resources,
        )
        async with _running(built) as running:
            yield running

    return make
