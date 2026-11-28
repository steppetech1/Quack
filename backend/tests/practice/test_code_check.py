"""Т-§6.10: judging runner results against a task's tests; hidden tests leave nothing but number
and status; the runner never gets the expected results. The runner is a fake HTTP transport."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from quack.config import Settings
from quack.practice.code_check import (
    all_numbers,
    learner_views,
    open_numbers,
    outcome_of,
    run_cases,
)
from quack.practice.code_tests import PythonCase, PythonTests, SqlCase, SqlTests
from quack.runner_client import contracts
from quack.runner_client.client import RunnerBusyError, RunnerClient, RunnerUnavailableError

SECRET = 987654321

PY_TESTS = PythonTests(
    function="square",
    reference="def square(x):\n    return x * x\n",
    cases=[
        PythonCase(args=[2], expected=4, hidden=False),
        PythonCase(args=[3], expected=9, hidden=False),
        PythonCase(args=[SECRET], expected=SECRET * SECRET, hidden=True),
        PythonCase(args=[[3, 1]], expected=[1, 3], mutates_arg=0, hidden=True),
        PythonCase(args=[-1], raises="ValueError", hidden=True),
    ],
)


def _runner(handler: Any) -> tuple[RunnerClient, list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return handler(request.url.path, body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return RunnerClient("http://runner", 5.0, client=client), seen


def _ok(value: Any, args_after: Any = None) -> dict[str, Any]:
    return {"status": "ok", "value": value, "args_after": args_after, "stdout": "", "stderr": ""}


async def test_python_judging_and_hidden_views() -> None:
    def handler(path: str, body: dict[str, Any]) -> httpx.Response:
        assert path == "/v1/run/python"
        assert body["return_args"] is True
        return httpx.Response(
            200,
            json={
                "tests": [
                    _ok(4),
                    _ok(
                        10,
                    ),  # wrong
                    {**_ok(SECRET * SECRET), "stdout": f"input was {SECRET}"},
                    _ok(None, [[1, 3]]),
                    {
                        "status": "error",
                        "error": {"type": "ValueError", "message": f"bad {SECRET}"},
                    },
                ]
            },
        )

    runner, seen = _runner(handler)
    views = await run_cases(runner, PY_TESTS, "code", all_numbers(PY_TESTS), Settings())
    assert [v.status for v in views] == ["passed", "wrong", "passed", "passed", "passed"]
    assert outcome_of(views) == "partial"
    # The runner got inputs only.
    assert "expected" not in json.dumps(seen)
    assert str(SECRET * SECRET) not in json.dumps(seen)
    # What the learner gets keeps no trace of hidden tests.
    shown = json.dumps([v.model_dump(mode="json") for v in learner_views(views)])
    assert str(SECRET) not in shown
    assert [v.hidden for v in learner_views(views)] == [False, False, True, True, True]


async def test_the_called_function_name_reaches_the_views() -> None:
    runner, _ = _runner(
        lambda p, b: httpx.Response(
            200, json={"tests": [{**_ok(4), "called": "sq"}, {**_ok(10), "called": "sq"}]}
        )
    )
    views = await run_cases(runner, PY_TESTS, "code", [1, 2], Settings())
    assert [v.called for v in views] == ["sq", "sq"]
    assert [v.status for v in views] == ["passed", "wrong"]  # the result decides, not the name
    plain_runner, _ = _runner(lambda p, b: httpx.Response(200, json={"tests": [_ok(4)]}))
    (plain,) = await run_cases(plain_runner, PY_TESTS, "code", [1], Settings())
    assert plain.called is None


async def test_open_numbers_and_outcomes() -> None:
    assert open_numbers(PY_TESTS) == [1, 2]
    runner, _ = _runner(lambda p, b: httpx.Response(200, json={"tests": [_ok(4), _ok(9)]}))
    views = await run_cases(runner, PY_TESTS, "code", [1, 2], Settings())
    assert outcome_of(views) == "correct"
    runner, _ = _runner(
        lambda p, b: httpx.Response(200, json={"tests": [{"status": "timeout"}, _ok(1)]})
    )
    views = await run_cases(runner, PY_TESTS, "code", [1, 2], Settings())
    assert [v.status for v in views] == ["timeout", "wrong"]
    assert outcome_of(views) == "incorrect"


async def test_sql_judging() -> None:
    tests = SqlTests(
        schema_sql="CREATE TABLE t (x INTEGER);",
        reference="SELECT x FROM t",
        ordered=False,
        cases=[
            SqlCase(
                data_sql="INSERT INTO t VALUES (1), (2);", expected_rows=[[1], [2]], hidden=False
            ),
            SqlCase(data_sql="INSERT INTO t VALUES (5);", expected_rows=[[5]], hidden=True),
        ],
    )

    def handler(path: str, body: dict[str, Any]) -> httpx.Response:
        assert path == "/v1/run/sql"
        assert "expected_rows" not in json.dumps(body)
        return httpx.Response(
            200,
            json={
                "tests": [
                    {"status": "ok", "columns": ["x"], "rows": [[2], [1]]},
                    {"status": "ok", "columns": ["x"], "rows": [[6]]},
                ]
            },
        )

    runner, _ = _runner(handler)
    views = await run_cases(runner, tests, "SELECT x FROM t", all_numbers(tests), Settings())
    assert [v.status for v in views] == ["passed", "wrong"]
    hidden = learner_views(views)[1]
    assert hidden.data_sql is None and hidden.actual_rows is None and hidden.expected_rows is None


@pytest.mark.parametrize(
    ("status", "error"), [(429, RunnerBusyError), (500, RunnerUnavailableError)]
)
async def test_runner_failures(status: int, error: type[Exception]) -> None:
    runner, _ = _runner(lambda p, b: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(error):
        await run_cases(runner, PY_TESTS, "code", [1], Settings())


def test_contracts_match_the_runner() -> None:
    """The backend copy of the runner contracts names the same fields and statuses."""
    runner_file = Path(__file__).resolve().parents[3] / "runner" / "quack_runner" / "contracts.py"
    if not runner_file.exists():
        pytest.skip("the runner sources are not in this checkout")
    source = runner_file.read_text(encoding="utf-8")
    for model in (
        contracts.PythonRunRequest,
        contracts.PythonTestResult,
        contracts.SqlRunRequest,
        contracts.SqlTestResult,
        contracts.RunError,
    ):
        assert f"class {model.__name__}(" in source
        for field in model.model_fields:
            assert f"    {field}:" in source, f"{model.__name__}.{field}"
    for status in contracts.TestStatus.__value__.__args__:
        assert f'"{status}"' in source
