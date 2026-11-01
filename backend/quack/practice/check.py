"""One entry point of answer checking `check(answer, spec) → Verdict` (Т-§6.1; ТЗ Ф4 WP11.1–11.4,
seam S7). Used by the session (B), the canvas and the cards (C).

The path is chosen by the answer type of the spec (Т-§6.1): instant types are checked in the
process, fast types in compute (`/v1/math/check`) within `timeout_ms`, slow types are not
checked here — the result says so and the caller schedules `check.model` (WP11.6). A fast
check that did not parse or did not collect points is `CheckFailed` (Т-§22.7), not
`unclear`; `unclear` is only too low a precision of a number (Т-§6.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.practice.answer_spec import AnswerSpec, LearnerAnswer

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
    raise NotImplementedError
