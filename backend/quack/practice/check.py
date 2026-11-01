"""One entry point of answer checking `check(answer, spec) → Verdict` (Т-§6.1; ТЗ Ф4 WP11.1–11.4,
seam S7). Used by the session (B), the canvas and the cards (C).

The path is chosen by the answer type of the spec (Т-§6.1): instant types are checked in the
process, fast types in compute (`/v1/math/check`) within `timeout_ms`, slow types are not
checked here — the result says so and the caller schedules `check.model` (WP11.6). A fast
check that did not parse or did not collect points is `CheckFailed` (Т-§22.7), not
`unclear`; `unclear` is only too low a precision of a number (Т-§6.4).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.practice.answer_spec import (
    INSTANT_TYPES,
    SLOW_TYPES,
    AnswerSpec,
    LearnerAnswer,
)

type Outcome = Literal["correct", "incorrect", "partial", "unclear"]
type Method = Literal["exact", "numeric", "symbolic", "units", "model"]
type Verification = Literal["code", "model"]
# Т-§6.2 ориентир: cheap deterministic checks of a wrong answer.
type ErrorHint = Literal["sign", "coefficient", "dimension"]
type CheckFailedReason = Literal["unparsed", "too_few_points", "timeout", "compute_unavailable"]


class SecondOpinion(BaseModel):
    """Т-§7.7: whether a second opinion was asked and whether it agreed (null — not asked)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: bool
    agreed: bool | None = None


class RubricItemResult(BaseModel):
    """One rubric item of a model verdict (Т-§6.8): found / missing / wrong, with a quote."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item: str
    status: Literal["found", "missing", "wrong"]
    quote: str | None = None


class Verdict(BaseModel):
    """`verdict.recorded` payload without the event envelope (Т-§6.2, ТЗ Ф4 WP11.4).

    `help_step` / `solution_shown` — help computed by code from the task events of the
    session (WP11.2); `misconception` — a label or a hypothesis in words (model verdicts).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer_seq: int
    part: str
    outcome: Outcome
    method: Method
    verification: Verification
    numeric_only: bool = False
    error_hint: ErrorHint | None = None
    second_opinion: SecondOpinion | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    help_step: int = Field(default=0, ge=0, le=3)
    solution_shown: bool = False
    rubric: list[RubricItemResult] | None = None
    misconception: str | None = None
    needs_reading_confirmation: bool = False


@dataclass(frozen=True)
class CheckFailed:
    """`check.failed {answer_seq, part, reason}` (Т-§22.7): goes to `check.model`."""

    answer_seq: int
    part: str
    reason: CheckFailedReason


@dataclass(frozen=True)
class NeedsModel:
    """A slow type (Т-§6.1): no code verdict; the caller schedules `check.model`."""

    answer_seq: int
    part: str


@dataclass(frozen=True)
class NotYet:
    """A fast check that did not finish within `timeout_ms`: nothing is written, the
    insurance job `check.symbolic` checks later (Т-§6.1)."""

    answer_seq: int
    part: str


type CheckResult = Verdict | CheckFailed | NeedsModel | NotYet


@dataclass(frozen=True)
class HelpUsed:
    """Help on the task in the session (WP11.2): highest hint step shown, solution asked."""

    hint_step: int = 0
    solution_requested: bool = False


async def check(
    answer: LearnerAnswer,
    spec: AnswerSpec,
    *,
    answer_seq: int,
    part: str,
    settings: Settings,
    compute: ComputeClient | None = None,
    timeout_ms: int | None = None,
    help_used: HelpUsed | None = None,
    recognition_confidence: float | None = None,
) -> CheckResult:
    """Т-§6.1: check one part of an answer.

    `timeout_ms` — budget of a fast check: `symbolic_inline_timeout_ms` in the API,
    `symbolic_background_timeout_ms` in the insurance job. `recognition_confidence` — of a
    handwritten or photographed answer: lowers the verdict confidence and, with a negative
    verdict, sets `needs_reading_confirmation` (Т-§6.9, WP11.7).
    """
    if spec.type in SLOW_TYPES:
        return NeedsModel(answer_seq, part)
    if spec.type in INSTANT_TYPES:
        outcome, method = _instant(answer, spec, settings)
        if outcome is not None:
            help_used = help_used or HelpUsed()
            return Verdict(
                answer_seq=answer_seq,
                part=part,
                outcome=outcome,
                method=method,
                verification="code",
                help_step=help_used.hint_step,
                solution_shown=help_used.solution_requested,
            )
    # Fast types and numbers written as expressions go to compute (WP11.3, WP11.5). Without a
    # compute call nothing is decided now: the insurance job checks later (Т-§6.1).
    if compute is None:
        return NotYet(answer_seq, part)
    raise NotImplementedError


# --- Instant checks (Т-§6.4, WP11.2) ---

_RATIONAL = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?(/[+-]?\d+)?$")
_ENCLOSING = "()[]{}"


