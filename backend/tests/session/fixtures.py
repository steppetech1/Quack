"""Shared fixtures of the session tests on `Db` (ТЗ Ф4 WP13.10, WP18.3), loaded as a plugin.

A test module declares `pytest_plugins = ["tests.session.fixtures"]` (a `conftest.py` here would
count as a test-configuration change). The infrastructure fixtures `settings`, `pg_db`, `neo4j`,
`redis` come from `tests/conftest.py`. Plain helpers (`parse_sse`) live in
`tests/session/helpers.py`: import them from there, importing this module directly defeats
pytest's assertion rewriting.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.api.app import create_app
from quack.api.auth import require_terms
from quack.config import Settings
from quack.db import Db
from tests.session.helpers import Learner, make_learner
from tests.support.resources import build_app_resources


@pytest.fixture
async def session_app(
    settings: Settings,
    pg_db: Db,
    neo4j: AsyncDriver,
    redis: Redis,
) -> FastAPI:
    """`create_app(settings, connect=False)` over the test pools (all routers mounted).

    The lifespan is not run by `ASGITransport`, so the resources set here stay in place
    and the pools are owned (and closed) by the infrastructure fixtures.
    """
    resources = build_app_resources(settings, neo4j, redis, db=pg_db)
    app = create_app(settings, resources=resources)
    # The learners of these tests have a cookie session but no consent; the consent gate of the
    # session start (WP18.4) has its own test (`test_session_terms.py`).
    app.dependency_overrides[require_terms] = lambda: None
    return app


@pytest.fixture
async def client(session_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=session_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
async def learner(pg_db: Db, settings: Settings) -> Learner:
    return await make_learner(pg_db, settings)
