"""FastAPI application factory and infrastructure lifecycle."""

import inspect
from contextlib import asynccontextmanager
from importlib import import_module
from time import perf_counter
from typing import Any
from uuid import uuid4

import redis.asyncio as redis_async
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.deps import get_current_student
from app.api.health import router as health_router
from app.api.knowledge import router as knowledge_router
from app.api.matching import router as matching_router
from app.api.overview import router as overview_router
from app.api.prep import router as prep_router
from app.api.profile import router as profile_router
from app.api.programs import router as programs_router
from app.api.saved import router as saved_router
from app.api.sets import router as sets_router
from app.api.tasks import router as tasks_router
from app.config import settings
from app.db.engine import close_engine, create_engine, create_sessionmaker
from app.errors import AppError, UnsupportedMediaType
from app.logging import configure_logging


def _optional_layer(name: str) -> Any | None:
    """Load B1/B2 only when that layer is present in the checkout."""
    try:
        return import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name and (name == exc.name or name.startswith(f"{exc.name}.")):
            return None
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine(settings)
    redis = None
    driver = None
    graph_client = None
    llm = None
    try:
        app.state.sessionmaker = create_sessionmaker(engine)
        graph_client = _optional_layer("app.graph.client")  # B1 integration point.
        if graph_client is not None:
            driver = graph_client.create_driver(settings)
            if inspect.isawaitable(driver):
                driver = await driver
        app.state.neo4j = driver
        redis = redis_async.from_url(settings.REDIS_URL)
        app.state.redis = redis
        llm_module = _optional_layer("app.llm.client")  # B2 integration point.
        if llm_module is not None:
            llm = llm_module.LLMClient(settings, redis)
        app.state.llm = llm
        yield
    finally:
        if llm is not None:
            close = getattr(llm, "aclose", None) or getattr(llm, "close", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
        if redis is not None:
            await redis.aclose()
        if driver is not None and graph_client is not None:
            result = graph_client.close_driver(driver)
            if inspect.isawaitable(result):
                await result
        await close_engine(engine)


def create_app() -> FastAPI:
    configure_logging(settings.LOG_LEVEL)
    logger = structlog.get_logger(__name__)
    app = FastAPI(title="Quack API", lifespan=lifespan)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        started = perf_counter()
        request_id = request.headers.get("X-Request-Id") or uuid4().hex[:16]
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        status = 500
        try:
            body = (
                await request.body()
                if request.method in {"POST", "PATCH", "PUT", "DELETE"}
                else b""
            )
            content_type = (
                request.headers.get("content-type", "").split(";", 1)[0].strip()
            )
            if body and content_type.lower() != "application/json":
                error = UnsupportedMediaType("application/json required")
                response = JSONResponse(
                    status_code=error.status,
                    content={"error": {"code": error.code, "message": error.message}},
                )
            else:
                response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-Id"] = request_id
            return response
        finally:
            fields: dict[str, Any] = {
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "request_id": request_id,
            }
            if request.cookies.get("quack_token"):
                try:
                    fields["student_id"] = str(get_current_student(request).student_id)
                except AppError:
                    pass
            logger.info("http_request", **fields)
            structlog.contextvars.clear_contextvars()

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        message = (
            exc.errors()[0]["msg"] if exc.errors() else "Request validation failed"
        )
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "validation_failed",
                    "message": message,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_request_error", request_id=request.state.request_id)
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "internal", "message": "Internal server error"},
                "request_id": request.state.request_id,
            },
            headers={"X-Request-Id": request.state.request_id},
        )

    app.include_router(auth_router)
    app.include_router(health_router)
    app.include_router(profile_router)
    app.include_router(saved_router)
    app.include_router(programs_router)
    app.include_router(chat_router)
    app.include_router(prep_router)
    app.include_router(tasks_router)
    app.include_router(sets_router)
    app.include_router(knowledge_router)
    app.include_router(matching_router)
    app.include_router(overview_router)
    return app
