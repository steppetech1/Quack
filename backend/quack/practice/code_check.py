"""Running learner code on the task's tests and judging it (Т-§6.10).

The runner gets the code and the inputs only; this module compares with the expected results.
What the learner sees: an open test in full (input, expected, actual, output, error); a hidden
test — its name, input and what the code gave or the error, never the expected result
(`CodeTestView.hidden_view`). Output is cut to `Settings.code_output_chars`.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from quack.config import Settings
from quack.events.values import CodeTestOutcome, CodeTestStatus, Outcome
from quack.practice.code_tests import PythonCase, PythonTests, SqlTests
from quack.practice.code_values import rows_equal, unordered_equal, values_equal
from quack.runner_client.client import RunnerClient
from quack.runner_client.contracts import (
    PythonRunRequest,
    PythonTest,
    PythonTestResult,
    RunError,
    SqlRunRequest,
    SqlTest,
    SqlTestResult,
)

_PASSTHROUGH: set[str] = {
    "timeout",
    "memory",
    "output_limit",
    "no_function",
    "unsupported_value",
    "row_limit",
    "not_run",
}


class CodeRunErrorView(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    message: str
    line: int | None = None


class CodeTestView(BaseModel):
    """One test as the learner sees it. Every field after `status` is `None` for a hidden test."""

    model_config = ConfigDict(frozen=True)

    number: int
    hidden: bool
    status: CodeTestStatus
    # The case name of a hidden test of the sandbox («дубликаты»); `None` for the others.
    name: str | None = None
    args: list[Any] | None = None
    expected: Any = None
    actual: Any = None
    data_sql: str | None = None
    columns: list[str] | None = None
    expected_rows: list[list[Any]] | None = None
    actual_rows: list[list[Any]] | None = None
    error: CodeRunErrorView | None = None
    stdout: str | None = None
    stderr: str | None = None
    time_ms: int | None = None

    def hidden_view(self) -> CodeTestView:
        """No expected result. A named case (the sandbox) also shows its input and what the code
        gave, so the learner can trace it; an unnamed one (teacher@4) its status only."""
        if self.name is None:
            return CodeTestView(number=self.number, hidden=True, status=self.status)
        return self.model_copy(update={"expected": None, "expected_rows": None})


class CustomRunView(BaseModel):
    """«Запустить свой ввод»: the function on the learner's own arguments, nothing compared."""

    model_config = ConfigDict(frozen=True)

    status: CodeTestStatus
    value: Any = None
    error: CodeRunErrorView | None = None
    stdout: str = ""
    stderr: str = ""
    time_ms: int = 0


def _error(error: RunError | None) -> CodeRunErrorView | None:
    if error is None:
        return None
    return CodeRunErrorView(type=error.type, message=error.message, line=error.line)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n… (cut)"


def _python_status(
    case: PythonCase, result: PythonTestResult, settings: Settings, epsilon: float | None = None
) -> CodeTestStatus:
    tolerance = settings.code_float_tolerance
    if case.compare == "float_approx" and epsilon is not None:
        tolerance = epsilon
    if case.raises is not None:
        if result.status == "error" and result.error is not None:
            return "passed" if result.error.type == case.raises else "wrong"
        return "wrong" if result.status == "ok" else _failure(result.status)
    if result.status != "ok":
        return _failure(result.status)
    if case.mutates_arg is not None:
        after = result.args_after or []
        if case.mutates_arg >= len(after):
            return "wrong"
        actual = after[case.mutates_arg]
    else:
        actual = result.value
    if case.compare == "unordered":
        same = unordered_equal(case.expected, actual, tolerance=tolerance)
    else:
        same = values_equal(
            case.expected,
            actual,
            tolerance=tolerance,
            mixed_numbers=case.compare in ("float_tol", "float_approx"),
        )
    return "passed" if same else "wrong"


def _failure(status: str) -> CodeTestStatus:
    return status if status in _PASSTHROUGH else "error"  # type: ignore[return-value]


def _python_actual(case: PythonCase, result: PythonTestResult) -> Any:
    if case.mutates_arg is not None and result.args_after is not None:
        if case.mutates_arg < len(result.args_after):
            return result.args_after[case.mutates_arg]
        return None
    return result.value


async def run_python_cases(
    runner: RunnerClient,
    tests: PythonTests,
    code: str,
    numbers: list[int],
    settings: Settings,
) -> list[CodeTestView]:
    """Run the cases `numbers` (1-based) of a Python task; open ones in full, hidden ones too —
    the caller hides them with `hidden_view`."""
    cases = [tests.cases[n - 1] for n in numbers]
    response = await runner.run_python(
        PythonRunRequest(
            code=code,
            function=tests.function,
            tests=[PythonTest(args=case.args) for case in cases],
            return_args=any(case.mutates_arg is not None for case in cases),
        )
    )
    views = []
    for number, case, result in zip(numbers, cases, response.tests, strict=True):
        views.append(
            CodeTestView(
                number=number,
                hidden=case.hidden,
                status=_python_status(case, result, settings, tests.epsilon),
                name=case.name,
                args=case.args,
                expected=case.expected if case.raises is None else {"raises": case.raises},
                actual=_python_actual(case, result),
                error=_error(result.error),
                stdout=_cut(result.stdout, settings.code_output_chars),
                stderr=_cut(result.stderr, settings.code_output_chars),
                time_ms=result.time_ms,
            )
        )
    return views


