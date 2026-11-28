"""Contracts of the runner (`runner/quack_runner/contracts.py`); a contract test keeps the two
copies equal. Values are tagged JSON (`quack.practice.code_values`)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

type TestStatus = Literal[
    "ok",
    "error",
    "timeout",
    "memory",
    "output_limit",
    "unsupported_value",
    "no_function",
    "bad_input",
    "row_limit",
    "not_run",
]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Response(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class RunError(_Response):
    type: str
    message: str
    line: int | None = None


class PythonTest(_Request):
    args: Annotated[list[Any], Field(max_length=16)]


class PythonRunRequest(_Request):
    code: str
    function: str
    tests: list[PythonTest]
    return_args: bool = False


class PythonTestResult(_Response):
    status: TestStatus
    value: Any = None
    args_after: list[Any] | None = None
    error: RunError | None = None
    stdout: str = ""
    stderr: str = ""
    time_ms: int = 0
    called: str | None = None


class PythonRunResponse(_Response):
    tests: list[PythonTestResult]


class SqlTest(_Request):
    data_sql: str


class SqlRunRequest(_Request):
    schema_sql: str
    query: str
    tests: list[SqlTest]


class SqlTestResult(_Response):
    status: TestStatus
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None
    error: RunError | None = None
    time_ms: int = 0


class SqlRunResponse(_Response):
    tests: list[SqlTestResult]
