from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest

from quack_runner.app import create_app
from quack_runner.contracts import PythonRunRequest, PythonTestResult, SqlRunRequest, SqlTestResult
from quack_runner.sandbox import SlotPool
from quack_runner.service import run_python, run_sql
from quack_runner.settings import RunnerSettings


@pytest.fixture(scope="session")
def settings() -> RunnerSettings:
    # Short walls keep the timeout tests quick; the rest are the defaults.
    return dataclasses.replace(
        RunnerSettings.from_env(), py_wall_s=2.0, py_cpu_s=1, py_total_wall_s=8.0
    )


@pytest.fixture(scope="session")
def pool(settings: RunnerSettings) -> Iterator[SlotPool]:
    pool = SlotPool(settings)
    pool.start()
    yield pool
    pool.stop()


@pytest.fixture
def py(pool: SlotPool, settings: RunnerSettings):
    async def run(
        code: str, *args_list: list[Any], function: str = "f", return_args: bool = False
    ) -> list[PythonTestResult]:
        request = PythonRunRequest(
            code=code,
            function=function,
            tests=[{"args": args} for args in args_list],  # type: ignore[list-item]
            return_args=return_args,
        )
        return (await run_python(request, pool, settings)).tests

    return run


@pytest.fixture
def sql(pool: SlotPool, settings: RunnerSettings):
    async def run(schema: str, query: str, *data: str) -> list[SqlTestResult]:
        request = SqlRunRequest(
            schema_sql=schema,
            query=query,
            tests=[{"data_sql": d} for d in data],  # type: ignore[list-item]
        )
        return (await run_sql(request, pool, settings)).tests

    return run


@pytest.fixture
async def client(settings: RunnerSettings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://runner") as client:
            yield client
