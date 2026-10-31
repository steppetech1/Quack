"""FastAPI application factory: pooled resources in the lifespan, request/trace ids,
unified errors, health endpoints and the product routers (ТЗ Фазы 3 WP1)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from quack.api.auth import get_current_learner_id
from quack.api.errors import install_error_handlers
from quack.api.resources import AppResources, create_app_resources, get_resources
from quack.config import Settings, get_settings
from quack.logging import configure_logging, get_logger, log_context

# Re-exported until WP1.4 moves product imports to `quack.api.resources` (D-C-003).
__all__ = ["AppResources", "create_app", "get_resources"]

log = get_logger(__name__)

HEALTH_CHECK_TIMEOUT_SEC = 2.0


def _trace_id(request: Request) -> str:
    traceparent = request.headers.get("traceparent", "")
    parts = traceparent.split("-")
    if len(parts) == 4 and len(parts[1]) == 32:
        return parts[1]
    return request.headers.get("X-Trace-ID") or uuid.uuid4().hex


async def _check(probe: Callable[[], Awaitable[object]]) -> str:
    try:
        async with asyncio.timeout(HEALTH_CHECK_TIMEOUT_SEC):
            await probe()
        return "ok"
    except Exception:
        return "error"


def _health_router() -> APIRouter:
    router = APIRouter(prefix="/health", tags=["health"])

    @router.get("/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/ready")
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

            checks = {
                "postgres": await _check(postgres),
                "neo4j": await _check(resources.neo4j.verify_connectivity),
                "redis": await _check(redis),
            }
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            {"status": "ok" if ok else "error", "checks": checks}, status_code=200 if ok else 503
        )

    return router


def create_app(settings: Settings | None = None, *, connect: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.secret_values())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.resources = None
        if connect:
            app.state.resources = create_app_resources(settings)
        try:
            yield
        finally:
            if app.state.resources is not None:
                await app.state.resources.close()

    app = FastAPI(title="Quack API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)

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
    # Imported here: product modules still import `quack.api.app` (cycle until WP1.4).
    from quack.artifacts.router import router as artifacts_router
    from quack.domains.router import router as domains_router
    from quack.session.router import router as session_router

    authenticated = [Depends(get_current_learner_id)]
    app.include_router(domains_router)  # router-level auth inside, except register/login
    app.include_router(session_router, dependencies=authenticated)
    app.include_router(artifacts_router)  # router-level auth inside
    return app
