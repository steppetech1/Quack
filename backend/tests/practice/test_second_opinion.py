"""ТЗ Ф4 §8.7 T-CH6 (pure part): model verdicts and the triggers of the second opinion
(Т-§6.8, Т-§7.7, WP11.6). The tier (agreed → Т2, not → Т3) is set by the fold of stream A
(WP4.1) from `secondOpinion`; the job `check.model` needs the dispatcher (S6) and memory
(S4) and lands with them."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from quack.config import Settings
from quack.practice.check import HelpUsed, RubricItemResult, SecondOpinion, Verdict
from quack.practice.model_check import (
    LevelState,
    model_verdict,
    needs_second_opinion,
    with_second_opinion,
)
from quack.practice.role_outputs import ModelVerdictOutput

M_ONE = UUID(int=1)
M_TWO = UUID(int=2)
SETTINGS = Settings()
SURE = (1 + SETTINGS.verdict_min_confidence) / 2
UNSURE = SETTINGS.verdict_min_confidence / 2
QUIET = LevelState(proven_by_direct=False, last_t1_failed=False)
PROVEN = LevelState(proven_by_direct=True, last_t1_failed=False)
FAILED = LevelState(proven_by_direct=False, last_t1_failed=True)


def _output(
    outcome: str = "incorrect", confidence: float = SURE, **extra: Any
) -> ModelVerdictOutput:
    fields: dict[str, Any] = {
        "items": [
            {"item": "R1", "status": "found", "quote": "a = b"},
            {"item": "R2", "status": "missing", "quote": None},
        ],
        "outcome": outcome,
        "confidence": confidence,
        "misconception": None,
        "misconception_hypothesis": None,
    }
    return ModelVerdictOutput.model_validate({**fields, **extra})


def _verdict(outcome: str = "incorrect", confidence: float = SURE) -> Verdict:
    return model_verdict(_output(outcome, confidence), answer_seq=5, part="main")


def test_model_output_becomes_a_model_class_verdict() -> None:
    verdict = model_verdict(
        _output(misconception="M2"),
        answer_seq=5,
        part="main",
        help_used=HelpUsed(hint_step=2, solution_requested=True),
        known_misconceptions=[M_ONE, M_TWO],
    )
    assert verdict == Verdict(
        answer_seq=5,
        part="main",
        outcome="incorrect",
        method="model",
        verification="model",
        confidence=SURE,
        help_step=2,
        solution_shown=True,
        rubric=[
            RubricItemResult(item="R1", status="found", quote="a = b"),
            RubricItemResult(item="R2", status="missing", quote=None),
        ],
        misconception_id=M_TWO,
    )


def test_misconception_hypothesis_in_words_is_kept() -> None:
    verdict = model_verdict(
        _output(misconception_hypothesis="thinks the minus distributes"), answer_seq=5, part="m"
    )
    assert verdict.misconception_id is None
    assert verdict.misconception_hypothesis == "thinks the minus distributes"


def test_unknown_label_is_dropped_not_stored() -> None:
    verdict = model_verdict(
        _output(misconception="M9"), answer_seq=5, part="m", known_misconceptions=[M_ONE]
    )
    assert verdict.misconception_id is None
    assert verdict.misconception_hypothesis is None


@pytest.mark.parametrize(
    ("outcome", "confidence", "levels", "expected"),
    [
        ("incorrect", UNSURE, [QUIET], True),  # low confidence
        ("correct", UNSURE, [QUIET], True),
        ("incorrect", SURE, [QUIET], False),
        ("incorrect", SURE, [QUIET, PROVEN], True),  # negative on a proven level
        ("correct", SURE, [PROVEN], False),
        ("correct", SURE, [FAILED], True),  # positive where the latest Т1 failed
        ("incorrect", SURE, [FAILED], False),
        ("partial", SURE, [PROVEN, FAILED], False),  # partial is neither (Т-§7.7 spike)
        ("correct", SURE, [], False),
    ],
)
def test_second_opinion_triggers(
    outcome: str, confidence: float, levels: list[LevelState], expected: bool
) -> None:
    assert needs_second_opinion(_verdict(outcome, confidence), levels, SETTINGS) is expected


def test_code_verdict_never_gets_a_second_opinion() -> None:
    code = Verdict(
        answer_seq=5,
        part="main",
        outcome="incorrect",
        method="symbolic",
        verification="code",
        confidence=UNSURE,  # a recognized answer (WP11.7)
    )
    assert not needs_second_opinion(code, [PROVEN], SETTINGS)


@pytest.mark.parametrize(("second", "agreed"), [("incorrect", True), ("correct", False)])
def test_second_opinion_result_is_recorded(second: str, agreed: bool) -> None:
    first = _verdict("incorrect", UNSURE)
    result = with_second_opinion(first, _output(second))
    assert result.second_opinion == SecondOpinion(requested=True, agreed=agreed)
    assert result.model_copy(update={"second_opinion": None}) == first
