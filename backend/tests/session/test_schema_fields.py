"""New optional fields of the session request and LLM output schemas."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from quack.session.schemas import StartSessionRequest, TeacherOutput, TurnRequest


def _start(**extra: object) -> StartSessionRequest:
    return StartSessionRequest.model_validate(
        {"domain_id": str(uuid4()), "idempotency_key": "k", **extra}
    )


def _turn(**extra: object) -> TurnRequest:
    return TurnRequest.model_validate({"text": "hi", "idempotency_key": "k", **extra})


def _teacher(**extra: object) -> TeacherOutput:
    return TeacherOutput.model_validate(
        {"reply_text": "ok", "pedagogical_move": "explain", **extra}
    )


def test_start_session_plan_defaults_to_none() -> None:
    assert _start().plan is None


def test_start_session_plan_accepts_list_of_dicts() -> None:
    plan = [{"step": 1, "kind": "explain"}, {"step": 2}]
    assert _start(plan=plan).plan == plan


def test_start_session_plan_rejects_non_list() -> None:
    with pytest.raises(ValidationError) as exc:
        _start(plan="not a list")
    assert exc.value.errors()[0]["type"] == "list_type"


def test_turn_response_time_defaults_to_none() -> None:
    assert _turn().response_time_sec is None


def test_turn_response_time_accepts_values() -> None:
    assert _turn(response_time_sec=0).response_time_sec == 0.0
    assert _turn(response_time_sec=12.5).response_time_sec == 12.5


def test_turn_response_time_rejects_negative() -> None:
    with pytest.raises(ValidationError) as exc:
        _turn(response_time_sec=-1)
    assert exc.value.errors()[0]["type"] == "greater_than_equal"


def test_teacher_output_evaluated_fields_default_to_none() -> None:
    out = _teacher()
    assert out.evaluated_is_correct is None
    assert out.evaluated_score is None
    assert out.evaluated_feedback is None


def test_teacher_output_accepts_evaluated_fields() -> None:
    out = _teacher(evaluated_is_correct=True, evaluated_score=0.75, evaluated_feedback="good")
    assert out.evaluated_is_correct is True
    assert out.evaluated_score == 0.75
    assert out.evaluated_feedback == "good"


@pytest.mark.parametrize("score", [0, 1])
def test_teacher_output_score_bounds_inclusive(score: int) -> None:
    assert _teacher(evaluated_score=score).evaluated_score == float(score)


@pytest.mark.parametrize("score", [1.5, -0.1])
def test_teacher_output_score_out_of_range_rejected(score: float) -> None:
    with pytest.raises(ValidationError) as exc:
        _teacher(evaluated_score=score)
    assert exc.value.errors()[0]["type"] in {"greater_than_equal", "less_than_equal"}
