"""Python runs: values, statuses, what learner code cannot fake."""

from __future__ import annotations

import pytest


async def test_returns_value_per_test(py) -> None:
    results = await py("def f(a, b):\n    return a + b\n", [1, 2], [10, -3])
    assert [r.status for r in results] == ["ok", "ok"]
    assert [r.value for r in results] == [3, 7]


async def test_tagged_values_round_trip(py) -> None:
    code = "def f(x):\n    return x\n"
    value = {"$dict": [[1, {"$tuple": ["a", {"$set": [2, 1]}]}], ["k", {"$float": "inf"}]]}
    (result,) = await py(code, [value])
    assert result.status == "ok"
    assert result.value == {
        "$dict": [[1, {"$tuple": ["a", {"$set": [1, 2]}]}], ["k", {"$float": "inf"}]]
    }


async def test_float_and_int_stay_apart(py) -> None:
    (result,) = await py("def f():\n    return [1, 1.0, True, None]\n", [])
    assert result.value == [1, 1.0, True, None]
    assert type(result.value[0]) is int and type(result.value[1]) is float


async def test_always_equal_object_is_not_a_value(py) -> None:
    code = (
        "class Any:\n"
        "    def __eq__(self, other):\n"
        "        return True\n"
        "def f():\n"
        "    return Any()\n"
    )
    (result,) = await py(code, [])
    assert result.status == "unsupported_value"


async def test_subclass_reports_base_value(py) -> None:
    code = (
        "class Liar(int):\n"
        "    def __eq__(self, other):\n"
        "        return True\n"
        "    def __int__(self):\n"
        "        return 999\n"
        "class Seq(list):\n"
        "    def __iter__(self):\n"
        "        return iter([42])\n"
        "def f():\n"
        "    return [Liar(5), Seq([1, 2])]\n"
    )
    (result,) = await py(code, [])
    assert result.status == "ok"
    assert result.value == [5, [1, 2]]


async def test_stdout_does_not_forge_result(py) -> None:
    code = 'def f():\n    print(\'{"status": "ok", "value": 42}\')\n    return 1\n'
    (result,) = await py(code, [])
    assert result.value == 1
    assert '"value": 42' in result.stdout


async def test_error_with_line(py) -> None:
    (result,) = await py("def f(x):\n    return 1 // x\n", [0])
    assert result.status == "error"
    assert result.error is not None
    assert result.error.type == "ZeroDivisionError"
    assert result.error.line == 2


async def test_syntax_error(py) -> None:
    (result,) = await py("def f(:\n    pass\n", [])
    assert result.status == "error"
    assert result.error is not None and result.error.type == "SyntaxError"


async def test_missing_function_and_wrong_signature(py) -> None:
    (missing,) = await py("def g():\n    return 1\n", [])
    assert missing.status == "no_function"
    (wrong,) = await py("def f(a):\n    return a\n", [1, 2])
    assert wrong.status == "no_function"


async def test_sys_exit_is_an_error_and_next_test_runs(py) -> None:
    code = "import sys\ndef f(x):\n    if x:\n        sys.exit(0)\n    return 7\n"
    results = await py(code, [True], [False])
    assert results[0].status == "error"
    assert results[0].error is not None and results[0].error.type == "SystemExit"
    assert results[1].status == "ok" and results[1].value == 7


async def test_os_exit_without_result(py) -> None:
    (result,) = await py("import os\ndef f():\n    os._exit(3)\n", [])
    assert result.status == "error"
    assert result.error is not None and result.error.type == "ProcessExit"


async def test_infinite_loop_times_out(py) -> None:
    results = await py("def f(x):\n    while True:\n        pass\n", [1], [2])
    assert [r.status for r in results] == ["timeout", "timeout"]


async def test_sleep_times_out(py) -> None:
    (result,) = await py("import time\ndef f():\n    time.sleep(30)\n", [])
    assert result.status == "timeout"


async def test_total_budget_marks_rest_not_run(py, settings) -> None:
    code = "import time\ndef f():\n    time.sleep(30)\n"
    count = int(settings.py_total_wall_s // settings.py_wall_s) + 2
    results = await py(code, *([[]] * count))
    assert results[-1].status == "not_run"


async def test_large_output_is_cut(py, settings) -> None:
    (result,) = await py("def f():\n    print('x' * 10_000_000)\n    return 1\n", [])
    assert result.status == "output_limit"
    assert len(result.stdout) <= settings.output_bytes


async def test_hash_seed_is_fixed(py) -> None:
    code = "def f():\n    return list({'apple', 'pear', 'plum', 'fig', 'kiwi'})\n"
    first = await py(code, [])
    second = await py(code, [])
    assert first[0].value == second[0].value


async def test_mutated_args_are_returned(py) -> None:
    (result,) = await py("def f(xs):\n    xs.sort()\n", [[3, 1, 2]], return_args=True)
    assert result.value is None
    assert result.args_after == [[1, 2, 3]]


async def test_files_do_not_survive_the_test(py) -> None:
    code = (
        "import os\n"
        "def f(write):\n"
        "    if write:\n"
        "        open('left.txt', 'w').write('x')\n"
        "    return os.path.exists('left.txt')\n"
    )
    results = await py(code, [True], [False])
    assert [r.value for r in results] == [True, False]


@pytest.mark.parametrize("bad", [{"$nope": 1}, {"a": 1, "b": 2}])
async def test_bad_input_value(py, bad) -> None:
    (result,) = await py("def f(x):\n    return x\n", [bad])
    assert result.status == "bad_input"


async def test_http_contract(client) -> None:
    response = await client.post(
        "/v1/run/python",
        json={"code": "def f(a):\n    return a * 2\n", "function": "f", "tests": [{"args": [4]}]},
    )
    assert response.status_code == 200
    assert response.json()["tests"][0]["value"] == 8
    bad = await client.post(
        "/v1/run/python", json={"code": "", "function": "1bad", "tests": [{"args": []}]}
    )
    assert bad.status_code == 422
