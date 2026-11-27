"""Output schemas of `code_task_generator@1` and `code_task_checker@1` (Т-§6.10).

The generator writes the inputs of the tests, never their expected results: those come from
running its reference solution in the runner. Values of Python arguments are tagged JSON
(`quack.practice.code_values`) inside a string (`args_json`), so the schema stays strict.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.events.values import CodeLanguage
from quack.practice.code_tests import PythonCompare
from quack.practice.markup import TaskMarkup
from quack.practice.role_outputs import TaskCondition


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class GeneratedPythonCase(_Strict):
    args_json: str
    compare: PythonCompare
    mutates_arg: int | None
    raises: str | None
    hidden: bool


class GeneratedSqlCase(_Strict):
    data_sql: str
    hidden: bool


class GeneratedCodeTask(_Strict):
    condition: TaskCondition
    markup: TaskMarkup
    language: CodeLanguage
    function: str | None
    starter_code: str
    reference: str
    schema_sql: str | None
    python_cases: list[GeneratedPythonCase] | None
    sql_cases: list[GeneratedSqlCase] | None
    explanation: str


class CodeTaskGeneratorOutput(_Strict):
    tasks: list[GeneratedCodeTask] = Field(min_length=1)


class CodeTaskCheckOutput(_Strict):
    """The checker's own solution from the statement and the open tests only, and its judgement
    of the markup, as `task_checker@1` gives it (Т-§5.6)."""

    solution_code: str
    method_named: bool = Field(alias="methodNamed")
    levels_fit: list[bool]
    note: str


type CodeCheckDecision = Literal["passed", "rejected"]
