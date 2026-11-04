"""Seams of the Phase 2 streams (ТЗ Фазы 3 WP6.4, §8.6a `T-H7`, `T-H8`)."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest

from quack.compute_client.client import ComputeClient, MathCheckRequest, MathCheckResponse
from quack.config import Settings
from quack.llm.gateway import LLMGateway
from quack.practice.evaluation import TaskToCheck, evaluate_answer, rubric_lines
from quack.practice.generation import GeneratedTaskError, build_generated_task
from quack.practice.schemas import TaskGenOutput, parse_reference_solution
from quack.session.spoiler_guard import spoiler_found

# --- T-H8 (а): reference_solution of task_gen@1 -------------------------------------------


def _task_gen(**overrides: Any) -> TaskGenOutput:
    data: dict[str, Any] = {
        "statement": "Differentiate x^3.",
        "options": [],
        "answer": {"type": "expression", "value": "3*x**2", "units": None},
        "solution": "Bring the exponent down.\nLower the exponent by one.",
        "hints": ["Power rule.", "Exponent down.", "3 times x squared."],
        "tests": [{"skill": "S1", "level": 3}],
        "method_named": True,
        "variant_of": None,
        "form": None,
        "pair_task": None,
    }
    data.update(overrides)
    return TaskGenOutput.model_validate(data)


CHOICE_OPTIONS = [
    {"label": "A", "text": "2x^3", "correct": False, "catches": None},
    {"label": "B", "text": "3x^2", "correct": True, "catches": None},
]


def _bank_task(kind: str, reference: dict[str, Any]) -> TaskToCheck:
    return TaskToCheck(
        task_id=uuid4(),
        kind=kind,
        prompt_text="Differentiate x^3.",
        reference_solution=reference,
        target_skills=[],
        target_misconceptions=[],
        difficulty=0.5,
        topic_id=uuid4(),
        family_id="f",
    )


class _Compute:
    def __init__(self) -> None:
        self.requests: list[MathCheckRequest] = []

    async def check_math(self, request: MathCheckRequest) -> MathCheckResponse:
        self.requests.append(request)
        return MathCheckResponse(
            status="completed",
            is_equivalent=True,
            normalized_student=None,
            diff_hint=None,
            error_message=None,
        )


async def _evaluate(task: TaskToCheck, answer: str, compute: _Compute, settings: Settings) -> bool:
    verdict = await evaluate_answer(
        task,
        answer,
        [],
        learner_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        idempotency_key="th8",
        gateway=cast(LLMGateway, None),  # math with a compute verdict and choice call no model
        compute=cast(ComputeClient, compute),
        settings=settings,
    )
    return verdict.is_correct


async def test_phase2_seams_agree(settings: Settings) -> None:
    """T-H8 (а), side of task_gen@1: the `reference_solution` of each kind passes
    `ReferenceSolution` and is read by practice and the spoiler guard as stored."""
    compute = _Compute()

    math = build_generated_task(_task_gen(), "math", uuid4(), [])
    parse_reference_solution("math", math.reference_solution)
    task = _bank_task("math", math.reference_solution)
    assert await _evaluate(task, "3*x^2", compute, settings)
    (request,) = compute.requests
    assert (request.reference_expr, request.mode, request.variables, request.tolerance) == (
        "3*x**2",
        "expression_equiv",
        ["x"],
        1e-6,
    )
    assert spoiler_found("So the derivative is 3*x**2.", task, settings)

    choice = build_generated_task(
        _task_gen(answer={"type": "choice", "value": "B", "units": None}, options=CHOICE_OPTIONS),
        "choice",
        uuid4(),
        [],
    )
    parse_reference_solution("choice", choice.reference_solution)
    task = _bank_task("choice", choice.reference_solution)
    assert await _evaluate(task, "B", compute, settings)
    assert spoiler_found("The answer is 3x^2.", task, settings)

    open_task = build_generated_task(
        _task_gen(answer={"type": "free_text", "value": "By the power rule.", "units": None}),
        "open",
        uuid4(),
        [],
    )
    reference = open_task.reference_solution
    parse_reference_solution("open", reference)
    assert rubric_lines(reference) == (
        "R1: Bring the exponent down.\nR2: Lower the exponent by one."
    )
    assert not spoiler_found("By the power rule.", _bank_task("open", reference), settings)


def test_wrong_reference_of_task_gen_is_rejected() -> None:
    """T-H8 (а): an answer of task_gen@1 that gives no valid `reference_solution` does not
    reach the bank (`GeneratedTaskError`, practice falls back to the bank)."""
    with pytest.raises(GeneratedTaskError):
        build_generated_task(
            _task_gen(answer={"type": "expression", "value": "", "units": None}),
            "math",
            uuid4(),
            [],
        )
