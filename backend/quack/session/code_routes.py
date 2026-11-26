"""Routes of `code` tasks (Т-§6.1 «По тестам», Т-§6.10).

- `GET /v1/tasks/{task_id}/code` — what the editor needs: the language, the function, the starter
  code and the open tests; of the hidden ones only their count; the sandbox mode, the state of the
  background build of the hidden tests (`build`) and the learner's saved draft. The client asks
  again while `build = pending`.
- `POST /v1/tasks/{task_id}/code/run` — every test there is now: the open ones and, once built,
  the hidden ones (teacher@4 tasks: the open ones only); or, for Python, the learner's own
  arguments. Writes no event; the code and the statuses are kept as the draft.
- `PUT /v1/tasks/{task_id}/code/draft` — the learner's current code, kept by `task_id`.
- `POST /v1/sessions/{session_id}/tasks/{task_id}/code/submit` — every test, then
  `answer.submitted` and `verdict.recorded`. A hidden test never comes back with its expected
  result: a named sandbox case with its input and what the code gave, an old one with its status.
  The teacher's review is the next turn (`retry_turn` on this answer).

The runner down or busy → 503 / 429, nothing is written. No model is called here: hints go
through the usual `hint_request` action.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from quack.api.auth import get_current_learner_id
from quack.api.errors import ApiError
from quack.api.ratelimit import check_rate_limit, enforce_action_rate_limit
from quack.api.resources import AppResources, get_resources
from quack.config import Settings
from quack.db.db import Db
from quack.events.errors import IdempotencyConflictError
from quack.practice.code_answers import submit_code
from quack.practice.code_check import (
    CodeTestView,
    CustomRunView,
    all_numbers,
    learner_views,
    open_numbers,
    run_cases,
    run_custom,
)
from quack.practice.code_tests import (
    BuildStatus,
    CodeDraft,
    CodeMode,
    PythonTests,
    SqlTests,
    get_code_draft,
    get_code_tests,
    store_code_draft,
)
from quack.practice.task_store import get_task
from quack.runner_client.client import RunnerBusyError, RunnerClient, RunnerUnavailableError
from quack.session import sessions
from quack.session.record import find_element_task

router = APIRouter(
    prefix="/v1",
    tags=["code"],
    dependencies=[Depends(get_current_learner_id), Depends(enforce_action_rate_limit)],
)

type CurrentLearnerId = Annotated[UUID, Depends(get_current_learner_id)]
type Resources = Annotated[AppResources, Depends(get_resources)]


class OpenPythonTest(BaseModel):
    number: int
    args: list[Any]
    expected: Any = None
    raises: str | None = None
    mutates_arg: int | None = None


class OpenSqlTest(BaseModel):
    number: int
    data_sql: str
    expected_rows: list[list[Any]]


class CodeTaskView(BaseModel):
    """The editor of a `code` task. Hidden tests: their count only."""

    task_id: UUID
    # The statement (Markdown): a `code_task` element carries none of its own.
    condition: str
    language: Literal["python", "sql"]
    function: str | None
    starter_code: str | None
    schema_sql: str | None
    ordered: bool | None
    open_python_tests: list[OpenPythonTest]
    open_sql_tests: list[OpenSqlTest]
    hidden_tests: int
    # The code sandbox (teacher@5): `inline_patch` or `full_problem`; the hidden tests are built in
    # the background — `pending` while they are, `failed` when the task stays on the sample.
    mode: CodeMode
    build: BuildStatus
    sql_preset: str | None = None
    # The learner's saved code and the statuses of its last run; `None` — nothing saved yet.
    draft: CodeDraft | None = None


class DraftRequest(BaseModel):
    code: str


class RunRequest(BaseModel):
    code: str
    # Python only: run the function on these arguments (tagged JSON values), nothing compared.
    args: Annotated[list[Any], Field(max_length=16)] | None = None


class RunResponse(BaseModel):
    tests: list[CodeTestView]
    custom: CustomRunView | None = None
    # The build of the hidden tests when the code ran: `pending` — only the sample ran.
    build: BuildStatus = "ready"


class SubmitRequest(BaseModel):
    code: str
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]


class SubmitResponse(BaseModel):
    answer_seq: int
    outcome: Literal["correct", "incorrect", "partial", "unclear"]
    tests: list[CodeTestView]


async def _task_tests(db: Db, learner_id: UUID, task_id: UUID) -> PythonTests | SqlTests:
    tests = await get_code_tests(db, learner_id=learner_id, task_id=task_id)
    if tests is None:
        raise ApiError("NOT_FOUND", f"no code task {task_id}", status_code=404)
    return tests


def _unavailable() -> ApiError:
    return ApiError(
        "SERVICE_UNAVAILABLE",
        "code cannot be run now",
        status_code=503,
        details={"service": "runner"},
    )


def _runner(resources: AppResources) -> RunnerClient:
    if resources.runner is None:
        raise _unavailable()
    return resources.runner


def _check_size(code: str, tests: PythonTests | SqlTests, settings: Settings) -> None:
    limit = settings.code_max_chars if tests.language == "python" else settings.sql_max_chars
    if len(code) > limit:
        raise ApiError(
            "VALIDATION_ERROR",
            f"code is longer than {limit} characters",
            status_code=422,
            details={"reason": "code_too_long", "max_chars": limit},
        )


def _runner_error(exc: RunnerUnavailableError) -> ApiError:
    if isinstance(exc, RunnerBusyError):
        return ApiError(
            "RATE_LIMITED",
            "all sandboxes are busy",
            status_code=429,
            details={"reason": "runner_busy", "retry_after_sec": 2},
        )
    return _unavailable()


@router.get("/tasks/{task_id}/code", response_model=CodeTaskView)
async def get_code_task(
    task_id: UUID, learner_id: CurrentLearnerId, resources: Resources
) -> CodeTaskView:
    return await _code_view(resources.db, learner_id, task_id)


@router.get(
    "/sessions/{session_id}/turns/{turn_seq}/elements/{element_id}/code",
    response_model=CodeTaskView,
)
async def get_element_code_task(
    session_id: UUID,
    turn_seq: int,
    element_id: str,
    learner_id: CurrentLearnerId,
    resources: Resources,
) -> CodeTaskView:
    """The code task behind an element of a teacher turn: elements carry no task id, so the
    client asks here whether to open the editor; 404 — no task, or not a code task."""
    task_id = await find_element_task(resources.db, learner_id, turn_seq, element_id)
    if task_id is None:
        raise ApiError("NOT_FOUND", f"no task behind element {element_id!r}", status_code=404)
    return await _code_view(resources.db, learner_id, task_id)


async def _code_view(db: Db, learner_id: UUID, task_id: UUID) -> CodeTaskView:
    tests = await _task_tests(db, learner_id, task_id)
    task = await get_task(db, learner_id=learner_id, task_id=task_id)
    draft = await get_code_draft(db, learner_id=learner_id, task_id=task_id)
    spec = task.answer_spec if task is not None else None
    condition = task.condition.text if task is not None else ""
    hidden = sum(case.hidden for case in tests.cases)
    common: dict[str, Any] = {"mode": tests.mode, "build": tests.build, "draft": draft}
    if isinstance(tests, PythonTests):
        return CodeTaskView(
            task_id=task_id,
            condition=condition,
            language="python",
            function=tests.function,
            starter_code=spec.starter_code if spec else None,
            schema_sql=None,
            ordered=None,
            open_python_tests=[
                OpenPythonTest(
                    number=n,
                    args=case.args,
                    expected=case.expected,
                    raises=case.raises,
                    mutates_arg=case.mutates_arg,
                )
                for n, case in enumerate(tests.cases, start=1)
                if not case.hidden
            ],
            open_sql_tests=[],
            hidden_tests=hidden,
            **common,
        )
    return CodeTaskView(
        task_id=task_id,
        condition=condition,
        language="sql",
        function=None,
        starter_code=spec.starter_code if spec else None,
        schema_sql=tests.schema_sql,
        ordered=tests.ordered,
        open_python_tests=[],
        open_sql_tests=[
            OpenSqlTest(number=n, data_sql=case.data_sql, expected_rows=case.expected_rows)
            for n, case in enumerate(tests.cases, start=1)
            if not case.hidden
        ],
        hidden_tests=hidden,
        sql_preset=tests.preset,
        **common,
    )


@router.post("/tasks/{task_id}/code/run", response_model=RunResponse)
async def run_code(
    task_id: UUID, body: RunRequest, learner_id: CurrentLearnerId, resources: Resources
) -> RunResponse:
    settings = resources.settings
    tests = await _task_tests(resources.db, learner_id, task_id)
    _check_size(body.code, tests, settings)
    await check_rate_limit(
        resources.redis, "code_runs", str(learner_id), settings.code_runs_per_min
    )
    runner = _runner(resources)
    try:
        if body.args is not None:
            if not isinstance(tests, PythonTests):
                raise ApiError("VALIDATION_ERROR", "own arguments are for Python", status_code=422)
            custom = await run_custom(
                runner, tests, body.code, body.args, settings.code_output_chars
            )
            return RunResponse(tests=[], custom=custom, build=tests.build)
        # The sandbox runs every test it has (the hidden ones once built; their expected results
        # stay here); a teacher@4 task — its open tests, the hidden ones are for the submit.
        numbers = open_numbers(tests) if _generated(tests) else all_numbers(tests)
        views = learner_views(await run_cases(runner, tests, body.code, numbers, settings))
    except RunnerUnavailableError as exc:
        raise _runner_error(exc) from exc
    draft = CodeDraft(code=body.code, statuses={str(v.number): v.status for v in views})
    await store_code_draft(resources.db, learner_id=learner_id, task_id=task_id, draft=draft)
    return RunResponse(tests=views, build=tests.build)


@router.put("/tasks/{task_id}/code/draft", status_code=204)
async def save_draft(
    task_id: UUID, body: DraftRequest, learner_id: CurrentLearnerId, resources: Resources
) -> None:
    """The learner's current code (the statuses of the last run are kept); writes no event."""
    tests = await _task_tests(resources.db, learner_id, task_id)
    _check_size(body.code, tests, resources.settings)
    old = await get_code_draft(resources.db, learner_id=learner_id, task_id=task_id)
    statuses = old.statuses if old is not None and old.code == body.code else {}
    await store_code_draft(
        resources.db,
        learner_id=learner_id,
        task_id=task_id,
        draft=CodeDraft(code=body.code, statuses=statuses),
    )


