"""Assembled API: one JWT contract and auth before validation (ТЗ Фазы 3 §8.2, WP1.2)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.app import create_app
from quack.api.errors import ErrorResponse
from quack.config import Settings
from quack.db.postgres import SessionFactory
from tests.support.resources import build_app_resources

PUBLIC_ROUTES = {("POST", "/v1/auth/register"), ("POST", "/v1/auth/login")}
PATH_PARAM = re.compile(r"\{[^}]+\}")
# One GET route of each product package (T-A3).
PACKAGE_PROBES = ("/v1/domains", "/v1/sessions/active", "/v1/artifacts")


@pytest.fixture
def assembled_app(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> FastAPI:
    app = create_app(settings, connect=False)
    app.state.resources = build_app_resources(settings, pg_engine, neo4j, redis, session_factory=db)
    return app


@pytest.fixture
async def api(assembled_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=assembled_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _protected_routes(app: FastAPI) -> Iterator[tuple[str, str]]:
    """`(method, path)` of `/v1/*` from the OpenAPI schema: since FastAPI 0.162
    `include_router` keeps routers lazily, so `app.routes` does not list their routes."""
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for method in operations:
            if method in HTTP_METHODS and (method.upper(), path) not in PUBLIC_ROUTES:
                yield method.upper(), path


def _assert_unauthorized(response: httpx.Response, what: str) -> None:
    assert response.status_code == 401, f"{what}: {response.status_code} {response.text}"
    assert ErrorResponse.model_validate(response.json()).error.code == "UNAUTHORIZED", what


async def test_every_protected_route_requires_auth_before_validation(
    assembled_app: FastAPI, api: httpx.AsyncClient
) -> None:
    """T-A2: no `Authorization`, valid UUIDs in the path, empty body → 401 everywhere."""
    routes = sorted(set(_protected_routes(assembled_app)))
    for method, path in (("GET", p) for p in PACKAGE_PROBES):
        assert (method, path) in routes, f"{method} {path} is not mounted"
    for method, path in routes:
        url = PATH_PARAM.sub(lambda _: str(uuid4()), path)
        response = await api.request(method, url)
        _assert_unauthorized(response, f"{method} {path}")


def _foreign_token(learner_id: str) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": learner_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=1)).timestamp()),
    }
    return jwt.encode(claims, "not-the-quack-secret-0123456789abcdef", algorithm="HS256")


async def test_single_jwt_contract_across_packages(api: httpx.AsyncClient) -> None:
    """T-A3: a login token works in domains, session and artifacts; after
    `DELETE /v1/learner` and with a foreign signature it gets 401 in all three."""
    email = f"t-a3-{uuid4().hex[:12]}@example.com"
    password = "correct-horse-battery"
    registered = await api.post("/v1/auth/register", json={"email": email, "password": password})
    assert registered.status_code == 201, registered.text
    login = await api.post("/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["token"]
    learner_id = login.json()["learner_id"]
    headers = {"Authorization": f"Bearer {token}"}

    for path in PACKAGE_PROBES:
        response = await api.get(path, headers=headers)
        assert response.status_code == 200, f"{path}: {response.text}"

    foreign = {"Authorization": f"Bearer {_foreign_token(learner_id)}"}
    for path in PACKAGE_PROBES:
        _assert_unauthorized(await api.get(path, headers=foreign), f"foreign signature {path}")

    deleted = await api.delete("/v1/learner", headers=headers)
    assert deleted.status_code == 200, deleted.text
    for path in PACKAGE_PROBES:
        _assert_unauthorized(await api.get(path, headers=headers), f"after delete {path}")
