"""Contracts of `POST /v1/math/check` and `POST /v1/math/transition` (ТЗ Ф4 WP11.5, seam S10).

Every expression comes as `{latex?, sympy?}` (Т-§22.7): `sympy` is parsed first by the
whitelist grammar; without it `latex` goes through ANTLR in strict mode (Т-§6.5). The budget
of the whole call is `timeout_ms` (the API sends `symbolic_inline_timeout_ms`, the insurance
job `symbolic_background_timeout_ms`, Т-§6.1); every threshold of the check comes from the
caller's `Settings` (ТЗ Ф4 §0.3, D-B-124), compute has no defaults of its own.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

type Mode = Literal["equal", "up_to_constant", "solution_set", "interval", "form"]
type Assumption = Literal["real", "positive", "nonnegative", "integer", "nonzero"]

_Text = Annotated[str, Field(max_length=2000)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MathInput(_Model):
    latex: _Text | None = None
    sympy: _Text | None = None


class Variable(_Model):
    name: str
    assumptions: list[Assumption] = []


class Tolerance(_Model):
    """Т-§6.4 for quantities: the spec's tolerance, else the learner's own precision with at
    least `min_sig_digits` significant digits."""

    tol_abs: float | None = None
    tol_rel: float | None = None
    min_sig_digits: int = Field(ge=1)


class NumericParams(_Model):
    """Т-§6.6 `numeric_points`, the share of `timeout_ms` for `simplify` and the numbers of the
    numeric check (`Settings.math_*`, D-B-124)."""

    numeric_points: int = Field(ge=1)
    simplify_share: float = Field(gt=0, le=1)
    precision_digits: int = Field(ge=1)
    sample_low: float
    sample_high: float
    positive_sample_min: float = Field(gt=0)
    max_attempts_per_point: int = Field(ge=1)
    rel_tol: float = Field(ge=0)
    abs_tol: float = Field(ge=0)


class MathCheckRequest(_Model):
    """`expression`: `student`, `reference`, `mode`. `quantity`: `student_text` is the raw
    answer with its unit (`9,8 m/s^2`), `reference` the magnitude and `unit` the unit."""

    kind: Literal["expression", "quantity"] = "expression"
    student: MathInput | None = None
    student_text: _Text | None = None
    reference: MathInput
    mode: Mode = "equal"
    form_target: Literal["factored", "simplified"] | None = None
    variables: list[Variable] = []
    unit: str | None = None
    tolerance: Tolerance
    numeric: NumericParams
    timeout_ms: int = Field(ge=1)


class MathCheckResponse(_Model):
    """`completed` with `outcome`; `failed` with `reason` → `check.failed` (Т-§22.7)."""

    status: Literal["completed", "failed"]
    outcome: Literal["correct", "incorrect", "unclear"] | None = None
    method: Literal["symbolic", "numeric", "units"] | None = None
    numeric_only: bool = False
    error_hint: Literal["sign", "coefficient", "dimension"] | None = None
    reason: Literal["unparsed", "too_few_points", "unsupported"] | None = None
    detail: str | None = None


class TransitionRequest(_Model):
    """Т-§15.4 п.1: does `current` follow symbolically from `previous` (canvas lines)."""

    previous: MathInput
    current: MathInput
    variables: list[Variable] = []
    numeric: NumericParams
    timeout_ms: int = Field(ge=1)


class TransitionResponse(_Model):
    status: Literal["completed", "failed"]
    follows: bool | None = None
    numeric_only: bool = False
    reason: Literal["unparsed", "too_few_points", "unsupported"] | None = None
    detail: str | None = None
