"""Assembled API: routes, one JWT contract, auth before validation and the single rate
limiter (ТЗ Фазы 3 §8.2, WP1.1, WP1.2, WP1.5)."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.app import create_app, write_openapi
from quack.api.errors import ErrorResponse
from quack.api.openapi import render_openapi
from quack.config import Settings
from quack.db.postgres import SessionFactory, create_engine
from quack.db.redis import ratelimit_key
from tests.support.broken import BrokenNeo4jDriver, BrokenRedis
from tests.support.resources import build_app_resources

PUBLIC_ROUTES = {("POST", "/v1/auth/register"), ("POST", "/v1/auth/login")}
PATH_PARAM = re.compile(r"\{[^}]+\}")
# One GET route of each product package (T-A3).
PACKAGE_PROBES = ("/v1/domains", "/v1/sessions/active", "/v1/artifacts")


type AppFactory = Callable[..., FastAPI]


@pytest.fixture
def make_app(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> AppFactory:
    """`create_app(settings, resources=...)` over the test pools. `overrides` replace
    settings fields; `redis=` replaces the Redis client (for example `BrokenRedis()`)."""

    def make(*, redis_client: Any = redis, **overrides: Any) -> FastAPI:
        cfg = settings.model_copy(update=overrides)
        resources = build_app_resources(cfg, pg_engine, neo4j, redis_client, session_factory=db)
        return create_app(cfg, resources=resources)

    return make


@pytest.fixture
def assembled_app(make_app: AppFactory) -> FastAPI:
    return make_app()


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


# --- T-A1: the endpoint set equals the contract -------------------------------------------

# ТЗ-П1 §3 (auth, learner, domains, structure, diagnostic, graph, topics; the short topic
# routes are D-7.1 of the remediation notes), ТЗ-П2 §3 (sessions), ТЗ-П3 §3 (practice,
# artifacts, search, cheatsheets, export), `/health/*`. The Phase 2 documents are not in the
# repository, so the set is kept in step with the router docstrings and the Phase 2 notes.
EXPECTED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        # stream 1
        ("POST", "/v1/auth/register"),
        ("POST", "/v1/auth/login"),
        ("GET", "/v1/auth/me"),
        ("PATCH", "/v1/learner/preferences"),
        ("DELETE", "/v1/learner"),
        ("GET", "/v1/domains"),
        ("POST", "/v1/domains"),
        ("GET", "/v1/domains/{domain_id}"),
        ("POST", "/v1/domains/{domain_id}/archive"),
        ("POST", "/v1/domains/{domain_id}/structure/propose"),
        ("PATCH", "/v1/domains/{domain_id}/structure"),
        ("POST", "/v1/domains/{domain_id}/structure/confirm"),
        ("POST", "/v1/domains/{domain_id}/diagnostic/start"),
        ("POST", "/v1/domains/{domain_id}/diagnostic/answer"),
        ("POST", "/v1/domains/{domain_id}/diagnostic/finish"),
        ("GET", "/v1/domains/{domain_id}/graph"),
        ("GET", "/v1/domains/{domain_id}/topics/{topic_id}"),
        ("PATCH", "/v1/domains/{domain_id}/topics/{topic_id}/status"),
        ("GET", "/v1/topics/{topic_id}"),
        ("PATCH", "/v1/topics/{topic_id}/status"),
        # stream 2
        ("POST", "/v1/sessions"),
        ("GET", "/v1/sessions/active"),
        ("GET", "/v1/sessions/{session_id}"),
        ("POST", "/v1/sessions/{session_id}/heartbeat"),
        ("POST", "/v1/sessions/{session_id}/finish"),
        ("POST", "/v1/sessions/{session_id}/turns"),
        ("GET", "/v1/sessions/{session_id}/events"),
        # stream 3
        ("POST", "/v1/sessions/{session_id}/tasks/next"),
        ("POST", "/v1/sessions/{session_id}/tasks/{task_id}/hint"),
        ("POST", "/v1/sessions/{session_id}/tasks/{task_id}/submit"),
        ("GET", "/v1/artifacts"),
        ("POST", "/v1/artifacts/notes"),
        ("PATCH", "/v1/artifacts/notes/{artifact_id}"),
        ("DELETE", "/v1/artifacts/notes/{artifact_id}"),
        ("GET", "/v1/artifacts/search"),
        ("GET", "/v1/topics/{topic_id}/cheatsheet"),
        ("POST", "/v1/topics/{topic_id}/cheatsheet/refresh"),
        ("GET", "/v1/domains/{domain_id}/export"),
        # §6.4: new endpoints of Phase 3
        ("GET", "/v1/learner/stream"),
        ("GET", "/healthz"),
    }
)
# Endpoints of §6.4 whose packages are not done yet (PHASE3_NOTES, O9). The entry is removed
# by the package that mounts the route; `test_pending_routes_are_still_pending` fails otherwise.
PENDING_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/v1/learner/stream"): "WP7",
}


def _declared_routes() -> list[tuple[str, str]]:
    """`(method, path)` of every route as the routers declare it, with duplicates kept."""
    from quack.artifacts.router import router as artifacts_router
    from quack.domains.router import _protected, _public
    from quack.session.router import router as session_router

    declared: list[tuple[str, str]] = []
    for router in (_public, _protected, session_router, artifacts_router):
        for route in router.routes:
            assert isinstance(route, APIRoute), route
            declared.extend((method, route.path) for method in sorted(route.methods or ()))
    return declared


def _app_routes(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method in HTTP_METHODS
    }


def test_app_routes_match_contract(assembled_app: FastAPI) -> None:
    """T-A1: the route set equals the contract, with no duplicate `(method, path)`."""
    declared = _declared_routes()
    duplicates = sorted({route for route in declared if declared.count(route) > 1})
    assert duplicates == []
    actual = _app_routes(assembled_app)
    assert set(declared) <= actual
    assert actual == EXPECTED_ROUTES - PENDING_ROUTES.keys()


def test_pending_routes_are_still_pending(assembled_app: FastAPI) -> None:
    mounted = _app_routes(assembled_app) & PENDING_ROUTES.keys()
    assert mounted == set(), f"remove from PENDING_ROUTES: {sorted(mounted)}"


async def test_static_session_route_wins_over_parametric(api: httpx.AsyncClient) -> None:
    """T-A1: `GET /v1/sessions/active` answers 200 `null`, not 422 from `{session_id}`."""
    email = f"t-a1-{uuid4().hex[:12]}@example.com"
    registered = await api.post(
        "/v1/auth/register", json={"email": email, "password": "correct-horse-battery"}
    )
    assert registered.status_code == 201, registered.text
    headers = {"Authorization": f"Bearer {registered.json()['token']}"}
    response = await api.get("/v1/sessions/active", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() is None


def test_create_app_without_resources_builds_them_in_lifespan(settings: Settings) -> None:
    """T-A1 / WP1.1: `resources=None` is the production path; the flag `connect` is gone."""
    assert list(inspect.signature(create_app).parameters) == ["settings", "resources"]
    app = create_app(settings)
    assert app.state.resources is None  # built by the lifespan, not at creation


# --- T-A4, T-A5: rate limits ----------------------------------------------------------------


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _register(client: httpx.AsyncClient) -> tuple[str, dict[str, str]]:
    email = f"rl-{uuid4().hex[:12]}@example.com"
    response = await client.post(
        "/v1/auth/register", json={"email": email, "password": "correct-horse-battery"}
    )
    assert response.status_code == 201, response.text
    return response.json()["learner_id"], {"Authorization": f"Bearer {response.json()['token']}"}


async def test_rate_limit_local_fallback_without_redis(make_app: AppFactory) -> None:
    """T-A4: with Redis down 10 logins from one IP pass, the 11th is `429 RATE_LIMITED`."""
    app = make_app(redis_client=BrokenRedis(), auth_requests_per_ip_per_min=10)
    async with _client(app) as client:
        body = {"email": "nobody@example.com", "password": "wrong-password-123"}
        for attempt in range(10):
            response = await client.post("/v1/auth/login", json=body)
            assert response.status_code == 401, f"attempt {attempt}: {response.text}"
        blocked = await client.post("/v1/auth/login", json=body)
        assert blocked.status_code == 429, blocked.text
        error = ErrorResponse.model_validate(blocked.json()).error
        assert error.code == "RATE_LIMITED"
        assert error.details["retry_after_sec"] > 0


async def test_actions_and_turns_rate_limits(make_app: AppFactory, redis: Redis) -> None:
    """T-A5: `learner_actions_per_min` for POST/PATCH/DELETE, `turns_per_min` for turns,
    GET unlimited, heartbeat outside `actions`, Redis keys carry a TTL."""
    app = make_app(learner_actions_per_min=3, turns_per_min=1)
    async with _client(app) as client:
        learner_id, headers = await _register(client)
        for i in range(3):
            created = await client.post(
                "/v1/domains",
                json={"name": f"domain {i}", "idempotency_key": str(uuid4())},
                headers=headers,
            )
            assert created.status_code == 201, created.text
        fourth = await client.post(
            "/v1/domains", json={"name": "four", "idempotency_key": str(uuid4())}, headers=headers
        )
        assert fourth.status_code == 429, fourth.text
        assert fourth.json()["error"]["code"] == "RATE_LIMITED"
        assert fourth.json()["error"]["details"]["retry_after_sec"] > 0
        listed = await client.get("/v1/domains", headers=headers)
        assert listed.status_code == 200, listed.text
        assert await redis.ttl(ratelimit_key("actions", learner_id)) > 0

        # a new learner: heartbeats do not spend `actions`, so three creations still fit
        other_id, other = await _register(client)
        for _ in range(5):
            beat = await client.post(f"/v1/sessions/{uuid4()}/heartbeat", headers=other)
            assert beat.status_code == 404, beat.text
        for i in range(3):
            created = await client.post(
                "/v1/domains",
                json={"name": f"other {i}", "idempotency_key": str(uuid4())},
                headers=other,
            )
            assert created.status_code == 201, created.text
        assert await redis.get(ratelimit_key("actions", other_id)) == "3"

        # turns: the second turn of the window is refused (the session does not matter)
        third_id, third = await _register(client)
        first_turn = await client.post(
            f"/v1/sessions/{uuid4()}/turns",
            json={"text": "hi", "idempotency_key": str(uuid4())},
            headers=third,
        )
        assert first_turn.status_code != 429, first_turn.text
        second_turn = await client.post(
            f"/v1/sessions/{uuid4()}/turns",
            json={"text": "hi again", "idempotency_key": str(uuid4())},
            headers=third,
        )
        assert second_turn.status_code == 429, second_turn.text
        assert await redis.ttl(ratelimit_key("turns", third_id)) > 0


async def test_turn_limit_without_redis_is_local(make_app: AppFactory) -> None:
    """T-E8 in small: the turn limit does not fail open when Redis is down."""
    app = make_app(redis_client=BrokenRedis(), turns_per_min=1)
    async with _client(app) as client:
        _, headers = await _register(client)
        for expected_blocked in (False, True):
            response = await client.post(
                f"/v1/sessions/{uuid4()}/turns",
                json={"text": "hi", "idempotency_key": str(uuid4())},
                headers=headers,
            )
            assert (response.status_code == 429) is expected_blocked, response.text


# --- WP1.6: thresholds read from Settings at request time ------------------------------------


async def test_search_limits_come_from_settings(make_app: AppFactory) -> None:
    app = make_app(search_min_query_len=4, search_default_limit=7, search_max_limit=9)
    async with _client(app) as client:
        _, headers = await _register(client)
        too_short = await client.get("/v1/artifacts/search", params={"q": "abc"}, headers=headers)
        assert too_short.status_code == 422, too_short.text
        assert too_short.json()["error"]["code"] == "VALIDATION_ERROR"
        too_many = await client.get(
            "/v1/artifacts/search", params={"q": "abcd", "limit": 10}, headers=headers
        )
        assert too_many.status_code == 422, too_many.text
        ok = await client.get(
            "/v1/artifacts/search", params={"q": "abcd", "limit": 9}, headers=headers
        )
        assert ok.status_code == 200, ok.text
        default = await client.get("/v1/artifacts/search", params={"q": "abcd"}, headers=headers)
        assert default.status_code == 200, default.text


class _SlowNeo4j:
    """Neo4j driver stand-in whose connectivity check takes 0.3 s."""

    async def verify_connectivity(self) -> None:
        await asyncio.sleep(0.3)

    async def close(self) -> None:
        return None


@pytest.mark.parametrize(("timeout_s", "neo4j_state"), [(0.05, "error"), (2.0, "ok")])
async def test_health_probe_timeout_comes_from_settings(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    redis: Redis,
    timeout_s: float,
    neo4j_state: str,
) -> None:
    cfg = settings.model_copy(update={"health_check_timeout_s": timeout_s})
    resources = build_app_resources(cfg, pg_engine, _SlowNeo4j(), redis, session_factory=db)
    async with _client(create_app(cfg, resources=resources)) as client:
        response = await client.get("/health/ready")
    assert response.json()["checks"]["neo4j"] == neo4j_state, response.text


# --- WP8.3: /healthz -----------------------------------------------------------------------


async def test_healthz_depends_only_on_postgres(
    settings: Settings, pg_engine: AsyncEngine, db: SessionFactory
) -> None:
    """T-A8: Neo4j and Redis broken → `/healthz` 200; Postgres unreachable → 503. No auth."""
    resources = build_app_resources(
        settings, pg_engine, BrokenNeo4jDriver(), BrokenRedis(), session_factory=db
    )
    async with _client(create_app(settings, resources=resources)) as client:
        ok = await client.get("/healthz")
    assert (ok.status_code, ok.json()) == (200, {"status": "ok"}), ok.text

    dead = create_engine(settings.model_copy(update={"postgres_dsn": DEAD_POSTGRES_DSN}))
    try:
        broken = build_app_resources(settings, dead, BrokenNeo4jDriver(), BrokenRedis())
        async with _client(create_app(settings, resources=broken)) as client:
            down = await client.get("/healthz")
    finally:
        await dead.dispose()
    assert (down.status_code, down.json()) == (503, {"status": "error"}), down.text


DEAD_POSTGRES_DSN = "postgresql://quack:quack@127.0.0.1:1/quack_test"


# --- WP8.7: OpenAPI snapshot -------------------------------------------------------------

OPENAPI_SNAPSHOT = Path(__file__).resolve().parent.parent / "openapi.json"
ERROR_REF = "#/components/schemas/ErrorResponse"
MUTATING = {"post", "patch", "delete"}


def _snapshot_app() -> FastAPI:
    """The app as `ops openapi` builds it: no resources, the schema does not need them."""
    return create_app(Settings())


def test_openapi_snapshot_is_current() -> None:
    """T-A7: the schema of the current `create_app`, serialized as `ops openapi` does, is
    byte-equal to the committed `backend/openapi.json` (regenerate: `ops openapi --out`)."""
    assert OPENAPI_SNAPSHOT.exists(), "backend/openapi.json is not committed"
    assert render_openapi(_snapshot_app()) == OPENAPI_SNAPSHOT.read_bytes(), (
        "backend/openapi.json is stale: run `python -m quack.cli ops openapi --out openapi.json`"
    )


def test_write_openapi_is_deterministic(tmp_path: Path) -> None:
    """WP8.7: `ops openapi` output is deterministic, with sorted keys."""
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    write_openapi(first)
    write_openapi(second)
    raw = first.read_bytes()
    assert raw == second.read_bytes()
    assert raw == render_openapi(_snapshot_app())
    assert raw.endswith(b"\n")
    parsed = json.loads(raw)
    assert json.dumps(parsed, sort_keys=True, indent=2, ensure_ascii=False) + "\n" == raw.decode()


def _error_codes(operation: dict[str, Any]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for status, response in operation["responses"].items():
        if status.startswith(("4", "5")):
            schema = response.get("content", {}).get("application/json", {}).get("schema", {})
            out[status] = schema.get("$ref")
    return out


def test_openapi_documents_every_response() -> None:
    """WP8.7: every endpoint has a response model; errors use the common `ErrorResponse`
    (`С-§8.5`) for 401, 404, 409, 422, 429, 503 where the route can return them."""
    schema = _snapshot_app().openapi()
    assert "ErrorResponse" in schema["components"]["schemas"]
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            what = f"{method.upper()} {path}"
            ok = [r for s, r in operation["responses"].items() if s.startswith("2")]
            assert len(ok) == 1, what
            content = ok[0].get("content", {})
            assert content, f"{what}: no response body"
            for media in content.values():
                assert media.get("schema"), f"{what}: response without a model"
            if not path.startswith("/v1/"):
                continue  # `/health/*` keep their own bodies (WP8.3)
            errors = _error_codes(operation)
            expected = {"503"}
            if (method.upper(), path) not in PUBLIC_ROUTES:
                expected.add("401")
            if PATH_PARAM.search(path):
                expected.add("404")
            if method in MUTATING:
                expected.add("409")
                if not path.endswith("/heartbeat"):
                    expected.add("429")
            if operation.get("parameters") or operation.get("requestBody"):
                expected.add("422")
            assert set(errors) == expected, f"{what}: {sorted(errors)} != {sorted(expected)}"
            assert all(ref == ERROR_REF for ref in errors.values()), what


def test_sse_endpoints_document_event_stream() -> None:
    """WP8.7: SSE endpoints (`С-§8.5`) describe a `text/event-stream` body."""
    paths = _snapshot_app().openapi()["paths"]
    for path in ("/v1/sessions/{session_id}/turns", "/v1/sessions/{session_id}/events"):
        (operation,) = paths[path].values()
        assert list(operation["responses"]["200"]["content"]) == ["text/event-stream"], path
