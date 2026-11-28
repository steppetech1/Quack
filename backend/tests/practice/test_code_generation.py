"""Т-§6.10: the reference of a generated code task gives the expected results; a draft whose
reference fails, differs between runs or whose hidden data repeat the open ones is dropped."""

from __future__ import annotations

import json
from typing import Any

import httpx

from quack.config import Settings
from quack.practice.code_generation import (
    python_reference_tests,
    reversed_statements,
    sql_reference_tests,
)
from quack.practice.code_role_outputs import (
    GeneratedCodeTask,
    GeneratedPythonCase,
    GeneratedSqlCase,
)
from quack.practice.markup import TaskMarkup
from quack.practice.role_outputs import TaskCondition
from quack.runner_client.client import RunnerClient


def _markup() -> TaskMarkup:
    return TaskMarkup.model_validate(
        {
            "tests": [{"skill": "S1", "level": 3}],
            "methodNamed": True,
            "answer": {"type": "code", "language": "python", "function": "f"},
        }
    )


def _python_draft(cases: list[GeneratedPythonCase]) -> GeneratedCodeTask:
    return GeneratedCodeTask(
        condition=TaskCondition(text="Square", latex=None),
        markup=_markup(),
        language="python",
        function="f",
        starter_code="def f(x):\n    pass\n",
        reference="def f(x):\n    return x * x\n",
        schema_sql=None,
        python_cases=cases,
        sql_cases=None,
        explanation="",
    )


def _case(args: list[Any], hidden: bool, raises: str | None = None) -> GeneratedPythonCase:
    return GeneratedPythonCase(
        args_json=json.dumps(args), compare="exact", mutates_arg=None, raises=raises, hidden=hidden
    )


def _runner(replies: list[dict[str, Any]]) -> RunnerClient:
    queue = list(replies)

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=queue.pop(0))

    return RunnerClient(
        "http://runner", 5.0, client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )


def _ok(value: Any) -> dict[str, Any]:
    return {"status": "ok", "value": value}


async def test_python_expected_comes_from_the_reference() -> None:
    draft = _python_draft([_case([2], False), _case([-1], True, raises="ValueError")])
    reply = {"tests": [_ok(4), {"status": "error", "error": {"type": "ValueError", "message": ""}}]}
    tests = await python_reference_tests(_runner([reply, reply]), draft, Settings())
    assert tests is not None
    assert tests.cases[0].expected == 4
    assert tests.cases[1].raises == "ValueError" and tests.cases[1].expected is None


async def test_python_reference_that_differs_between_runs_is_dropped() -> None:
    draft = _python_draft([_case([2], False)])
    tests = await python_reference_tests(
        _runner([{"tests": [_ok([1, 2])]}, {"tests": [_ok([2, 1])]}]), draft, Settings()
    )
    assert tests is None


async def test_python_reference_that_fails_is_dropped() -> None:
    draft = _python_draft([_case([2], False)])
    failed = {"tests": [{"status": "timeout"}]}
    assert await python_reference_tests(_runner([failed, failed]), draft, Settings()) is None


def _sql_draft(datasets: list[tuple[str, bool]]) -> GeneratedCodeTask:
    return GeneratedCodeTask(
        condition=TaskCondition(text="Names", latex=None),
        markup=_markup(),
        language="sql",
        function=None,
        starter_code="",
        reference="SELECT name FROM people",
        schema_sql="CREATE TABLE people (name TEXT);",
        python_cases=None,
        sql_cases=[GeneratedSqlCase(data_sql=d, hidden=h) for d, h in datasets],
        explanation="",
    )


def _rows(*rows: list[Any]) -> dict[str, Any]:
    return {"status": "ok", "columns": ["name"], "rows": list(rows)}


async def test_sql_hidden_result_equal_to_open_is_dropped() -> None:
    draft = _sql_draft([("INSERT INTO people VALUES ('a');", False), ("x", True)])
    reply = {"tests": [_rows(["a"]), _rows(["a"])]}
    assert await sql_reference_tests(_runner([reply, reply]), draft, Settings()) is None


async def test_sql_tests_built() -> None:
    draft = _sql_draft([("open", False), ("hidden", True)])
    reply = {"tests": [_rows(["a"], ["b"]), _rows(["c"])]}
    second = {"tests": [_rows(["b"], ["a"]), _rows(["c"])]}
    tests = await sql_reference_tests(_runner([reply, second]), draft, Settings())
    assert tests is not None
    assert tests.ordered is False
    assert tests.cases[1].expected_rows == [["c"]]


def test_reversed_statements() -> None:
    assert reversed_statements("INSERT INTO t VALUES (1);\nINSERT INTO t VALUES (2);") == (
        "INSERT INTO t VALUES (2);\nINSERT INTO t VALUES (1);"
    )
