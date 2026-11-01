"""ТЗ Ф4 §8.7 T-CH4, T-CH7: equivalence modes and cheap error hints (Т-§6.2, Т-§6.6)."""

from __future__ import annotations

import pytest

from quack_compute.math.equivalence import (
    Equivalent,
    Failed,
    FormTarget,
    Mode,
    Params,
    compare,
)

PARAMS = Params(numeric_points=20, simplify_budget_ms=2000)


def _eq(
    student: str, reference: str, mode: Mode = "equal", form_target: FormTarget | None = None
) -> Equivalent:
    result = compare(student, reference, mode=mode, form_target=form_target, params=PARAMS)
    assert isinstance(result, Equivalent), result
    return result


@pytest.mark.parametrize(
    ("student", "reference", "expected"),
    [
        ("(x+1)**2", "x**2 + 2*x + 1", True),
        ("sin(x)**2 + cos(x)**2", "1", True),
        ("sqrt(2)/2", "1/sqrt(2)", True),
        ("x**2 + 1", "x**2 - 1", False),
        # Т-§6.6: points where a side is undefined (x < 0 for log) are skipped.
        ("log(x**2)", "2*log(x)", True),
        ("x/(x+1)", "1 - 1/(x-1)", False),
    ],
)
def test_equal(student: str, reference: str, expected: bool) -> None:
    assert _eq(student, reference).equivalent is expected


def test_up_to_constant() -> None:
    assert _eq("x**3/3 + 5", "x**3/3", mode="up_to_constant").equivalent
    assert not _eq("x**3", "x**3/3", mode="up_to_constant").equivalent


def test_solution_set() -> None:
    assert _eq("2, -2", "x**2 = 4", mode="solution_set").equivalent
    assert _eq("-2, 2", "x**2 - 4", mode="solution_set").equivalent
    assert not _eq("2", "x**2 = 4", mode="solution_set").equivalent
    assert _eq("x**2 - 4 = 0", "(x-2)*(x+2) = 0", mode="solution_set").equivalent


def test_interval() -> None:
    assert _eq("2*x > 2", "x > 1", mode="interval").equivalent
    assert not _eq("x >= 1", "x > 1", mode="interval").equivalent


def test_form_factored() -> None:
    assert _eq("(x-1)*(x+1)", "x**2 - 1", mode="form", form_target="factored").equivalent
    assert not _eq("x**2 - 1", "x**2 - 1", mode="form", form_target="factored").equivalent
    unsupported = compare(
        "x**2 - 1", "x**2 - 1", mode="form", form_target="simplified", params=PARAMS
    )
    assert isinstance(unsupported, Failed) and unsupported.reason == "unsupported"


def test_numeric_only_when_simplify_out_of_budget() -> None:
    """Т-§6.6: numerically equal, `simplify` did not finish — equivalent, `numeric_only`."""
    no_time = Params(numeric_points=20, simplify_budget_ms=0)
    result = compare("(x+1)**2", "x**2 + 2*x + 1", params=no_time)
    assert isinstance(result, Equivalent)
    assert result.equivalent and result.numeric_only


def test_failures_go_to_model() -> None:
    """Т-§22.7: not parsed or too few points — a failure (`check.failed`), not `unclear`."""
    unparsed = compare("x.__class__", "x", params=PARAMS)
    assert isinstance(unparsed, Failed) and unparsed.reason == "unparsed"
    nowhere = compare("sqrt(-1 - x**2)", "sqrt(-2 - x**2)", params=PARAMS)
    assert isinstance(nowhere, Failed) and nowhere.reason == "too_few_points"


def test_same_input_same_verdict() -> None:
    first = compare("exp(x)*sin(x)", "sin(x)*exp(x)", params=PARAMS)
    assert first == compare("exp(x)*sin(x)", "sin(x)*exp(x)", params=PARAMS)


@pytest.mark.parametrize(
    ("student", "reference", "hint"),
    [
        ("-x**2 - 1", "x**2 + 1", "sign"),
        ("2*sin(x)", "sin(x)", "coefficient"),
        ("x/2", "x", "coefficient"),
        ("x + 3", "x**2", None),
    ],
)
def test_error_hint_cheap_checks(student: str, reference: str, hint: str | None) -> None:
    """T-CH7 (sign, coefficient; dimension — with `pint`, WP11.5 units)."""
    result = _eq(student, reference)
    assert not result.equivalent
    assert result.error_hint == hint
