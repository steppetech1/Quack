"""HTTP contracts of the runner. Values are tagged JSON (see `harness_python.py`); the runner
passes them through and never compares them. The backend keeps a copy of these models in
`quack.runner_client.contracts`; a contract test checks they agree."""

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

MAX_TESTS = 20


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunError(_Model):
    type: str
    message: str
    line: int | None = None


class PythonTest(_Model):
    args: Annotated[list[Any], Field(max_length=16)]


class PythonRunRequest(_Model):
    code: Annotated[str, Field(max_length=64 * 1024)]
    function: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
    tests: Annotated[list[PythonTest], Field(min_length=1, max_length=MAX_TESTS)]
    # Report the arguments after the call too (a test of a function that changes them).
    return_args: bool = False


class PythonTestResult(_Model):
    status: TestStatus
    value: Any = None
    args_after: list[Any] | None = None
    error: RunError | None = None
    stdout: str = ""
    stderr: str = ""
    time_ms: int = 0
    # The name of the learner's only own function, called because `function` is not defined.
    called: str | None = None


class PythonRunResponse(_Model):
    tests: list[PythonTestResult]


class SqlTest(_Model):
    data_sql: Annotated[str, Field(max_length=64 * 1024)]


class SqlRunRequest(_Model):
    schema_sql: Annotated[str, Field(max_length=64 * 1024)]
    query: Annotated[str, Field(max_length=16 * 1024)]
    tests: Annotated[list[SqlTest], Field(min_length=1, max_length=MAX_TESTS)]


class SqlTestResult(_Model):
    status: TestStatus
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None
    error: RunError | None = None
    time_ms: int = 0


class SqlRunResponse(_Model):
    tests: list[SqlTestResult]


class RunnerError(_Model):
    error: str
