"""Tests of a `code` task (Т-§6.10): kept in `content.tasks.code_tests` only — never in an event,
never sent to the model, and the hidden ones never to the client.

Python: each case is the arguments of the function and what it must give back — a value, the
state of one argument after the call (a function that changes it in place) or an exception.
SQL: each case is a data script for the task's schema and the rows the reference query gives on
it; `ordered` — the reference query sorts at the top level, so the order of rows counts.
Values are tagged JSON (`quack.practice.code_values`).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from quack.db.db import Db

type PythonCompare = Literal["exact", "float_tol", "unordered"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PythonCase(_Model):
    args: list[Any]
    expected: Any = None
    compare: PythonCompare = "exact"
    # The index of the argument whose state after the call is compared with `expected`.
    mutates_arg: Annotated[int, Field(ge=0)] | None = None
    # The name of the exception the call must raise (`ValueError`); `expected` is unused.
    raises: str | None = None
    hidden: bool


class PythonTests(_Model):
    language: Literal["python"] = "python"
    function: str
    reference: str
    cases: list[PythonCase]


class SqlCase(_Model):
    data_sql: str
    expected_rows: list[list[Any]]
    hidden: bool


class SqlTests(_Model):
    language: Literal["sql"] = "sql"
    schema_sql: str
    reference: str
    ordered: bool
    cases: list[SqlCase]


type CodeTests = Annotated[PythonTests | SqlTests, Field(discriminator="language")]
CODE_TESTS: TypeAdapter[PythonTests | SqlTests] = TypeAdapter(CodeTests)

_SELECT = (
    "SELECT code_tests FROM content.tasks "
    "WHERE learner_id = %(learner_id)s AND task_id = %(task_id)s"
)
_UPDATE = (
    "UPDATE content.tasks SET code_tests = %(code_tests)s "
    "WHERE learner_id = %(learner_id)s AND task_id = %(task_id)s"
)


class _Row(BaseModel):
    code_tests: dict[str, Any] | None


async def get_code_tests(
    db: Db, *, learner_id: UUID, task_id: UUID
) -> PythonTests | SqlTests | None:
    """The tests of a learner's task; `None` — no such task of this learner, or not a code task."""
    row = await db.fetch_one(_SELECT, {"learner_id": learner_id, "task_id": task_id}, row=_Row)
    if row is None or row.code_tests is None:
        return None
    return CODE_TESTS.validate_python(row.code_tests)


async def store_code_tests(
    db: Db, *, learner_id: UUID, task_id: UUID, tests: PythonTests | SqlTests
) -> None:
    """Attach the tests to the task document; call in the transaction that stores the task."""
    await db.execute(
        _UPDATE,
        {
            "learner_id": learner_id,
            "task_id": task_id,
            "code_tests": Jsonb(tests.model_dump(mode="json")),
        },
    )
