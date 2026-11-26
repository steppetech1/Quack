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

# `float_tol` — the strict tolerance of `Settings.code_float_tolerance` and an int equal to a
# float; `float_approx` — the same with the task's own `epsilon` (the code sandbox, teacher@5).
type PythonCompare = Literal["exact", "float_tol", "unordered", "float_approx"]
# The code sandbox (teacher@5): `inline_patch` — the learner completes or fixes a piece and checks
# it on the sample only; `full_problem` — a whole block, with hidden tests built in the background.
type CodeMode = Literal["inline_patch", "full_problem"]
# The hidden tests of a `full_problem` task: `none` — the mode has none; `pending` — the background
# builder is at work; `ready` — stored; `failed` — the reference failed twice, the task stays on the
# sample and the teacher's review.
type BuildStatus = Literal["none", "pending", "ready", "failed"]


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
    # The short name of a hidden case the learner sees when it fails («пустой массив»).
    name: str | None = None


class PythonTests(_Model):
    language: Literal["python"] = "python"
    function: str
    # Empty while the hidden tests of a sandbox task are not built (and for `inline_patch`).
    reference: str
    cases: list[PythonCase]
    # Tasks of the generator (teacher@4) predate the sandbox: a whole task with ready tests.
    mode: CodeMode = "full_problem"
    build: BuildStatus = "ready"
    # The tolerance of `float_approx`; `None` — `Settings.code_float_tolerance`.
    epsilon: float | None = None


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
    mode: CodeMode = "full_problem"
    build: BuildStatus = "ready"
    # A ready read-only database of `quack.practice.sql_presets` (`shop`, `university`): its schema
    # and data are copied into `schema_sql` and the data of every case.
    preset: str | None = None


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


_UPDATE_BUILT = (
    "UPDATE content.tasks SET code_tests = %(code_tests)s "
    "WHERE learner_id = %(learner_id)s AND task_id = %(task_id)s "
    "AND code_tests->>'build' = 'pending' RETURNING task_id"
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


async def store_built_tests(
    db: Db, *, learner_id: UUID, task_id: UUID, tests: PythonTests | SqlTests
) -> bool:
    """The background builder's result, written only while the task still waits for it
    (`build = pending`): a late or repeated result for the same `task_id` is dropped. `False` —
    nothing was written."""
    row = await db.fetch_one(
        _UPDATE_BUILT,
        {
            "learner_id": learner_id,
            "task_id": task_id,
            "code_tests": Jsonb(tests.model_dump(mode="json")),
        },
    )
    return row is not None


# --- the learner's draft (the code sandbox) -----------------------------------------------------


class CodeDraft(_Model):
    """The learner's current code and the statuses of its last run (`number: status`)."""

    code: str
    statuses: dict[str, str] = Field(default_factory=dict)


_SELECT_DRAFT = (
    "SELECT code_draft FROM content.tasks "
    "WHERE learner_id = %(learner_id)s AND task_id = %(task_id)s"
)
_UPDATE_DRAFT = (
    "UPDATE content.tasks SET code_draft = %(code_draft)s "
    "WHERE learner_id = %(learner_id)s AND task_id = %(task_id)s AND code_tests IS NOT NULL"
)


class _DraftRow(BaseModel):
    code_draft: dict[str, Any] | None


async def get_code_draft(db: Db, *, learner_id: UUID, task_id: UUID) -> CodeDraft | None:
    row = await db.fetch_one(
        _SELECT_DRAFT, {"learner_id": learner_id, "task_id": task_id}, row=_DraftRow
    )
    if row is None or row.code_draft is None:
        return None
    return CodeDraft.model_validate(row.code_draft)


async def store_code_draft(db: Db, *, learner_id: UUID, task_id: UUID, draft: CodeDraft) -> None:
    await db.execute(
        _UPDATE_DRAFT,
        {
            "learner_id": learner_id,
            "task_id": task_id,
            "code_draft": Jsonb(draft.model_dump(mode="json")),
        },
    )