def _instant(
    answer: LearnerAnswer, spec: AnswerSpec, settings: Settings
) -> tuple[Outcome | None, Method]:
    """Outcome of an instant type, or `None` when it is not instant after all (a number
    written as an expression, `√2/2`, Т-§6.1)."""
    match spec.type:
        case "choice":
            correct = {o.option_id for o in spec.options or [] if o.correct}
            return ("correct" if answer.value.strip() in correct else "incorrect"), "exact"
        case "short_text":
            accepted = {_normalize_text(a) for a in spec.accepted or []}
            ok = _normalize_text(answer.value) in accepted
            return ("correct" if ok else "incorrect"), "exact"
        case "number":
            if spec.value is None:
                return None, "numeric"
            return _number(answer.value, spec.value.sympy, spec, settings), "numeric"
        case "set" | "vector":
            return _sequence(answer.value, spec, settings), "numeric"
        case _:
            return None, "numeric"


def _normalize_text(value: str) -> str:
    """Т-§6.4: case, spaces, `ё → е`."""
    return " ".join(value.casefold().replace("ё", "е").split())


@dataclass(frozen=True)
class _Written:
    """A number as the learner wrote it: exact value; for a decimal also half a unit of the
    last written digit and the count of significant digits (Т-§6.4)."""

    value: Fraction
    half_unit: Fraction | None
    sig_digits: int | None


def _parse_number(text: str) -> _Written | None:
    """Decimal comma or point, `3/4`, scientific notation (Т-§6.4); `None` — not a plain
    number (an expression, or not a number at all)."""
    text = text.strip().replace(" ", "").replace(",", ".")
    if not _RATIONAL.match(text):
        return None
    if "/" in text:
        num, den = text.split("/")
        if Fraction(Decimal(den)) == 0:
            return None
        return _Written(Fraction(Decimal(num)) / Fraction(Decimal(den)), None, None)
    dec = Decimal(text)
    exponent = dec.as_tuple().exponent
    assert isinstance(exponent, int)  # a finite decimal: the regex allows nothing else
    digits = dec.as_tuple().digits
    significant = len(digits) - next((i for i, d in enumerate(digits) if d), len(digits))
    return _Written(Fraction(dec), Fraction(1, 2) * Fraction(10) ** exponent, significant)


def _number(value: str, reference: str, spec: AnswerSpec, settings: Settings) -> Outcome | None:
    written = _parse_number(value)
    expected = _parse_number(reference)
    if written is None or expected is None:
        return None
    diff = abs(written.value - expected.value)
    if diff == 0:
        return "correct"
    tolerance = _spec_tolerance(spec, expected.value)
    if tolerance is not None:
        return "correct" if diff <= tolerance else "incorrect"
    if written.half_unit is None:
        return "incorrect"  # an exact fraction that is not equal
    if diff > written.half_unit:
        return "incorrect"
    if (written.sig_digits or 0) < settings.min_sig_digits:
        return "unclear"
    return "correct"


def _spec_tolerance(spec: AnswerSpec, expected: Fraction) -> Fraction | None:
    """The tolerance of the spec (Т-§6.4): absolute, relative or a number of significant
    digits (half a unit of the last one); `None` — the spec gives none."""
    if spec.tol_abs is not None:
        return Fraction(spec.tol_abs)
    if spec.tol_rel is not None:
        return Fraction(spec.tol_rel) * abs(expected)
    if spec.sig_digits is not None and expected != 0:
        magnitude = math.floor(math.log10(abs(expected)))
        return Fraction(1, 2) * Fraction(10) ** (magnitude - spec.sig_digits + 1)
    return None


def _sequence(value: str, spec: AnswerSpec, settings: Settings) -> Outcome | None:
    """Т-§6.4: `set` — regardless of order, `vector` — element by element with tolerance."""
    references = [item.sympy for item in spec.items or []]
    if any(_parse_number(r) is None for r in references):
        return None
    elements = [e for e in value.strip().strip(_ENCLOSING).split(",") if e.strip()]
    if any(_parse_number(e) is None for e in elements):
        return None
    if len(elements) != len(references):
        return "incorrect"
    if spec.type == "vector":
        outcomes = [
            _number(e, r, spec, settings) for e, r in zip(elements, references, strict=True)
        ]
        return _combine(outcomes)
    remaining = list(references)
    outcomes: list[Outcome | None] = []
    for element in elements:
        matched: tuple[Outcome, str] | None = None
        for reference in remaining:
            outcome = _number(element, reference, spec, settings)
            if outcome == "correct" or (outcome == "unclear" and matched is None):
                matched = (outcome, reference)
                if outcome == "correct":
                    break
        if matched is None:
            return "incorrect"
        remaining.remove(matched[1])
        outcomes.append(matched[0])
    return _combine(outcomes)


def _combine(outcomes: list[Outcome | None]) -> Outcome:
    if any(o in (None, "incorrect") for o in outcomes):
        return "incorrect"
    if "unclear" in outcomes:
        return "unclear"
    return "correct"
