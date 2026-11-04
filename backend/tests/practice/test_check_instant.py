"""ТЗ Ф4 §8.7 T-CH1 (pure part): instant checks of `check()` (Т-§6.4, WP11.2).

The «same transaction as `answer.submitted`» half of T-CH1 needs `Db` (seam S2) and lands
with WP13.1.
"""

from __future__ import annotations

from typing import Any

import pytest

from quack.config import Settings
from quack.practice.answer_spec import AnswerSpec, LearnerAnswer
from quack.practice.check import Verdict, check

SETTINGS = Settings()


async def _outcome(spec: dict[str, Any], value: str) -> str:
    answer_spec = AnswerSpec.model_validate(spec)
    answer = LearnerAnswer(kind=answer_spec.type, value=value)
    result = await check(answer, answer_spec, answer_seq=7, part="main", settings=SETTINGS)
    assert isinstance(result, Verdict)
    assert (result.answer_seq, result.part, result.verification) == (7, "main", "code")
    return result.outcome


THIRD = {"type": "number", "value": {"latex": "\\frac{1}{3}", "sympy": "1/3"}}


@pytest.mark.parametrize(
    ("value", "outcome"),
    [
        ("0.3", "unclear"),
        ("0.333", "correct"),
        ("0,333", "correct"),
        ("0.35", "incorrect"),
        ("1/3", "correct"),
        ("3.33e-1", "correct"),
        ("0.3333333", "correct"),
    ],
)
async def test_number_tolerance_by_learner_precision(value: str, outcome: str) -> None:
    """T-CH1: no tolerance in the spec → half a unit of the last written digit, at least
    `min_sig_digits` significant digits; less precision — `unclear` (Т-§6.4)."""
    assert await _outcome(THIRD, value) == outcome


async def test_number_tolerance_from_spec() -> None:
    spec = {**THIRD, "tol_abs": 0.05}
    assert await _outcome(spec, "0.3") == "correct"
    assert await _outcome(spec, "0.4") == "incorrect"
    spec = {"type": "number", "value": {"latex": "100", "sympy": "100"}, "tol_rel": 0.01}
    assert await _outcome(spec, "100.9") == "correct"
    assert await _outcome(spec, "102") == "incorrect"


async def test_choice_by_option_id() -> None:
    spec = {
        "type": "choice",
        "options": [
            {"option_id": "A", "text": "x", "correct": False},
            {"option_id": "B", "text": "y", "correct": True},
        ],
    }
    assert await _outcome(spec, "B") == "correct"
    assert await _outcome(spec, "A") == "incorrect"


async def test_short_text_normalized() -> None:
    """Т-§6.4: case, spaces, `ё → е`, compared with the list."""
    spec = {"type": "short_text", "accepted": ["ёлка", "Absolute  convergence"]}
    assert await _outcome(spec, "  ЕЛКА ") == "correct"
    assert await _outcome(spec, "absolute convergence") == "correct"
    assert await _outcome(spec, "conditional") == "incorrect"


async def test_set_unordered_vector_elementwise() -> None:
    items = [{"latex": "1", "sympy": "1"}, {"latex": "2", "sympy": "2"}]
    assert await _outcome({"type": "set", "items": items}, "2, 1") == "correct"
    assert await _outcome({"type": "set", "items": items}, "1, 3") == "incorrect"
    assert await _outcome({"type": "vector", "items": items}, "(1, 2)") == "correct"
    assert await _outcome({"type": "vector", "items": items}, "(2, 1)") == "incorrect"
