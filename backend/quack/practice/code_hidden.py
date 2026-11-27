"""Hidden tests of a `full_problem` sandbox task, built in the background while the learner
already writes code (teacher@5; ТЗ «песочница для кода», режим 2).

1. `code_test_builder@1` writes a reference solution and up to `Settings.code_hidden_max` extra
   inputs with short case names — never their expected results.
2. Double validation in the runner: the reference runs on the teacher's sample first; a failure
   or a result that is not the sample's expected one means the reference is broken. Only then the
   results of the reference on the extra inputs become their expected results (code computes,
   the model never does — law 2).
3. A failure, a timeout or a sample mismatch: one more attempt (`Settings.code_build_attempts`).
   Failed again — `build = failed`: the task silently stays on the sample and the review.

The result is written only while the task still waits for it (`store_built_tests`): a late
result for a task the learner left is dropped by its `task_id`. The expected results stay in the
task document: never in an event, never sent to the client.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from quack.config import Settings
from quack.db.db import Db
from quack.llm.gateway import LLMGateway
from quack.logging import get_logger
from quack.practice.code_check import _python_status
from quack.practice.code_tests import PythonCase, PythonTests, get_code_tests, store_built_tests
from quack.practice.task_store import get_task
from quack.runner_client.client import RunnerClient
from quack.runner_client.contracts import PythonRunRequest, PythonTest

log = get_logger(__name__)

BUILDER_ROLE = "code_test_builder"
NONE_LINE = "(none)"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BuiltCase(_Strict):
    name: str
    args_json: str


class CodeTestBuilderOutput(_Strict):
    reference_solution: str
    cases: list[BuiltCase] = Field(max_length=8)


def _args(text: str) -> list[Any] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, list) else None


def output_errors(output: CodeTestBuilderOutput, settings: Settings) -> str | None:
    """Form of the output; the text goes back to the model once (Т-§4.5)."""
    errors = []
    if not output.reference_solution.strip():
        errors.append("reference_solution is empty")
    if len(output.cases) > settings.code_hidden_max:
        errors.append(f"at most {settings.code_hidden_max} cases")
    for i, case in enumerate(output.cases):
        if not case.name.strip():
            errors.append(f"cases[{i}].name is empty")
        if _args(case.args_json) is None:
            errors.append(f"cases[{i}].args_json is not a JSON array")
    return "; ".join(errors) or None


def _compare_line(tests: PythonTests) -> str:
    case = tests.cases[0]
    if case.compare == "unordered":
        return "the order of elements does not matter"
    if case.compare == "float_approx":
        return f"numbers within {tests.epsilon or 'a small tolerance'}"
    return "exact equality"


def builder_variables(
    tests: PythonTests, statement: str, starter_code: str, failure: str | None, settings: Settings
) -> dict[str, str]:
    sample = tests.cases[0]
    return {
        "statement": statement,
        "entry_point": tests.function,
        "starter_code": starter_code or NONE_LINE,
        "sample": (
            f"args = {json.dumps(sample.args, ensure_ascii=False)} → "
            f"{json.dumps(sample.expected, ensure_ascii=False)}"
        ),
        "compare": _compare_line(tests),
        "max_cases": str(settings.code_hidden_max),
        "previous_failure": failure or NONE_LINE,
    }


async def validated_tests(
    runner: RunnerClient, tests: PythonTests, output: CodeTestBuilderOutput, settings: Settings
) -> tuple[PythonTests | None, str | None]:
    """The tests with the hidden cases, or `None` and why the attempt is thrown away.
    `RunnerUnavailableError` propagates: nothing is decided without a run."""
    sample = tests.cases[0]
    extra = [
        (case.name.strip(), args)
        for case in output.cases[: settings.code_hidden_max]
        if (args := _args(case.args_json)) is not None
    ]
    response = await runner.run_python(
        PythonRunRequest(
            code=output.reference_solution,
            function=tests.function,
            tests=[PythonTest(args=sample.args), *(PythonTest(args=a) for _, a in extra)],
        )
    )
    first, *rest = response.tests
    # 1. the reference against the teacher's sample
    if first.status != "ok":
        return None, f"the reference failed on the sample: {first.status}"
    if _python_status(sample, first, settings, tests.epsilon) != "passed":
        got = json.dumps(first.value, ensure_ascii=False)
        return None, f"the reference returned {got} on the sample, not the expected result"
    # 2. its results on the extra inputs are their expected results
    cases: list[PythonCase] = [sample]
    for (name, args), result in zip(extra, rest, strict=True):
        if result.status != "ok":
            return None, f"the reference failed on «{name}»: {result.status}"
        cases.append(
            PythonCase(
                args=args,
                expected=result.value,
                compare=sample.compare,
                hidden=True,
                name=name,
            )
        )
    built = tests.model_copy(
        update={"reference": output.reference_solution, "cases": cases, "build": "ready"}
    )
    return built, None


async def build_hidden_tests(
    db: Db,
    gateway: LLMGateway,
    runner: RunnerClient,
    settings: Settings,
    *,
    learner_id: UUID,
    task_id: UUID,
) -> str | None:
    """Build and store the hidden tests of one task; the new `build`, or `None` when the task
    does not wait for them (not a sandbox `full_problem`, already built, or not the learner's)."""
    tests = await get_code_tests(db, learner_id=learner_id, task_id=task_id)
    if not isinstance(tests, PythonTests) or tests.build != "pending":
        return None
    task = await get_task(db, learner_id=learner_id, task_id=task_id)
    if task is None:
        return None
    prompt = settings.llm_roles[BUILDER_ROLE].prompt
    if prompt is None:
        raise ValueError(f"role {BUILDER_ROLE!r} has no prompt")
    failure: str | None = None
    for attempt in range(settings.code_build_attempts):
        result = await gateway.generate_structured(
            BUILDER_ROLE,
            int(prompt.rsplit("@", 1)[1]),
            CodeTestBuilderOutput,
            builder_variables(
                tests, task.condition.text, task.answer_spec.starter_code or "", failure, settings
            ),
            learner_id,
            None,
            None,
            f"code_tests:{task_id}:{attempt}:{prompt}",
            check=lambda output: output_errors(output, settings),
        )
        if (errors := output_errors(result.value, settings)) is not None:
            failure = errors
        else:
            built, failure = await validated_tests(runner, tests, result.value, settings)
            if built is not None:
                stored = await store_built_tests(
                    db, learner_id=learner_id, task_id=task_id, tests=built
                )
                log.info("code_tests_built", task_id=str(task_id), stored=stored, attempt=attempt)
                return "ready" if stored else None
        log.info("code_tests_attempt_failed", task_id=str(task_id), attempt=attempt, why=failure)
    failed = tests.model_copy(update={"build": "failed"})
    stored = await store_built_tests(db, learner_id=learner_id, task_id=task_id, tests=failed)
    return "failed" if stored else None
