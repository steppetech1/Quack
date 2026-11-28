"""The code sandbox (teacher@5, owner decision 2026-11-26): the element the teacher writes makes a
task with its sample as the first test; the background builder adds hidden cases whose expected
results come from a reference that first passed the sample; a hidden case never shows its
expected result to the learner, but the verdict keeps it for the teacher's review."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx
import pytest

from quack.config import Settings
from quack.practice import code_hidden
from quack.practice.code_check import CodeTestView, learner_views, verdict_tests
from quack.practice.code_hidden import (
    BuiltCase,
    CodeTestBuilderOutput,
    build_hidden_tests,
    validated_tests,
)
from quack.practice.code_tests import PythonCase, PythonTests, SqlTests
from quack.practice.sql_presets import SQL_PRESETS, sql_preset
from quack.runner_client.client import RunnerClient
from quack.session.code_sandbox import sandbox_problems, sandbox_tests
from quack.session.elements import CodeSandboxElement


def _element(**over: Any) -> CodeSandboxElement:
    data: dict[str, Any] = {
        "element_id": "e1",
        "kind": "code_sandbox",
        "mode": "full_problem",
        "language": "python",
        "statement": "Return the sum of the list.",
        "starter_code": "def total(xs):\n    pass\n",
        "entry_point": "total",
        "sql_preset": None,
        "sample_args_json": "[[1, 2, 3]]",
        "sample_expected_json": "6",
        "compare": "exact",
        "epsilon": None,
    }
    return CodeSandboxElement.model_validate({**data, **over})


def _runner(replies: list[dict[str, Any]]) -> tuple[RunnerClient, list[dict[str, Any]]]:
    queue = list(replies)
    seen: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=queue.pop(0))

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return RunnerClient("http://runner", 5.0, client=client), seen


def _ok(value: Any) -> dict[str, Any]:
    return {"status": "ok", "value": value}


# --- the element ---------------------------------------------------------------------------------


def test_a_good_python_element_has_no_problems() -> None:
    assert sandbox_problems(_element(), Settings()) == []


@pytest.mark.parametrize(
    ("over", "problem"),
    [
        ({"entry_point": "1bad"}, "entry_point"),
        ({"sample_args_json": "{}"}, "sample_args_json"),
        ({"sample_expected_json": "not json"}, "sample_expected_json"),
        ({"statement": " "}, "statement"),
        ({"epsilon": 0.0}, "epsilon"),
        ({"language": "sql", "entry_point": None, "sql_preset": None}, "sql_preset"),
        ({"language": "sql", "sql_preset": "shop", "sample_expected_json": '{"a": 1}'}, "rows"),
    ],
)
def test_a_bad_element_is_named(over: dict[str, Any], problem: str) -> None:
    assert any(problem in p for p in sandbox_problems(_element(**over), Settings()))


def test_full_problem_starts_with_the_sample_and_a_pending_build() -> None:
    tests = sandbox_tests(_element())
    assert isinstance(tests, PythonTests)
    assert tests.build == "pending" and tests.mode == "full_problem"
    assert [(c.args, c.expected, c.hidden) for c in tests.cases] == [([[1, 2, 3]], 6, False)]


def test_inline_patch_builds_nothing() -> None:
    tests = sandbox_tests(_element(mode="inline_patch"))
    assert tests.build == "none"


def test_sql_uses_the_preset_and_has_no_hidden_tests() -> None:
    tests = sandbox_tests(
        _element(
            language="sql",
            mode="full_problem",
            entry_point=None,
            sample_args_json=None,
            sql_preset="university",
            sample_expected_json='[["Alina"], ["Lev"]]',
            compare="unordered",
        )
    )
    assert isinstance(tests, SqlTests)
    preset = sql_preset("university")
    assert tests.mode == "inline_patch" and tests.build == "none"
    assert tests.schema_sql == preset.schema_sql
    assert tests.cases[0].data_sql == preset.data_sql
    assert tests.ordered is False and tests.preset == "university"


def test_every_preset_has_a_schema_and_data() -> None:
    for name in SQL_PRESETS:
        preset = sql_preset(name)
        assert "CREATE TABLE" in preset.schema_sql and "INSERT INTO" in preset.data_sql


# --- what the learner and the teacher see ---------------------------------------------------


def _hidden_view(name: str | None) -> CodeTestView:
    return CodeTestView(
        number=2,
        hidden=True,
        status="wrong",
        name=name,
        args=[[]],
        expected=0,
        actual=None,
        stdout="debug",
    )


def test_a_named_hidden_test_shows_its_input_and_output_but_not_the_expected() -> None:
    [view] = learner_views([_hidden_view("пустой список")])
    assert view.name == "пустой список" and view.args == [[]] and view.stdout == "debug"
    assert view.expected is None


def test_an_old_hidden_test_shows_its_status_only() -> None:
    [view] = learner_views([_hidden_view(None)])
    assert view.args is None and view.stdout is None


def test_the_verdict_keeps_the_expected_result_of_a_named_hidden_test_for_the_review() -> None:
    [named, old] = verdict_tests([_hidden_view("пустой список"), _hidden_view(None)], 160)
    assert named.detail is not None and "пустой список" in named.detail
    assert "expected 0" in named.detail
    assert old.detail is None


# --- the builder ---------------------------------------------------------------------------------


def _pending() -> PythonTests:
    tests = sandbox_tests(_element())
    assert isinstance(tests, PythonTests)
    return tests


def _output(cases: list[tuple[str, list[Any]]]) -> CodeTestBuilderOutput:
    return CodeTestBuilderOutput(
        reference_solution="def total(xs):\n    return sum(xs)\n",
        cases=[BuiltCase(name=n, args_json=json.dumps(a)) for n, a in cases],
    )


async def test_the_expected_results_come_from_the_reference_after_it_passes_the_sample() -> None:
    runner, seen = _runner([{"tests": [_ok(6), _ok(0), _ok(-2)]}])
    built, why = await validated_tests(
        runner,
        _pending(),
        _output([("пустой список", [[]]), ("отрицательные", [[-1, -1]])]),
        Settings(),
    )
    assert why is None and built is not None
    assert built.build == "ready" and built.reference.startswith("def total")
    assert [(c.name, c.expected, c.hidden) for c in built.cases[1:]] == [
        ("пустой список", 0, True),
        ("отрицательные", -2, True),
    ]
    assert seen[0]["tests"][0] == {"args": [[1, 2, 3]]}  # the sample runs first


async def test_a_reference_that_misses_the_sample_is_thrown_away() -> None:
    runner, _ = _runner([{"tests": [_ok(7), _ok(0)]}])
    built, why = await validated_tests(runner, _pending(), _output([("x", [[]])]), Settings())
    assert built is None and why is not None and "sample" in why


async def test_a_reference_that_fails_a_hidden_input_is_thrown_away() -> None:
    timeout = {"status": "timeout", "error": {"type": "Timeout", "message": ""}}
    runner, _ = _runner([{"tests": [_ok(6), timeout]}])
    built, why = await validated_tests(runner, _pending(), _output([("big", [[1]])]), Settings())
    assert built is None and why is not None and "timeout" in why


def test_float_approx_uses_the_task_epsilon() -> None:
    from quack.practice.code_check import _python_status
    from quack.runner_client.contracts import PythonTestResult

    case = PythonCase(args=[], expected=0.5, compare="float_approx", hidden=False)
    near = PythonTestResult(status="ok", value=0.5004)
    assert _python_status(case, near, Settings(), 1e-3) == "passed"
    assert _python_status(case, near, Settings(), 1e-6) == "wrong"
    exact = PythonCase(args=[], expected=1, compare="float_approx", hidden=False)
    one = PythonTestResult(status="ok", value=1.0)
    assert _python_status(exact, one, Settings(), 1e-6) == "passed"


@dataclass
class _Result:
    value: CodeTestBuilderOutput


class _Gateway:
    def __init__(self, outputs: list[CodeTestBuilderOutput]) -> None:
        self.outputs = outputs
        self.keys: list[str] = []
        self.failures: list[str] = []

    async def generate_structured(self, *args: Any, **kwargs: Any) -> _Result:
        self.keys.append(args[7])
        self.failures.append(args[3]["previous_failure"])
        return _Result(self.outputs.pop(0))


@dataclass
class _Task:
    condition: Any
    answer_spec: Any


async def _run_builder(
    monkeypatch: pytest.MonkeyPatch, gateway: _Gateway, runner: RunnerClient
) -> tuple[str | None, list[PythonTests]]:
    stored: list[PythonTests] = []

    async def get_tests(*_a: Any, **_k: Any) -> PythonTests:
        return _pending()

    async def get_task(*_a: Any, **_k: Any) -> _Task:
        cond = type("C", (), {"text": "Sum"})()
        spec = type("S", (), {"starter_code": "def total(xs): pass"})()
        return _Task(cond, spec)

    async def store(*_a: Any, tests: PythonTests, **_k: Any) -> bool:
        stored.append(tests)
        return True

    monkeypatch.setattr(code_hidden, "get_code_tests", get_tests)
    monkeypatch.setattr(code_hidden, "get_task", get_task)
    monkeypatch.setattr(code_hidden, "store_built_tests", store)
    build = await build_hidden_tests(
        None,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        runner,
        Settings(),
        learner_id=uuid4(),
        task_id=uuid4(),
    )
    return build, stored


async def test_one_retry_after_a_broken_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _Gateway([_output([("a", [[]])]), _output([("a", [[]])])])
    runner, _ = _runner([{"tests": [_ok(5), _ok(0)]}, {"tests": [_ok(6), _ok(0)]}])
    build, stored = await _run_builder(monkeypatch, gateway, runner)
    assert build == "ready" and stored[0].build == "ready"
    assert len(gateway.keys) == 2 and gateway.keys[0] != gateway.keys[1]
    assert gateway.failures[0] == "(none)" and "sample" in gateway.failures[1]


async def test_twice_broken_the_task_stays_on_the_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _Gateway([_output([("a", [[]])]), _output([("a", [[]])])])
    runner, _ = _runner([{"tests": [_ok(5), _ok(0)]}, {"tests": [_ok(5), _ok(0)]}])
    build, stored = await _run_builder(monkeypatch, gateway, runner)
    assert build == "failed"
    assert stored[-1].build == "failed" and len(stored[-1].cases) == 1


def test_a_flat_sql_sample_is_one_column() -> None:
    element = _element(
        language="sql",
        mode="inline_patch",
        entry_point=None,
        sample_args_json=None,
        sql_preset="shop",
        sample_expected_json='["Boris", "Dina"]',
        compare="unordered",
    )
    assert sandbox_problems(element, Settings()) == []
    tests = sandbox_tests(element)
    assert isinstance(tests, SqlTests)
    assert tests.cases[0].expected_rows == [["Boris"], ["Dina"]]
