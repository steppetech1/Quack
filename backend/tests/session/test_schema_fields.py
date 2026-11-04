"""New optional fields of the LLM output schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from quack.session.schemas import TeacherOutput


def _teacher(**extra: object) -> TeacherOutput:
    return TeacherOutput.model_validate(
        {"reply_text": "ok", "pedagogical_move": "explain", **extra}
    )


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
