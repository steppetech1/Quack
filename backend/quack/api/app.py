"""FastAPI application factory: pooled resources in the lifespan, request/trace ids,
unified errors and health endpoints. Phase 2 streams add their routers here."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.errors import install_error_handlers
from quack.config import Settings, get_settings
from quack.db.neo4j import create_driver
from quack.db.postgres import SessionFactory, create_engine, create_session_factory
from quack.db.redis import create_redis
from quack.logging import configure_logging, get_logger, log_context

log = get_logger(__name__)

HEALTH_CHECK_TIMEOUT_SEC = 2.0


@dataclass
class AppResources:
    """Pools shared by request handlers. API code opens Neo4j sessions read-only (law 3)."""

    engine: AsyncEngine
    session_factory: SessionFactory
    neo4j: AsyncDriver
    redis: Redis

    async def close(self) -> None:
        await self.redis.aclose()
        await self.neo4j.close()
        await self.engine.dispose()


def get_resources(request: Request) -> AppResources:
    resources = getattr(request.app.state, "resources", None)
    if resources is None:
        raise RuntimeError("application resources are not initialized")
    return resources


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
            engine = create_engine(settings)
            app.state.resources = AppResources(
                engine=engine,
                session_factory=create_session_factory(engine),
                neo4j=create_driver(settings),
                redis=create_redis(settings),
            )
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
    return app
