"""FastAPI application factory: pooled resources in the lifespan, request/trace ids,
unified errors, health endpoints and the product routers (ТЗ Фазы 3 WP1)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from quack.api.errors import install_error_handlers
from quack.api.learner_stream import router as learner_stream_router
from quack.api.openapi import install_openapi, render_openapi
from quack.api.ratelimit import enforce_action_rate_limit
from quack.api.resources import AppResources, create_app_resources
from quack.artifacts.cards_routes import router as cards_router
from quack.artifacts.hub_cheatsheet_routes import router as hub_cheatsheet_router
from quack.artifacts.notes_routes import router as notes_router
from quack.artifacts.skills_routes import router as skills_router
from quack.artifacts.viz_routes import router as viz_router
from quack.auth.consents import has_current_terms
from quack.auth.google import AuthlibGoogleOIDC, GoogleOIDC
from quack.auth.router import router as auth_router
from quack.auth.sessions import resolve_session
from quack.canvas.router import router as canvas_router
from quack.config import Settings, get_settings
from quack.deletion.router import router as deletion_router
from quack.domains.router import router as domains_router
from quack.logging import configure_logging, get_logger, log_context
from quack.materials.router import router as materials_router
from quack.ops.status import collect_status
from quack.review.router import router as review_router
from quack.session.router import router as session_router

log = get_logger(__name__)


class HealthLive(BaseModel):
    status: str


class HealthReady(BaseModel):
    status: str
    checks: dict[str, str]


def _trace_id(request: Request) -> str:
    traceparent = request.headers.get("traceparent", "")
    parts = traceparent.split("-")
    if len(parts) == 4 and len(parts[1]) == 32:
        return parts[1]
    return request.headers.get("X-Trace-ID") or uuid.uuid4().hex


async def _check(probe: Callable[[], Awaitable[object]], timeout_s: float) -> str:
    try:
        async with asyncio.timeout(timeout_s):
            await probe()
        return "ok"
    except Exception:
        return "error"


def _health_router() -> APIRouter:
    router = APIRouter(prefix="/health", tags=["health"])

    @router.get("/live", response_model=HealthLive)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get(
        "/ready",
        response_model=HealthReady,
        responses={503: {"model": HealthReady, "description": "a dependency is down"}},
    )
    async def ready(request: Request) -> JSONResponse:
        resources: AppResources | None = getattr(request.app.state, "resources", None)
        if resources is None:
            checks = {"postgres": "error", "neo4j": "error", "redis": "error"}
        else:

            async def postgres() -> None:
                await resources.db.fetch_one("SELECT 1")

            async def redis() -> None:
                if resources.redis is None:
                    raise RuntimeError("redis is not configured")
                await resources.redis.ping()  # type: ignore[misc]

            timeout_s = resources.settings.health_check_timeout_s
            checks = {
                "postgres": await _check(postgres, timeout_s),
                "neo4j": await _check(resources.neo4j.verify_connectivity, timeout_s),
                "redis": await _check(redis, timeout_s),
            }
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            {"status": "ok" if ok else "error", "checks": checks}, status_code=200 if ok else 503
        )

    return router


class Healthz(BaseModel):
    status: str


def _healthz_router() -> APIRouter:
    """`GET /healthz` (WP8.3, `Д-§9.1`): liveness by Postgres only — the teacher turn survives
    Neo4j and Redis outages, `ops status` shows them."""
    router = APIRouter(tags=["health"])

    @router.get(
        "/healthz",
        response_model=Healthz,
        responses={503: {"model": Healthz, "description": "Postgres is unreachable"}},
    )
    async def healthz(request: Request) -> JSONResponse:
        resources: AppResources | None = getattr(request.app.state, "resources", None)
        status = "error"
        if resources is not None:

            async def postgres() -> None:
                await resources.db.fetch_one("SELECT 1")

            status = await _check(postgres, resources.settings.health_check_timeout_s)
        return JSONResponse({"status": status}, status_code=200 if status == "ok" else 503)

    return router


def _internal_router() -> APIRouter:
    """`GET /internal/status` (WP9.5, `Д-§9.1`): the report of `ops status`, for the Docker
    network only. Caddy does not proxy `/internal/*` (D-C-212), and the route is not in the
    OpenAPI snapshot of the frontend."""
    router = APIRouter(prefix="/internal", include_in_schema=False)

    @router.get("/status")
    async def status(request: Request) -> JSONResponse:
        resources: AppResources | None = getattr(request.app.state, "resources", None)
        if resources is None:
            return JSONResponse({"status": "error"}, status_code=503)
        report, code = await collect_status(
            resources.settings, resources.db, resources.neo4j, resources.redis
        )
        return JSONResponse(report, status_code=200 if code == 0 else 503)

    return router


def create_app(
    settings: Settings | None = None,
    *,
    resources: AppResources | None = None,
    google_oidc: GoogleOIDC | None = None,
) -> FastAPI:
    """The API application. `resources` are the process resources: tests pass their own and
    keep ownership of them; `None` builds them in the lifespan and closes them on shutdown."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.secret_values())
    # Д-§9.1: Uvicorn worker processes (`api_workers > 1`) call this factory themselves and do
    # not inherit the CLI's contextvars; the role comes from `PROCESS_ROLE` in the environment.
    structlog.contextvars.bind_contextvars(
        process=settings.process_role, version=settings.quack_version
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        built = None if resources is not None else await create_app_resources(settings)
        if built is not None:
            app.state.resources = built
        try:
            yield
        finally:
            if built is not None:
                await built.close()
                app.state.resources = None

    app = FastAPI(title="Quack API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.resources = resources
    # ТЗ Ф4 WP18: the API base reads sessions and consents through these (§4.1, D-C-115).
    app.state.session_resolver = resolve_session
    app.state.terms_checker = has_current_terms
    app.state.google_oidc = google_oidc or AuthlibGoogleOIDC(settings)
    install_error_handlers(app)
    install_openapi(app)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        trace_id = _trace_id(request)
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        with log_context(request_id=request_id, trace_id=trace_id):
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-ID"] = trace_id
        return response

    # ТЗ Ф4 WP9.2, Д-§2.1: the frontend on app./preview.qvack.online calls this API with the
    # session cookie. An exact origin list with credentials, never `*`. Added last, so it is the
    # outermost layer and answers a preflight before routing and authentication.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Trace-ID"],
    )
    app.include_router(_health_router())
    app.include_router(_healthz_router())
    app.include_router(_internal_router())
    # Domains and artifacts attach authentication and the `actions` limit in their routers
    # (public `register`/`login` carry the `auth` limit); the session router is wrapped here.
    app.include_router(domains_router)
    app.include_router(review_router)
    app.include_router(deletion_router)
    app.include_router(session_router, dependencies=[Depends(enforce_action_rate_limit)])
    app.include_router(notes_router)
    app.include_router(cards_router)
    app.include_router(skills_router)
    app.include_router(viz_router)
    app.include_router(hub_cheatsheet_router)
    app.include_router(learner_stream_router)
    app.include_router(auth_router)
    app.include_router(materials_router)
    app.include_router(canvas_router)
    return app


def write_openapi(path: Path) -> None:
    """`ops openapi --out PATH`: write the schema of `create_app` (WP8.7). Lives in the
    assembly layer because it builds the app (§4.1, PHASE3_NOTES D-C-015)."""
    path.write_bytes(render_openapi(create_app()))
