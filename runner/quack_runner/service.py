"""A run: every test of a request in one slot, one process per test, under a total budget."""

from __future__ import annotations

import time
from typing import Any

from quack_runner.contracts import (
    PythonRunRequest,
    PythonRunResponse,
    PythonTestResult,
    RunError,
    SqlRunRequest,
    SqlRunResponse,
    SqlTestResult,
    TestStatus,
)
from quack_runner.sandbox import (
    PYTHON_HARNESS,
    SQL_HARNESS,
    Limits,
    ProcessOutcome,
    SlotPool,
    run_process,
)
from quack_runner.settings import RunnerSettings

_HARNESS_STATUSES: set[str] = {
    "ok",
    "error",
    "timeout",
    "memory",
    "unsupported_value",
    "no_function",
    "bad_input",
    "row_limit",
}


def _python_limits(settings: RunnerSettings, wall_s: float) -> Limits:
    return Limits(
        wall_s=wall_s,
        cpu_s=settings.py_cpu_s,
        memory_mb=settings.py_memory_mb,
        nproc=settings.py_nproc,
        output_bytes=settings.output_bytes,
        result_bytes=settings.result_bytes,
        file_bytes=settings.file_bytes,
        open_files=settings.open_files,
    )


def _sql_limits(settings: RunnerSettings, wall_s: float) -> Limits:
    return Limits(
        wall_s=wall_s,
        cpu_s=int(settings.sql_wall_s) + 1,
        memory_mb=settings.sql_memory_mb,
        nproc=settings.sql_nproc,
        output_bytes=settings.output_bytes,
        result_bytes=settings.result_bytes,
        file_bytes=settings.file_bytes,
        open_files=settings.open_files,
    )


def _status(outcome: ProcessOutcome) -> tuple[TestStatus, dict[str, Any], RunError | None]:
    """The status of one test from its process; the process facts win over its own report."""
    result = outcome.result or {}
    if outcome.timed_out:
        return "timeout", {}, RunError(type="Timeout", message="превышено время")
    if outcome.output_exceeded:
        return "output_limit", {}, RunError(type="OutputLimit", message="слишком большой вывод")
    status = result.get("status")
    if status not in _HARNESS_STATUSES:
        code = outcome.returncode
        message = f"процесс завершился без результата (код {code})"
        return "error", {}, RunError(type="ProcessExit", message=message)
    error = result.get("error")
    run_error = None
    if isinstance(error, dict):
        try:
            run_error = RunError.model_validate(error)
        except ValueError:
            run_error = RunError(type="Error", message="ошибка")
    return status, result, run_error  # type: ignore[return-value]


async def run_python(
    request: PythonRunRequest, pool: SlotPool, settings: RunnerSettings
) -> PythonRunResponse:
    results: list[PythonTestResult] = []
    async with pool.slot() as slot:
        deadline = time.monotonic() + settings.py_total_wall_s
        for test in request.tests:
            remaining = deadline - time.monotonic()
            if remaining <= 0.05:
                results.append(PythonTestResult(status="not_run"))
                continue
            payload = {
                "code": request.code,
                "function": request.function,
                "args": test.args,
                "return_args": request.return_args,
                "seed": 0,
            }
            limits = _python_limits(settings, min(settings.py_wall_s, remaining))
            outcome = await run_process(settings, slot, PYTHON_HARNESS, payload, limits)
            status, result, error = _status(outcome)
            ok = status == "ok"
            results.append(
                PythonTestResult(
                    status=status,
                    value=result.get("value") if ok else None,
                    args_after=result.get("args_after") if ok else None,
                    error=error,
                    stdout=outcome.stdout,
                    stderr=outcome.stderr,
                    time_ms=int(result.get("time_ms", outcome.wall_ms)),
                )
            )
    return PythonRunResponse(tests=results)


async def run_sql(
    request: SqlRunRequest, pool: SlotPool, settings: RunnerSettings
) -> SqlRunResponse:
    results: list[SqlTestResult] = []
    async with pool.slot() as slot:
        deadline = time.monotonic() + settings.sql_total_wall_s
        for test in request.tests:
            remaining = deadline - time.monotonic()
            if remaining <= 0.05:
                results.append(SqlTestResult(status="not_run"))
                continue
            payload = {
                "schema_sql": request.schema_sql,
                "data_sql": test.data_sql,
                "query": request.query,
                "query_s": min(settings.sql_query_s, remaining),
                "max_rows": settings.sql_max_rows,
            }
            limits = _sql_limits(settings, min(settings.sql_wall_s, remaining))
            outcome = await run_process(settings, slot, SQL_HARNESS, payload, limits)
            status, result, error = _status(outcome)
            ok = status == "ok"
            results.append(
                SqlTestResult(
                    status=status,
                    columns=result.get("columns") if ok else None,
                    rows=result.get("rows") if ok else None,
                    error=error,
                    time_ms=int(result.get("time_ms", outcome.wall_ms)),
                )
            )
    return SqlRunResponse(tests=results)
