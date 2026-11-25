"""Generation of `code` tasks (Т-§6.10; documentation/идея-редактор-кода-и-запуск.md, «Проверка
задачи при генерации»).

1. `code_task_generator@1` writes the statement, the starter code, the reference solution and
   the inputs of the tests (never the expected results); code checks the form.
2. The reference runs on every input twice in the runner — for SQL the second time with the
   INSERT statements in reverse order. Any failure or any difference: the draft is dropped. The
   expected results are what it returned.
3. SQL: the hidden data sets must not all give an empty result, and no hidden result may equal
   an open one (a query with constants copied from the examples would pass).
4. `code_task_checker@1` writes its own solution from the statement and the open tests only;
   it must pass all five tests, and its judgement of the markup must agree (as `task_checker`).
   Otherwise the draft is dropped: the statement does not say what the tests check.

Only then the task is stored: `task.created` and the document with its tests in one
transaction. Ids come from the call key, so a retry writes the same tasks (law 5).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from quack.config import Settings
from quack.db import Db
from quack.events.records import TaskMarkupRecord
from quack.events.values import AnswerSpec, CodeLanguage, Level
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap
from quack.logging import get_logger
from quack.practice.code_check import all_numbers, open_numbers, run_cases
from quack.practice.code_role_outputs import (
    CodeTaskCheckOutput,
    CodeTaskGeneratorOutput,
    GeneratedCodeTask,
)
from quack.practice.code_tests import PythonCase, PythonTests, SqlCase, SqlTests
from quack.practice.code_values import rows_equal, sorts_at_top_level, values_equal
from quack.practice.markup import markup_record, validate_markup
from quack.practice.role_outputs import TaskCondition
from quack.practice.task_check import checker_variables
from quack.practice.task_generation import family_id, generated_task_id
from quack.practice.task_store import create_task
from quack.runner_client.client import RunnerClient
from quack.runner_client.contracts import (
    PythonRunRequest,
    PythonTest,
    PythonTestResult,
    SqlRunRequest,
    SqlTest,
    SqlTestResult,
)

log = get_logger(__name__)

CODE_GENERATOR_ROLE = "code_task_generator"
CODE_CHECKER_ROLE = "code_task_checker"
NONE_LINE = "(none)"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


@dataclass(frozen=True)
class CodeGenerationRequest:
    key: str
    skill_id: UUID
    skill_name: str
    topic_name: str
    level: Level
    language: CodeLanguage
    misconceptions: Mapping[UUID, str] | None = None
    course_context: str = ""
    excluded_families: Sequence[str] = ()
    domain_id: UUID | None = None
    session_id: UUID | None = None


@dataclass(frozen=True)
class _Draft:
    """A draft as `checker_variables` sees it (`TaskText`)."""

    condition: TaskCondition
    answer_spec: AnswerSpec


def generator_variables(request: CodeGenerationRequest, labels: LabelMap) -> dict[str, str]:
    skill = labels.label("skill", request.skill_id)
    known = [
        f"{label}: {statement}"
        for misconception_id, statement in (request.misconceptions or {}).items()
        if (label := labels.label("misconception", misconception_id))
    ]
    return {
        "topic_name": request.topic_name,
        "target_skill": f"{skill}: {request.skill_name}",
        "target_level": str(request.level),
        "language": request.language,
        "known_misconceptions": "\n".join(known) or NONE_LINE,
        "course_context": request.course_context or NONE_LINE,
        "excluded_families": "\n".join(request.excluded_families) or NONE_LINE,
    }


def _hidden_layout(hidden: list[bool], settings: Settings) -> str | None:
    want = [False] * settings.code_open_tests + [True] * settings.code_hidden_tests
    if hidden != want:
        return (
            f"exactly {settings.code_open_tests} open cases first, then "
            f"{settings.code_hidden_tests} hidden ones"
        )
    return None


def _python_args(case_json: str) -> list[Any] | None:
    try:
        args = json.loads(case_json)
    except ValueError:
        return None
    return args if isinstance(args, list) else None


def draft_errors(
    draft: GeneratedCodeTask, request: CodeGenerationRequest, labels: LabelMap, settings: Settings
) -> list[str]:
    """Form rules of one draft (code checks form, not truth — Т-§5.3)."""
    errors = [
        f"markup: {v.rule} ({v.detail})"
        for v in validate_markup(
            draft.markup,
            skill_labels=labels.labels("skill"),
            misconception_labels=labels.labels("misconception"),
            task_labels=labels.labels("task"),
        )
    ]
    if draft.markup.answer.type != "code":
        errors.append('markup.answer.type must be "code"')
    if draft.language != request.language:
        errors.append(f"language must be {request.language}")
    if not draft.reference.strip():
        errors.append("reference is empty")
    if draft.language == "python":
        if draft.function is None or not _IDENTIFIER.fullmatch(draft.function):
            errors.append("function must be a Python identifier")
        cases = draft.python_cases or []
        if (layout := _hidden_layout([c.hidden for c in cases], settings)) is not None:
            errors.append(f"python_cases: {layout}")
        for i, case in enumerate(cases):
            if _python_args(case.args_json) is None:
                errors.append(f"python_cases[{i}].args_json is not a JSON array")
    else:
        if not (draft.schema_sql or "").strip():
            errors.append("schema_sql is empty")
        cases_sql = draft.sql_cases or []
        if (layout := _hidden_layout([c.hidden for c in cases_sql], settings)) is not None:
            errors.append(f"sql_cases: {layout}")
    return errors


def output_errors(
    output: CodeTaskGeneratorOutput,
    request: CodeGenerationRequest,
    labels: LabelMap,
    settings: Settings,
) -> str | None:
    """The text goes back to the model once (Т-§4.5)."""
    errors = [
        f"tasks[{i}]: {error}"
        for i, draft in enumerate(output.tasks)
        for error in draft_errors(draft, request, labels, settings)
    ]
    return "; ".join(errors) if errors else None


# --- the reference ------------------------------------------------------------------------------


def _python_expected(case_raises: str | None, mutates: int | None, result: PythonTestResult) -> Any:
    """What the reference gave for one case; `_MISSING` when it failed."""
    if case_raises is not None:
        if result.status == "error" and result.error is not None:
            return {"raises": result.error.type} if result.error.type == case_raises else _MISSING
        return _MISSING
    if result.status != "ok":
        return _MISSING
    if mutates is not None:
        after = result.args_after or []
        return after[mutates] if mutates < len(after) else _MISSING
    return result.value


_MISSING = object()


async def python_reference_tests(
    runner: RunnerClient, draft: GeneratedCodeTask, settings: Settings
) -> PythonTests | None:
    """The tests of a Python draft with the expected results of its reference, run twice."""
    raw = draft.python_cases or []
    args = [_python_args(c.args_json) or [] for c in raw]
    request = PythonRunRequest(
        code=draft.reference,
        function=draft.function or "",
        tests=[PythonTest(args=a) for a in args],
        return_args=any(c.mutates_arg is not None for c in raw),
    )
    first = await runner.run_python(request)
    second = await runner.run_python(request)
    cases: list[PythonCase] = []
    for case, case_args, one, two in zip(raw, args, first.tests, second.tests, strict=True):
        expected = _python_expected(case.raises, case.mutates_arg, one)
        again = _python_expected(case.raises, case.mutates_arg, two)
        if expected is _MISSING or again is _MISSING:
            return None
        if case.raises is None and not values_equal(expected, again, tolerance=0.0):
            return None
        cases.append(
            PythonCase(
                args=case_args,
                expected=None if case.raises is not None else expected,
                compare=case.compare,
                mutates_arg=case.mutates_arg,
                raises=case.raises,
                hidden=case.hidden,
            )
        )
    return PythonTests(function=draft.function or "", reference=draft.reference, cases=cases)


def reversed_statements(data_sql: str) -> str:
    """The INSERT statements of a data script in reverse order (rows of one statement stay)."""
    statements = [s.strip() for s in data_sql.split(";") if s.strip()]
    return ";\n".join(reversed(statements)) + ";"


def _rows(result: SqlTestResult) -> list[list[Any]] | None:
    return result.rows if result.status == "ok" and result.rows is not None else None


async def sql_reference_tests(
    runner: RunnerClient, draft: GeneratedCodeTask, settings: Settings
) -> SqlTests | None:
    raw = draft.sql_cases or []
    schema = draft.schema_sql or ""
    ordered = sorts_at_top_level(draft.reference)
    first = await runner.run_sql(
        SqlRunRequest(
            schema_sql=schema,
            query=draft.reference,
            tests=[SqlTest(data_sql=c.data_sql) for c in raw],
        )
    )
    second = await runner.run_sql(
        SqlRunRequest(
            schema_sql=schema,
            query=draft.reference,
            tests=[SqlTest(data_sql=reversed_statements(c.data_sql)) for c in raw],
        )
    )
    compare = {
        "ordered": ordered,
        "tolerance": settings.code_float_tolerance,
        "sort_digits": settings.code_sql_sort_digits,
    }
    cases: list[SqlCase] = []
    for case, one, two in zip(raw, first.tests, second.tests, strict=True):
        rows, again = _rows(one), _rows(two)
        if rows is None or again is None or not rows_equal(rows, again, **compare):
            return None
        cases.append(SqlCase(data_sql=case.data_sql, expected_rows=rows, hidden=case.hidden))
    open_rows = [c.expected_rows for c in cases if not c.hidden]
    hidden_rows = [c.expected_rows for c in cases if c.hidden]
    if all(not rows for rows in hidden_rows):
        return None
    for rows in hidden_rows:
        if rows and any(rows_equal(rows, shown, **compare) for shown in open_rows):
            return None
    return SqlTests(schema_sql=schema, reference=draft.reference, ordered=ordered, cases=cases)


# --- the independent solution --------------------------------------------------------------------


def _open_tests_text(tests: PythonTests | SqlTests) -> str:
    lines = []
    for n in open_numbers(tests):
        case = tests.cases[n - 1]
        if isinstance(case, PythonCase):
            outcome = (
                f"raises {case.raises}"
                if case.raises
                else json.dumps(case.expected, ensure_ascii=False)
            )
            target = (
                f" (argument {case.mutates_arg} after the call)"
                if case.mutates_arg is not None
                else ""
            )
            lines.append(
                f"{n}. args = {json.dumps(case.args, ensure_ascii=False)} → {outcome}{target}"
            )
        else:
            lines.append(
                f"{n}. data:\n{case.data_sql}\nexpected rows: "
                f"{json.dumps(case.expected_rows, ensure_ascii=False)}"
            )
    return "\n\n".join(lines)


async def independent_check(
    gateway: LLMGateway,
    runner: RunnerClient,
    settings: Settings,
    draft: GeneratedCodeTask,
    tests: PythonTests | SqlTests,
    *,
    markup_view: _Draft,
    markup: TaskMarkupRecord,
    skill_names: Mapping[UUID, str],
    learner_id: UUID,
    key: str,
) -> bool:
    prompt = settings.llm_roles[CODE_CHECKER_ROLE].prompt
    if prompt is None:
        raise ValueError(f"role {CODE_CHECKER_ROLE!r} has no prompt")
    base = checker_variables(markup_view, markup, skill_names)
    language = (
        f"python, function {tests.function}" if isinstance(tests, PythonTests) else "sql (SQLite)"
    )
    result = await gateway.generate_structured(
        CODE_CHECKER_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        CodeTaskCheckOutput,
        {
            "problem": base["problem"],
            "language": language,
            "starter_code": draft.starter_code or NONE_LINE,
            "schema_sql": draft.schema_sql or NONE_LINE,
            "open_tests": _open_tests_text(tests),
            "claimed_markup": base["claimed_markup"],
        },
        learner_id,
        None,
        None,
        f"{key}:{prompt}",
    )
    checked: CodeTaskCheckOutput = result.value
    fits = list(checked.levels_fit)[: len(markup.tests)]
    if checked.method_named != markup.method_named or not all(fits):
        log.info(
            "code_task_dropped",
            key=key,
            reason="markup_disputed",
            method_named=[markup.method_named, checked.method_named],
            levels_fit=fits,
            note=checked.note,
        )
        return False
    views = await run_cases(runner, tests, checked.solution_code, all_numbers(tests), settings)
    failed = [f"{view.number}:{view.status}" for view in views if view.status != "passed"]
    if failed:
        log.info(
            "code_task_dropped",
            key=key,
            reason="independent_solution_failed",
            tests=failed,
            note=checked.note,
        )
    return not failed


# --- the whole call ------------------------------------------------------------------------------


async def generate_code_tasks(
    db: Db,
    gateway: LLMGateway,
    runner: RunnerClient,
    settings: Settings,
    request: CodeGenerationRequest,
    *,
    learner_id: UUID,
) -> list[UUID]:
    """Generate, check and store the code tasks of one request; the ids of the stored tasks.
    `RunnerUnavailableError` propagates (the job retries): nothing is stored without a check."""
    prompt = settings.llm_roles[CODE_GENERATOR_ROLE].prompt
    if prompt is None:
        raise ValueError(f"role {CODE_GENERATOR_ROLE!r} has no prompt")
    labels = LabelMap.build(
        settings,
        skills=[request.skill_id],
        misconceptions=list(request.misconceptions or {}),
        tasks=[],
    )
    result = await gateway.generate_structured(
        CODE_GENERATOR_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        CodeTaskGeneratorOutput,
        generator_variables(request, labels),
        learner_id,
        request.session_id,
        None,
        f"{request.key}:{prompt}",
        check=lambda output: output_errors(output, request, labels, settings),
    )
    names = {request.skill_id: request.skill_name}
    stored: list[UUID] = []
    output: CodeTaskGeneratorOutput = result.value
    for index, draft in enumerate(output.tasks):
        if draft_errors(draft, request, labels, settings):
            continue  # still wrong after the repair: dropped
        tests = (
            await python_reference_tests(runner, draft, settings)
            if draft.language == "python"
            else await sql_reference_tests(runner, draft, settings)
        )
        if tests is None:
            # the reference failed, differed between runs or the data sets are weak
            log.info("code_task_dropped", key=request.key, index=index, reason="reference")
            continue
        markup = markup_record(draft.markup, labels)
        answer = markup.answer.model_copy(
            update={
                "language": draft.language,
                "function": draft.function if draft.language == "python" else None,
                "starter_code": draft.starter_code,
            }
        )
        markup = markup.model_copy(update={"answer": answer})
        ok = await independent_check(
            gateway,
            runner,
            settings,
            draft,
            tests,
            markup_view=_Draft(draft.condition, answer),
            markup=markup,
            skill_names=names,
            learner_id=learner_id,
            key=f"{request.key}:{index}",
        )
        if not ok:
            continue
        task_id = generated_task_id(request.key, index)
        await create_task(
            db,
            learner_id=learner_id,
            task_id=task_id,
            family_id=family_id(request.key),
            source="generated",
            condition=draft.condition,
            markup=markup,
            verification="checked",
            actor=prompt,
            idempotency_key=f"task:{request.key}:{index}",
            domain_id=request.domain_id,
            session_id=request.session_id,
            code_tests=tests.model_dump(mode="json"),
        )
        stored.append(task_id)
    return stored
