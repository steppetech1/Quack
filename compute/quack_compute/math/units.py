"""Physical quantities (Т-§6.7, Т-§22.2; ТЗ Ф4 WP11.5): `pint`, SI symbols only (no table of
Russian units on the slice), a decimal comma becomes a point. The answer is converted to the
unit of the spec and compared with the tolerance of Т-§6.4; a different dimension is
`incorrect` with `error_hint = dimension`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import cache
from typing import Literal

import pint

from quack_compute.math.schemas import Tolerance

_LEADING_NUMBER = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")

type Outcome = Literal["correct", "incorrect", "unclear"]


class QuantityRefused(Exception):
    """The answer or the unit is not a quantity pint understands."""


@dataclass(frozen=True)
class QuantityVerdict:
    outcome: Outcome
    dimension_mismatch: bool = False


@cache
def _registry() -> pint.UnitRegistry:
    return pint.UnitRegistry()


def compare_quantity(
    answer: str, reference: float, unit: str, tolerance: Tolerance
) -> QuantityVerdict:
    registry = _registry()
    text = answer.strip().replace(",", ".")
    try:
        given = registry.Quantity(text)
        expected = registry.Quantity(reference, unit)
    except (pint.errors.PintError, ValueError, TypeError, AttributeError) as exc:
        raise QuantityRefused(str(exc)) from exc
    if not isinstance(given, pint.Quantity) or given.dimensionality != expected.dimensionality:
        return QuantityVerdict("incorrect", dimension_mismatch=True)
    value = float(given.to(expected.units).magnitude)
    target = float(expected.magnitude)
    diff = abs(value - target)
    if diff == 0:
        return QuantityVerdict("correct")
    if tolerance.tol_abs is not None:
        return QuantityVerdict("correct" if diff <= tolerance.tol_abs else "incorrect")
    if tolerance.tol_rel is not None:
        return QuantityVerdict(
            "correct" if diff <= tolerance.tol_rel * abs(target) else "incorrect"
        )
    scale = value / float(given.magnitude) if given.magnitude else 1.0
    half_unit, digits = _precision(text, scale)
    if half_unit is None or diff > half_unit:
        return QuantityVerdict("incorrect")
    return QuantityVerdict("unclear" if digits < tolerance.min_sig_digits else "correct")


def _precision(text: str, scale: float) -> tuple[float | None, int]:
    """Half a unit of the last written digit of the magnitude (in the spec's unit) and the
    number of significant digits the learner wrote (Т-§6.4)."""
    match = _LEADING_NUMBER.match(text)
    if match is None:
        return None, 0
    try:
        dec = Decimal(match.group())
    except InvalidOperation:
        return None, 0
    exponent = dec.as_tuple().exponent
    if not isinstance(exponent, int):
        return None, 0
    digits = dec.as_tuple().digits
    significant = len(digits) - next((i for i, d in enumerate(digits) if d), len(digits))
    return 0.5 * 10.0**exponent * abs(scale), significant