def _generated(tests: PythonTests | SqlTests) -> bool:
    """A task of the generator (teacher@4): hidden cases without names. Its run shows the open
    tests only; the sandbox runs every case, since a hidden one never shows its expected result."""
    hidden = [case for case in tests.cases if case.hidden]
    return bool(hidden) and all(getattr(case, "name", None) is None for case in hidden)


@router.post(
    "/sessions/{session_id}/tasks/{task_id}/code/submit",
    response_model=SubmitResponse,
    status_code=201,
)
async def submit(
    session_id: UUID,
    task_id: UUID,
    body: SubmitRequest,
    learner_id: CurrentLearnerId,
    resources: Resources,
) -> SubmitResponse:
    settings = resources.settings
    db = resources.db
    tests = await _task_tests(db, learner_id, task_id)
    _check_size(body.code, tests, settings)
    try:
        session = await sessions.get_session(db, learner_id=learner_id, session_id=session_id)
    except LookupError as exc:
        raise ApiError("NOT_FOUND", str(exc), status_code=404) from exc
    await check_rate_limit(
        resources.redis,
        "code_submits",
        f"{learner_id}:{task_id}",
        settings.code_submits_per_min,
    )
    runner = _runner(resources)
    try:
        submitted = await submit_code(
            db,
            runner,
            learner_id=learner_id,
            session_id=session_id,
            domain_id=session.domain_id,
            task_id=task_id,
            tests=tests,
            code=body.code,
            idempotency_key=body.idempotency_key,
            settings=settings,
            redis=resources.redis,
        )
    except RunnerUnavailableError as exc:
        raise _runner_error(exc) from exc
    except IdempotencyConflictError as exc:
        raise ApiError("IDEMPOTENCY_CONFLICT", str(exc), status_code=409) from exc
    return SubmitResponse(
        answer_seq=submitted.answer_seq,
        outcome=submitted.verdict.outcome,
        tests=submitted.tests,
    )
