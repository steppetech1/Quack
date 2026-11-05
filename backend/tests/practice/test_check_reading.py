"""ТЗ Ф4 §8.7 T-CH8: a recognized answer (handwriting, photo) is checked the same way; the
recognition confidence lowers the verdict confidence; unsure recognition + a negative verdict
→ `needs_reading_confirmation` (Т-§6.9, Т-§16.3, WP11.7; D-B-127)."""

from __future__ import annotations

import pytest

from quack.compute_client.math_contracts import MathCheckResponse
from quack.config import Settings
from quack.practice.answer_spec import AnswerSpec, LearnerAnswer
from quack.practice.check import Verdict, check
from tests.support.compute import ComputeScenario, scripted_compute

SETTINGS = Settings()
UNSURE = SETTINGS.recognition_min_confidence / 2
SURE = (1 + SETTINGS.recognition_min_confidence) / 2

HALF = AnswerSpec.model_validate({"type": "number", "value": {"latex": "0.5", "sympy": "0.5"}})


async def _instant(value: str, recognition: float | None) -> Verdict:
    answer = LearnerAnswer(kind="number", value=value)
    result = await check(
        answer,
        HALF,
        answer_seq=1,
        part="main",
        settings=SETTINGS,
        recognition_confidence=recognition,
    )
    assert isinstance(result, Verdict)
    return result


async def test_unsure_reading_of_a_wrong_answer_asks_to_confirm_the_reading() -> None:
    verdict = await _instant("0.7", UNSURE)
    assert verdict.outcome == "incorrect"
    assert verdict.needs_reading_confirmation
    assert verdict.confidence == pytest.approx(UNSURE)


async def test_unsure_reading_of_a_right_answer_only_lowers_confidence() -> None:
    verdict = await _instant("0.5", UNSURE)
    assert verdict.outcome == "correct"
    assert not verdict.needs_reading_confirmation
    assert verdict.confidence == pytest.approx(UNSURE)


async def test_sure_reading_of_a_wrong_answer_needs_no_confirmation() -> None:
    verdict = await _instant("0.7", SURE)
    assert not verdict.needs_reading_confirmation
    assert verdict.confidence == pytest.approx(SURE)


async def test_typed_answer_is_unchanged() -> None:
    verdict = await _instant("0.7", None)
    assert (verdict.confidence, verdict.needs_reading_confirmation) == (None, False)


async def test_fast_check_applies_the_reading_too() -> None:
    spec = AnswerSpec.model_validate(
        {"type": "expression", "value": {"latex": "2x", "sympy": "2*x"}}
    )
    scenario = ComputeScenario()
    scenario.push_for(
        "/v1/math/check",
        MathCheckResponse(status="completed", outcome="incorrect", method="symbolic"),
    )
    client = scripted_compute(scenario)
    try:
        result = await check(
            LearnerAnswer(kind="expression", value="3x", latex="3x"),
            spec,
            answer_seq=1,
            part="main",
            settings=SETTINGS,
            compute=client,
            timeout_ms=SETTINGS.symbolic_inline_timeout_ms,
            recognition_confidence=UNSURE,
        )
    finally:
        await client.aclose()
    assert isinstance(result, Verdict)
    assert result.needs_reading_confirmation
    assert result.confidence == pytest.approx(UNSURE)