def _sql_status(
    rows: list[list[Any]], result: SqlTestResult, tests: SqlTests, settings: Settings
) -> CodeTestStatus:
    if result.status != "ok":
        return _failure(result.status)
    same = rows_equal(
        rows,
        result.rows or [],
        ordered=tests.ordered,
        tolerance=settings.code_float_tolerance,
        sort_digits=settings.code_sql_sort_digits,
    )
    return "passed" if same else "wrong"


async def run_sql_cases(
    runner: RunnerClient,
    tests: SqlTests,
    query: str,
    numbers: list[int],
    settings: Settings,
) -> list[CodeTestView]:
    cases = [tests.cases[n - 1] for n in numbers]
    response = await runner.run_sql(
        SqlRunRequest(
            schema_sql=tests.schema_sql,
            query=query,
            tests=[SqlTest(data_sql=case.data_sql) for case in cases],
        )
    )
    return [
        CodeTestView(
            number=number,
            hidden=case.hidden,
            status=_sql_status(case.expected_rows, result, tests, settings),
            data_sql=case.data_sql,
            columns=result.columns,
            expected_rows=case.expected_rows,
            actual_rows=result.rows,
            error=_error(result.error),
            time_ms=result.time_ms,
        )
        for number, case, result in zip(numbers, cases, response.tests, strict=True)
    ]


async def run_cases(
    runner: RunnerClient,
    tests: PythonTests | SqlTests,
    code: str,
    numbers: list[int],
    settings: Settings,
) -> list[CodeTestView]:
    if isinstance(tests, PythonTests):
        return await run_python_cases(runner, tests, code, numbers, settings)
    return await run_sql_cases(runner, tests, code, numbers, settings)


async def run_custom(
    runner: RunnerClient, tests: PythonTests, code: str, args: list[Any], output_chars: int
) -> CustomRunView:
    response = await runner.run_python(
        PythonRunRequest(code=code, function=tests.function, tests=[PythonTest(args=args)])
    )
    (result,) = response.tests
    return CustomRunView(
        status="passed" if result.status == "ok" else _failure(result.status),
        value=result.value,
        error=_error(result.error),
        stdout=_cut(result.stdout, output_chars),
        stderr=_cut(result.stderr, output_chars),
        time_ms=result.time_ms,
    )


def open_numbers(tests: PythonTests | SqlTests) -> list[int]:
    return [n for n, case in enumerate(tests.cases, start=1) if not case.hidden]


def all_numbers(tests: PythonTests | SqlTests) -> list[int]:
    return list(range(1, len(tests.cases) + 1))


def outcome_of(views: list[CodeTestView]) -> Outcome:
    """Т-§6.10: correct — every test passed; partial — some; incorrect — none."""
    passed = sum(view.status == "passed" for view in views)
    if views and passed == len(views):
        return "correct"
    return "partial" if passed else "incorrect"


def _short(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def verdict_tests(views: list[CodeTestView], detail_chars: int) -> list[CodeTestOutcome]:
    """The tests of a `code` verdict for the event: an open test with a short `detail` of what
    the learner saw; a named hidden test of the sandbox (teacher@5) too, with its expected result,
    so the teacher's review sees it — the event never reaches the client; an unnamed hidden test
    (teacher@4) with its status only (Т-§6.10)."""
    outcomes = []
    for view in views:
        detail = None
        if (not view.hidden or view.name is not None) and view.status != "passed":
            if view.error is not None:
                detail = f"{view.error.type}: {view.error.message}"
                if view.error.line is not None:
                    detail += f" (line {view.error.line})"
            elif view.args is not None:
                args = _short(view.args, detail_chars)
                expected = _short(view.expected, detail_chars)
                actual = _short(view.actual, detail_chars)
                detail = f"args {args}, expected {expected}, got {actual}"
            elif view.expected_rows is not None:
                detail = (
                    f"expected rows {_short(view.expected_rows, detail_chars)}, got "
                    f"{_short(view.actual_rows, detail_chars)}"
                )
            if detail is not None and view.name is not None:
                detail = f"«{view.name}»: {detail}"
            if detail is not None:
                detail = detail[: detail_chars * 3]
        outcomes.append(
            CodeTestOutcome(
                number=view.number, hidden=view.hidden, status=view.status, detail=detail
            )
        )
    return outcomes


def learner_views(views: list[CodeTestView]) -> list[CodeTestView]:
    """What the client may get: hidden tests reduced to number and status."""
    return [view.hidden_view() if view.hidden else view for view in views]
