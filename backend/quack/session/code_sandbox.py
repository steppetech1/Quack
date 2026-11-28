"""The code sandbox (teacher@5, owner decision 2026-11-26; ТЗ «песочница для кода»): a code task
the teacher writes in the turn, as a `code_sandbox` element and its task markup.

The task document gets its tests at once: one open case, the sample of the element. In
`full_problem` (Python) the hidden cases come later from the background builder
(`code_hidden.build_hidden_tests`, the `task.check` job of the turn): `build = pending` until then.
`inline_patch` and SQL have no hidden tests (`build = none`): the sample and the teacher's review.
Code checks the form of the element here; what fails it drops the task, never the turn.
"""

from __future__ import annotations

import json
import re
from typing import Any

from quack.config import Settings
from quack.events.values import AnswerSpec
from quack.practice.code_tests import PythonCase, PythonTests, SqlCase, SqlTests
from quack.practice.sql_presets import SQL_PRESETS, sql_preset
from quack.session.elements import CodeSandboxElement

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _json(text: str | None) -> tuple[bool, Any]:
    if text is None:
        return False, None
    try:
        return True, json.loads(text)
    except ValueError:
        return False, None


def sql_rows(value: Any) -> list[list[Any]] | None:
    """The sample rows of an SQL task: an array of rows; a flat array is one column (`["Anna",
    "Vera"]` — two rows of one value each, as a model often writes a one-column result). `None` —
    not rows."""
    if not isinstance(value, list):
        return None
    if all(isinstance(row, list) for row in value):
        return value
    if any(isinstance(cell, list | dict) for cell in value):
        return None
    return [[cell] for cell in value]


def sandbox_problems(element: CodeSandboxElement, settings: Settings) -> list[str]:
    """Form rules of the element; the empty list — it makes a task."""
    problems: list[str] = []
    if not element.statement.strip():
        problems.append("code_sandbox: statement is empty")
    limit = settings.code_max_chars if element.language == "python" else settings.sql_max_chars
    if len(element.starter_code) > limit:
        problems.append("code_sandbox: starter_code is too long")
    if element.epsilon is not None and not element.epsilon > 0:
        problems.append("code_sandbox: epsilon must be positive")
    parsed, expected = _json(element.sample_expected_json)
    if not parsed:
        problems.append("code_sandbox: sample_expected_json is not JSON")
    if element.language == "python":
        if element.entry_point is None or not _IDENTIFIER.fullmatch(element.entry_point):
            problems.append("code_sandbox: entry_point must be a Python identifier")
        ok, args = _json(element.sample_args_json)
        if not ok or not isinstance(args, list):
            problems.append("code_sandbox: sample_args_json is not a JSON array")
        if element.sql_preset is not None:
            problems.append("code_sandbox: sql_preset is for SQL")
    else:
        if element.sql_preset not in SQL_PRESETS:
            problems.append(f"code_sandbox: sql_preset must be one of {', '.join(SQL_PRESETS)}")
        if parsed and sql_rows(expected) is None:
            problems.append("code_sandbox: sample_expected_json must be an array of rows")
    return problems


def sandbox_mode(element: CodeSandboxElement) -> str:
    """SQL has no hidden tests in the sandbox: a query on a ready database is checked on the
    sample and by the review (ТЗ: SQL goes to `inline_patch`)."""
    return "inline_patch" if element.language == "sql" else element.mode


def sandbox_tests(element: CodeSandboxElement) -> PythonTests | SqlTests:
    """The first tests of the task: the sample, open. Call after `sandbox_problems` is empty."""
    expected = json.loads(element.sample_expected_json)
    mode = sandbox_mode(element)
    if element.language == "python":
        compare = element.compare
        return PythonTests(
            function=element.entry_point or "",
            reference="",
            cases=[
                PythonCase(
                    args=json.loads(element.sample_args_json or "[]"),
                    expected=expected,
                    compare=compare,
                    hidden=False,
                )
            ],
            mode=mode,  # type: ignore[arg-type]
            build="pending" if mode == "full_problem" else "none",
            epsilon=element.epsilon,
        )
    preset = sql_preset(element.sql_preset or "")
    return SqlTests(
        schema_sql=preset.schema_sql,
        reference="",
        # «exact» — the order of rows counts; the others compare rows as a multiset.
        ordered=element.compare == "exact",
        cases=[
            SqlCase(data_sql=preset.data_sql, expected_rows=sql_rows(expected) or [], hidden=False)
        ],
        mode="inline_patch",
        build="none",
        preset=preset.name,
    )


def sandbox_answer_spec(element: CodeSandboxElement, answer: AnswerSpec) -> AnswerSpec:
    """The answer spec of the task: the markup's, with the language, function and starter code of
    the element (the element wins: it is what the editor shows)."""
    return answer.model_copy(
        update={
            # a code answer has no value of its own: the tests decide
            "value": None,
            "items": None,
            "type": "code",
            "language": element.language,
            "function": element.entry_point if element.language == "python" else None,
            "starter_code": element.starter_code,
        }
    )
