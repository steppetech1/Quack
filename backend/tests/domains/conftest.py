"""Shared test fixtures for `tests/domains/` (ТЗ §0.1, §5).

Avoids `TestClient` event-loop conflicts with asyncpg/Neo4j by using `httpx.AsyncClient`
with `ASGITransport` and attaching existing test pools directly to `app.state.resources`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.app import create_app
from quack.api.auth import DEV_JWT_SECRET, JWT_ALGORITHM
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.domains import curation
from quack.events.append import append_events
from quack.events.models import NewEvent
from tests.support.llm import FakeLLMProvider, QueuedLLMItem
from tests.support.resources import build_app_resources

__all__ = ["FakeLLMProvider", "QueuedLLMItem"]


@pytest.fixture
def fake_llm_provider() -> FakeLLMProvider:
    provider = FakeLLMProvider()
    curation.llm_provider_override = provider
    return provider


@pytest.fixture
def domains_app(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
    fake_llm_provider: FakeLLMProvider,
) -> FastAPI:
    resources = build_app_resources(
        settings, pg_engine, neo4j, redis, session_factory=db, provider=fake_llm_provider
    )
    return create_app(settings, resources=resources)


@pytest.fixture
async def api_client(domains_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=domains_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def seeded_learner(
    db: SessionFactory,
    settings: Settings,
) -> tuple[UUID, str]:
    """Seed a test learner directly into `auth.users` + `core.events` (`learner.registered`)
    and return `(learner_id, bearer_jwt)` so Contour 2 and Contour 3 tests do not depend on
    `/v1/auth/register` being implemented yet.
    """
    learner_id = uuid4()
    email = f"learner-{learner_id.hex[:8]}@example.com"
    async with db() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO auth.users (id, email, password_hash, display_name, preferences) "
                "VALUES (:id, :email, :pw, :name, CAST(:prefs AS JSONB))"
            ),
            {
                "id": learner_id,
                "email": email,
                "pw": "$argon2id$v=19$m=65536,t=3,p=4$seeded$seeded",
                "name": "Seeded Learner",
                "prefs": "{}",
            },
        )
        await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="learner.registered",
                    actor="learner",
                    idempotency_key=f"register:{learner_id}",
                    payload={
                        "email": email,
                        "display_name": "Seeded Learner",
                        "preferences": {},
                    },
                )
            ],
            publish_wakeup=False,
        )
    now = datetime.now(UTC)
    secret = settings.jwt_secret.get_secret_value() or DEV_JWT_SECRET
    token = jwt.encode(
        {
            "sub": str(learner_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=7)).timestamp()),
        },
        secret,
        algorithm=JWT_ALGORITHM,
    )
    return learner_id, token
