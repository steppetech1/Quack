"""Shared test fixtures for `tests/domains/` (ТЗ §0.1, §5).

Avoids `TestClient` event-loop conflicts with asyncpg/Neo4j by using `httpx.AsyncClient`
with `ASGITransport` and attaching existing test pools directly to `app.state.resources`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import jwt
import orjson
import pytest
from fastapi import FastAPI, Request
from neo4j import AsyncDriver
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.app import AppResources, create_app
from quack.api.errors import ApiError
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.domains import curation
from quack.domains.auth import DEV_JWT_SECRET, JWT_ALGORITHM, get_current_learner_id
from quack.domains.router import router as domains_router
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.llm.provider import ProviderRequest, ProviderResponse

type QueuedLLMItem = dict[str, Any] | BaseModel | str | Exception


class FakeLLMProvider:
    """In-memory `LLMProvider` test double recording `ProviderRequest` calls and returning
    queued structured responses (`dict`, `BaseModel`, JSON `str`, or raising `Exception`).
    """

    def __init__(self, responses: Sequence[QueuedLLMItem] | None = None) -> None:
        self.responses: list[QueuedLLMItem] = list(responses or [])
        self.calls: list[ProviderRequest] = []

    def enqueue(self, *items: QueuedLLMItem) -> None:
        self.responses.extend(items)

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError(
                f"FakeLLMProvider queue is empty for schema {request.schema_name!r}"
            )
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, BaseModel):
            content = item.model_dump_json()
        elif isinstance(item, dict):
            content = orjson.dumps(item).decode()
        else:
            content = item
        return ProviderResponse(
            content=content,
            input_tokens=100,
            output_tokens=40,
            cached_tokens=0,
            model=request.model,
        )


def _decode_bearer_fallback(request: Request, settings: Settings) -> UUID:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise ApiError("UNAUTHORIZED", "missing bearer token", status_code=401)
    token = header.removeprefix("Bearer ").strip()
    secret = settings.jwt_secret.get_secret_value() or DEV_JWT_SECRET
    try:
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
        return UUID(str(payload["sub"]))
    except Exception as exc:
        raise ApiError("UNAUTHORIZED", "invalid token", status_code=401) from exc


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
    app = create_app(settings, connect=False)
    app.state.resources = AppResources(
        engine=pg_engine,
        session_factory=db,
        neo4j=neo4j,
        redis=redis,
    )
    app.state.llm_provider = fake_llm_provider
    app.include_router(domains_router)

    async def _auth_dep_with_stage2_fallback(request: Request) -> UUID:
        try:
            return await get_current_learner_id(request)
        except NotImplementedError:
            return _decode_bearer_fallback(request, settings)

    app.dependency_overrides[get_current_learner_id] = _auth_dep_with_stage2_fallback
    return app


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
