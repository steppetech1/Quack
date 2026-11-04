"""Assembled API: routes, one session contract, auth before validation and the single rate
limiter (ТЗ Фазы 3 §8.2, WP1.1, WP1.2, WP1.5; ТЗ Ф4 WP18.3 — sign-in is a session)."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.api.app import create_app, write_openapi
from quack.api.errors import ErrorResponse
from quack.api.openapi import render_openapi
from quack.config import Settings
from quack.db import Db, open_db
from quack.db.redis import ratelimit_key
from tests.e2e.routes import declared_routers, expected_routes
from tests.support.auth import session_cookie, session_token
from tests.support.broken import BrokenNeo4jDriver, BrokenRedis
from tests.support.resources import build_app_resources

PUBLIC_ROUTES = {
    # ТЗ Ф4 WP18: sign-in routes.
    ("POST", "/auth/link"),
    ("GET", "/auth/link/verify"),
    ("GET", "/auth/google/start"),
    ("GET", "/auth/google/callback"),
    ("POST", "/companion/pair"),
}
# Т-§19.1: the start of the Google flow is a redirect only, it has no 2xx body.
REDIRECT_ONLY_ROUTES = {("GET", "/auth/google/start")}
PATH_PARAM = re.compile(r"\{[^}]+\}")
# One GET route of each product package (T-A3).
PACKAGE_PROBES = ("/v1/domains", "/v1/sessions/active", "/v1/me")


type AppFactory = Callable[..., FastAPI]


@pytest.fixture
def make_app(
    settings: Settings,
    pg_db: Db,
    neo4j: AsyncDriver,
    redis: Redis,
) -> AppFactory:
    """`create_app(settings, resources=...)` over the test pools. `overrides` replace
    settings fields; `redis=` replaces the Redis client (for example `BrokenRedis()`)."""

    def make(*, redis_client: Any = redis, **overrides: Any) -> FastAPI:
        cfg = settings.model_copy(update=overrides)
        resources = build_app_resources(cfg, neo4j, redis_client, db=pg_db)
        return create_app(cfg, resources=resources)

    return make


type SignIn = Callable[[], Awaitable[tuple[str, dict[str, str]]]]


@pytest.fixture
def sign_in(pg_db: Db, settings: Settings) -> SignIn:
    """A new learner with a Phase 4 session (ТЗ Ф4 WP18.3): `(learner_id, headers)`.
    `/v1/auth/register` is removed (D-A-113); the `core.learners` row is the account (domains,
    preferences, D-A-207; `auth.users` of Phase 3 is not read any more)."""

    async def make() -> tuple[str, dict[str, str]]:
        learner_id = uuid4()
        await pg_db.execute(
            "INSERT INTO core.learners (learner_id, email) VALUES (%(id)s, %(email)s)",
            {"id": learner_id, "email": f"{learner_id}@test.invalid"},
        )
        token = await session_token(pg_db, learner_id, settings)
        return str(learner_id), session_cookie(token)

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
    assert ErrorResponse.model_validate(response.json()).error.code == "UNAUTHENTICATED", what


async def test_every_protected_route_requires_auth_before_validation(
    assembled_app: FastAPI, api: httpx.AsyncClient
) -> None:
    """T-A2: no session cookie, valid UUIDs in the path, empty body → 401 everywhere."""
    routes = sorted(set(_protected_routes(assembled_app)))
    for method, path in (("GET", p) for p in PACKAGE_PROBES):
        assert (method, path) in routes, f"{method} {path} is not mounted"
    for method, path in routes:
        url = PATH_PARAM.sub(lambda _: str(uuid4()), path)
        response = await api.request(method, url)
        _assert_unauthorized(response, f"{method} {path}")


async def test_single_session_contract_across_packages(
    api: httpx.AsyncClient, sign_in: SignIn
) -> None:
    """T-A3: one session works in domains, session and the account; after `DELETE /v1/learner`
    and with an unknown cookie it gets 401 in all three (ТЗ Ф4 WP18.3)."""
    _, headers = await sign_in()

    for path in PACKAGE_PROBES:
        response = await api.get(path, headers=headers)
        assert response.status_code == 200, f"{path}: {response.text}"

    foreign = session_cookie("not-a-session-token")
    for path in PACKAGE_PROBES:
        _assert_unauthorized(await api.get(path, headers=foreign), f"unknown session {path}")

    deleted = await api.delete("/v1/learner", headers=headers)
    assert deleted.status_code == 200, deleted.text
    for path in PACKAGE_PROBES:
        _assert_unauthorized(await api.get(path, headers=headers), f"after delete {path}")


# --- T-A1: the endpoint set equals the contract -------------------------------------------

# The contract is the union of what the streams register in `tests/e2e/routes/` (D-C-147): ТЗ-П1
# §3, ТЗ-П2 §3, ТЗ-П3 §3, ТЗ Ф4 §6.4 and `/health/*`; a stream adds its route in its own module.
EXPECTED_ROUTES: frozenset[tuple[str, str]] = expected_routes()
# Endpoints of §6.4 whose packages are not done yet (PHASE3_NOTES, O9). The entry is removed
# by the package that mounts the route; `test_pending_routes_are_still_pending` fails otherwise.
PENDING_ROUTES: dict[tuple[str, str], str] = {}


def _declared_routes() -> list[tuple[str, str]]:
    """`(method, path)` of every route as the routers declare it, with duplicates kept. The
    routers are the ones the streams name in `Stream.routers`."""
    declared: list[tuple[str, str]] = []
    for spec in declared_routers():
        module_name, attribute = spec.split(":")
        router = getattr(importlib.import_module(module_name), attribute)
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


async def test_static_session_route_wins_over_parametric(
    api: httpx.AsyncClient, sign_in: SignIn
) -> None:
    """T-A1: `GET /v1/sessions/active` answers 200 `null`, not 422 from `{session_id}`."""
    _, headers = await sign_in()
    response = await api.get("/v1/sessions/active", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() is None


def test_create_app_without_resources_builds_them_in_lifespan(settings: Settings) -> None:
    """T-A1 / WP1.1: `resources=None` is the production path; the flag `connect` is gone."""
    assert list(inspect.signature(create_app).parameters) == [
        "settings",
        "resources",
        "google_oidc",
    ]
    app = create_app(settings)
    assert app.state.resources is None  # built by the lifespan, not at creation


# --- T-A4, T-A5: rate limits ----------------------------------------------------------------


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_rate_limit_local_fallback_without_redis(
    make_app: AppFactory, sign_in: SignIn
) -> None:
    """T-A4: with Redis down 10 actions of a learner pass the limiter, the 11th is
    `429 RATE_LIMITED` (the per-IP `auth` limit went with `/v1/auth/login`, D-A-113). The
    body is empty: the limiter counts before validation, so the passing ones are 422."""
    app = make_app(redis_client=BrokenRedis(), learner_actions_per_min=10)
    _, headers = await sign_in()
    async with _client(app) as client:
        for attempt in range(10):
            response = await client.patch("/v1/learner/preferences", headers=headers)
            assert response.status_code == 422, f"attempt {attempt}: {response.text}"
        blocked = await client.patch("/v1/learner/preferences", headers=headers)
        assert blocked.status_code == 429, blocked.text
        error = ErrorResponse.model_validate(blocked.json()).error
        assert error.code == "RATE_LIMITED"
        assert error.details["retry_after_sec"] > 0


async def test_actions_and_turns_rate_limits(
    make_app: AppFactory, redis: Redis, sign_in: SignIn
) -> None:
    """T-A5: `learner_actions_per_min` for POST/PATCH/DELETE (one limit for every action, ТЗ Ф4
    §2.3 строка 16), GET unlimited, heartbeat outside `actions`, Redis keys carry a TTL."""
    app = make_app(learner_actions_per_min=3)
    async with _client(app) as client:
        learner_id, headers = await sign_in()
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
        other_id, other = await sign_in()
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


# --- WP1.6: thresholds read from Settings at request time ------------------------------------


async def test_search_limits_come_from_settings(make_app: AppFactory, sign_in: SignIn) -> None:
    app = make_app(search_min_query_len=4, search_default_limit=7, search_max_limit=9)
    async with _client(app) as client:
        _, headers = await sign_in()
        too_short = await client.get("/v1/search", params={"q": "abc"}, headers=headers)
        assert too_short.status_code == 422, too_short.text
        assert too_short.json()["error"]["code"] == "VALIDATION_ERROR"
        too_many = await client.get(
            "/v1/search", params={"q": "abcd", "limit": 10}, headers=headers
        )
        assert too_many.status_code == 422, too_many.text
        ok = await client.get("/v1/search", params={"q": "abcd", "limit": 9}, headers=headers)
        assert ok.status_code == 200, ok.text
        default = await client.get("/v1/search", params={"q": "abcd"}, headers=headers)
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
    redis: Redis,
    timeout_s: float,
    neo4j_state: str,
) -> None:
    cfg = settings.model_copy(update={"health_check_timeout_s": timeout_s})
    resources = build_app_resources(cfg, _SlowNeo4j(), redis)
    async with _client(create_app(cfg, resources=resources)) as client:
        response = await client.get("/health/ready")
    assert response.json()["checks"]["neo4j"] == neo4j_state, response.text


# --- WP8.3: /healthz -----------------------------------------------------------------------


async def test_healthz_depends_only_on_postgres(settings: Settings, pg_db: Db) -> None:
    """T-A8: Neo4j and Redis broken → `/healthz` 200; Postgres unreachable → 503. No auth.
    Postgres is asked through the `Db` of the resources (WP1.3)."""
    resources = build_app_resources(settings, BrokenNeo4jDriver(), BrokenRedis(), db=pg_db)
    async with _client(create_app(settings, resources=resources)) as client:
        ok = await client.get("/healthz")
    assert (ok.status_code, ok.json()) == (200, {"status": "ok"}), ok.text

    dead_db = await open_db(
        settings.model_copy(update={"postgres_dsn": DEAD_POSTGRES_DSN}), wait=False
    )
    try:
        broken = build_app_resources(
            settings,
            BrokenNeo4jDriver(),
            BrokenRedis(),
            db=dead_db,
        )
        async with _client(create_app(settings, resources=broken)) as client:
            down = await client.get("/healthz")
            ready = await client.get("/health/ready")
    finally:
        await dead_db.close()
    assert (down.status_code, down.json()) == (503, {"status": "error"}), down.text
    assert ready.status_code == 503 and ready.json()["checks"]["postgres"] == "error", ready.text


DEAD_POSTGRES_DSN = "postgresql://quack:quack@127.0.0.1:1/quack_test"


# --- WP8.7: OpenAPI snapshot -------------------------------------------------------------

OPENAPI_SNAPSHOT = Path(__file__).resolve().parent.parent / "openapi.json"
ERROR_REF = "#/components/schemas/ErrorResponse"
MUTATING = {"post", "patch", "delete"}
# ТЗ Ф4 §4.7: an action answers SSE (200) or plain JSON `201 {seq}`; every other route has one 2xx.
SUCCESS_CODES = {("POST", "/v1/sessions/{session_id}/actions"): {"200", "201"}}


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
            if (method.upper(), path) in REDIRECT_ONLY_ROUTES:
                continue
            ok = {s: r for s, r in operation["responses"].items() if s.startswith("2")}
            assert len(ok) == len(SUCCESS_CODES.get((method.upper(), path), {"2xx"})), what
            if (method.upper(), path) in SUCCESS_CODES:
                assert set(ok) == SUCCESS_CODES[(method.upper(), path)], what
            for response in ok.values():
                content = response.get("content", {})
                assert content, f"{what}: no response body"
                for media_type, media in content.items():
                    # FastAPI SSE (OpenAPI 3.2) documents the model of one event as `itemSchema`.
                    model = media.get("schema") or (
                        media.get("itemSchema") if media_type == "text/event-stream" else None
                    )
                    assert model, f"{what}: response without a model"
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


def test_openapi_refs_all_resolve() -> None:
    """Д-§5.2 п.7 (D-C-210): the schema feeds the type generator of the frontend, which fails on
    a `$ref` to a component that is not there (the stripped `HTTPValidationError` was left on
    `/auth/*` and `/companion/pair`)."""
    schema = _snapshot_app().openapi()
    components = set(schema["components"]["schemas"])
    refs = set(re.findall(r'"#/components/schemas/([^"]+)"', json.dumps(schema)))
    assert refs - components == set()


def test_sse_endpoints_document_event_stream() -> None:
    """WP8.7: SSE endpoints (`С-§8.5`) describe a `text/event-stream` body."""
    paths = _snapshot_app().openapi()["paths"]
    for path in (
        "/v1/sessions/{session_id}/events",
        "/v1/sessions/{session_id}/actions",
    ):
        (operation,) = paths[path].values()
        assert list(operation["responses"]["200"]["content"]) == ["text/event-stream"], path


def test_create_app_binds_process_and_version(settings: Settings) -> None:
    """WP8.1 (Д-§9.1): Uvicorn worker processes (`api_workers > 1`) build the app themselves and
    do not inherit the CLI's contextvars; `create_app` binds `process` and `version` itself."""
    import structlog

    structlog.contextvars.unbind_contextvars("process", "version")
    try:
        create_app(settings.model_copy(update={"process_role": "api", "quack_version": "4.5.6"}))
        bound = structlog.contextvars.get_contextvars()
        assert (bound.get("process"), bound.get("version")) == ("api", "4.5.6")
    finally:
        structlog.contextvars.unbind_contextvars("process", "version")
