"""FastAPI application factory: pooled resources in the lifespan, request/trace ids,
unified errors, health endpoints and the product routers (ТЗ Фазы 3 WP1)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from quack.api.errors import install_error_handlers
from quack.api.openapi import install_openapi, render_openapi
from quack.api.ratelimit import enforce_action_rate_limit
from quack.api.resources import AppResources, create_app_resources, get_resources
from quack.config import Settings, get_settings
from quack.logging import configure_logging, get_logger, log_context

# Re-exported for the files of stream B that still import them from here (D-C-014): the
# imports move to `quack.api.resources` in B's PRs and this line then goes away.
__all__ = ["AppResources", "create_app", "get_resources", "write_openapi"]

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
                async with resources.engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))

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


def create_app(
    settings: Settings | None = None, *, resources: AppResources | None = None
) -> FastAPI:
    """The API application. `resources` are the process resources: tests pass their own and
    keep ownership of them; `None` builds them in the lifespan and closes them on shutdown."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.secret_values())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        built = None if resources is not None else create_app_resources(settings)
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

    app.include_router(_health_router())
    # Imported here: `quack.session.router` and `artifacts/practice.py` still import this
    # module (D-C-014), so a module-level import would be a cycle.
    from quack.artifacts.router import router as artifacts_router
    from quack.domains.router import router as domains_router
    from quack.session.router import router as session_router

    # Domains and artifacts attach authentication and the `actions` limit in their routers
    # (public `register`/`login` carry the `auth` limit); the session router is wrapped here.
    app.include_router(domains_router)
    app.include_router(session_router, dependencies=[Depends(enforce_action_rate_limit)])
    app.include_router(artifacts_router)
    return app


def write_openapi(path: Path) -> None:
    """`ops openapi --out PATH`: write the schema of `create_app` (WP8.7). Lives in the
    assembly layer because it builds the app (§4.1, PHASE3_NOTES D-C-015)."""
    path.write_bytes(render_openapi(create_app()))
