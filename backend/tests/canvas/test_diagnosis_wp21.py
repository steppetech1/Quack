"""WP21.4 п.3: the `canvas_diagnoser@1` output and the diagnosis it gives (ТЗ Ф4 WP21.4, WP21.5;
Т-§15.4; D-C-120, D-C-136)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from quack.canvas.diagnosis import CanvasDiagnoserOutput, diagnosis_record
from quack.config import Settings
from quack.llm.labels import LabelMap, UnknownLabelError

SKILL, MISCONCEPTION = uuid4(), uuid4()


def _labels() -> LabelMap:
    return LabelMap({"skill": [SKILL], "misconception": [MISCONCEPTION]})


def _output(**fields: Any) -> CanvasDiagnoserOutput:
    base: dict[str, Any] = {
        "error": True,
        "gap_type": "misconception",
        "misconception": "M1",
        "misconception_hypothesis": None,
        "root_cause": "S1",
        "confidence": 0.9,
    }
    return CanvasDiagnoserOutput.model_validate(base | fields)


def test_no_error_gives_no_diagnosis(settings: Settings) -> None:
    """D-C-120: a line without an error writes nothing, `gap_type = none` with an error too."""
    no_error = _output(error=False, gap_type="none", misconception=None, root_cause=None)
    assert diagnosis_record(no_error, _labels(), settings) is None
    assert diagnosis_record(_output(gap_type="none"), _labels(), settings) is None


def test_error_and_question_by_confidence(settings: Settings) -> None:
    """WP21.5: below `mark_min_confidence` the diagnosis is a question; labels become ids."""
    found = diagnosis_record(_output(), _labels(), settings)
    assert found is not None
    record, confidence = found
    assert record.kind == "error" and confidence == 0.9
    assert record.misconception_id == MISCONCEPTION and record.root_cause_skill_id == SKILL
    unsure = _output(confidence=settings.mark_min_confidence - 0.01)
    assert diagnosis_record(unsure, _labels(), settings)[0].kind == "question"  # type: ignore[index]


def test_hypothesis_only_without_a_known_misconception(settings: Settings) -> None:
    both = _output(misconception_hypothesis="adds exponents when multiplying bases")
    assert diagnosis_record(both, _labels(), settings)[0].misconception_hypothesis is None  # type: ignore[index]
    free = _output(misconception=None, misconception_hypothesis="adds exponents")
    record = diagnosis_record(free, _labels(), settings)[0]  # type: ignore[index]
    assert record.misconception_id is None and record.misconception_hypothesis == "adds exponents"
    slip = _output(gap_type="slip", misconception=None, root_cause=None)
    assert diagnosis_record(slip, _labels(), settings)[0].gap_type == "slip"  # type: ignore[index]


def test_unknown_label_is_a_labelling_error(settings: Settings) -> None:
    with pytest.raises(UnknownLabelError):
        diagnosis_record(_output(misconception="M7"), _labels(), settings)
