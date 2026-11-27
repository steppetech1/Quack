"""runner service: `GET /health/live`, `POST /v1/run/python`, `POST /v1/run/sql`.

No database, no network out, no secrets (documentation/идея-редактор-кода-и-запуск.md). All
slots busy → 429; the backend shows «сервер занят» and does not count the attempt.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from quack_runner.contracts import (
    PythonRunRequest,
    PythonRunResponse,
    RunnerError,
    SqlRunRequest,
    SqlRunResponse,
)
from quack_runner.sandbox import SlotPool, SlotsBusy
from quack_runner.service import run_python, run_sql
from quack_runner.settings import RunnerSettings


def create_app(settings: RunnerSettings | None = None) -> FastAPI:
    settings = settings or RunnerSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = SlotPool(settings)
        pool.start()
        app.state.settings = settings
        app.state.pool = pool
        try:
            yield
        finally:
            pool.stop()

    app = FastAPI(title="quack-runner", lifespan=lifespan)

    @app.exception_handler(SlotsBusy)
    async def busy(request: Request, exc: SlotsBusy) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content=RunnerError(error="all slots are busy").model_dump(),
            headers={"Retry-After": "2"},
        )

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/run/python")
    async def python(body: PythonRunRequest, request: Request) -> PythonRunResponse:
        return await run_python(body, request.app.state.pool, request.app.state.settings)

    @app.post("/v1/run/sql")
    async def sql(body: SqlRunRequest, request: Request) -> SqlRunResponse:
        return await run_sql(body, request.app.state.pool, request.app.state.settings)

    return app


app = create_app()
