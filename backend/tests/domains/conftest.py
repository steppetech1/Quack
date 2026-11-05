"""Fixtures of `tests/domains/` (ТЗ Ф4 WP1.3, D-A-207): the app over the Phase 4 `Db`, a learner
signed in with a Phase 4 session, and a tracer that records the deletion of traces."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.api.app import create_app
from quack.api.resources import AppResources
from quack.config import Settings
from quack.db import Db
from tests.db.fixtures import create_learner
from tests.support.auth import session_token
from tests.support.resources import build_app_resources
from tests.support.tracer import RecordingTracer


@pytest.fixture
def tracer() -> RecordingTracer:
    return RecordingTracer()


@pytest.fixture
def resources(
    settings: Settings,
    pg_db: Db,
    neo4j: AsyncDriver,
    redis: Redis,
    tracer: RecordingTracer,
) -> AppResources:
    built = build_app_resources(settings, neo4j, redis, db=pg_db)
    built.llm.tracer = tracer
    return built


@pytest.fixture
def domains_app(settings: Settings, resources: AppResources) -> FastAPI:
    return create_app(settings, resources=resources)


@pytest.fixture
async def api_client(domains_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=domains_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def seeded_learner(pg_db: Db, settings: Settings) -> tuple[UUID, str]:
    """A learner of Phase 4 (`core.learners`) with a live session: `(learner_id, token)`; the
    requests send the token with `tests.support.auth.session_cookie`."""
    learner_id = await create_learner(pg_db)
    return learner_id, await session_token(pg_db, learner_id, settings)
