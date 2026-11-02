"""ТЗ Ф4 §8.7 T-CH4 (quantities, LaTeX), T-CH7 (dimension), Т-§15.4 transition; the endpoints
`/v1/math/check`, `/v1/math/transition` through the compute pool (WP11.5, WP24)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from quack_compute.app import create_app
from quack_compute.math.schemas import MathCheckRequest, Tolerance, TransitionRequest
from quack_compute.math.service import check_math, check_transition
from quack_compute.settings import ComputeSettings

NUMERIC: dict[str, Any] = {
    "numeric_points": 20,
    "simplify_share": 0.8,
    "precision_digits": 30,
    "sample_low": -10.0,
    "sample_high": 10.0,
    "positive_sample_min": 1e-3,
    "max_attempts_per_point": 10,
    "rel_tol": 1e-9,
    "abs_tol": 1e-12,
}
BASE: dict[str, Any] = {"numeric": NUMERIC, "timeout_ms": 5000}
CHECK_BASE: dict[str, Any] = {**BASE, "tolerance": {"min_sig_digits": 3}}


def _check(**fields: Any) -> Any:
    return check_math(MathCheckRequest.model_validate({**CHECK_BASE, **fields}))


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        ("9.81 m/s**2", "correct"),
        ("9,81 m/s^2", "correct"),
        ("981 cm/s^2", "correct"),
        ("9.9 m/s^2", "incorrect"),
        ("9.8 m/s^2", "unclear"),  # two significant digits, within half a unit
    ],
)
def test_quantity_si_with_learner_precision(answer: str, outcome: str) -> None:
    """Т-§6.7: pint, SI, decimal comma; tolerance by Т-§6.4."""
    result = _check(
        kind="quantity", student_text=answer, reference={"sympy": "9.81"}, unit="m/s**2"
    )
    assert (result.status, result.outcome, result.method) == ("completed", outcome, "units")


def test_quantity_dimension_mismatch() -> None:
    """T-CH7: a different dimension — `incorrect` with `error_hint = dimension`."""
    result = _check(
        kind="quantity", student_text="9.81 m/s", reference={"sympy": "9.81"}, unit="m/s**2"
    )
    assert (result.outcome, result.error_hint) == ("incorrect", "dimension")


def test_quantity_spec_tolerance() -> None:
    result = _check(
        kind="quantity",
        student_text="10 N",
        reference={"sympy": "9.81"},
        unit="N",
        tolerance=Tolerance(tol_abs=0.5, min_sig_digits=3),
    )
    assert result.outcome == "correct"


def test_latex_strict_when_no_sympy() -> None:
    """Т-§6.5, Т-§22.7: no `sympy` — LaTeX through ANTLR strict; broken LaTeX — `check.failed`."""
    ok = _check(student={"latex": "\\frac{x^{2}}{2}"}, reference={"sympy": "x**2/2"})
    assert (ok.status, ok.outcome) == ("completed", "correct")
    broken = _check(student={"latex": "\\frac{x}{"}, reference={"sympy": "x"})
    assert (broken.status, broken.reason) == ("failed", "unparsed")


def test_sympy_wins_over_latex() -> None:
    result = _check(student={"latex": "\\frac{x}{", "sympy": "x/2"}, reference={"sympy": "x/2"})
    assert result.outcome == "correct"


def test_transition() -> None:
    """Т-§15.4 п.1: a line follows from the previous one — equivalent expression, or for
    equations the same solution set."""

    def follows(previous: str, current: str) -> bool | None:
        request = TransitionRequest.model_validate(
            {**BASE, "previous": {"sympy": previous}, "current": {"sympy": current}}
        )
        return check_transition(request).follows

    assert follows("(x+1)**2", "x**2 + 2*x + 1")
    assert not follows("(x+1)**2", "x**2 + 1")
    assert follows("2*x + 4 = 10", "x = 3")
    assert not follows("2*x + 4 = 10", "x = 7")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(ComputeSettings())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://compute") as http:
            yield http


async def test_endpoints_through_pool(client: httpx.AsyncClient) -> None:
    check = await client.post(
        "/v1/math/check",
        json={
            **CHECK_BASE,
            "student": {"sympy": "sin(x)**2 + cos(x)**2"},
            "reference": {"sympy": "1"},
        },
    )
    assert check.status_code == 200 and check.json()["outcome"] == "correct"
    transition = await client.post(
        "/v1/math/transition",
        json={**BASE, "previous": {"sympy": "x**2 = 4"}, "current": {"sympy": "x = 2"}},
    )
    assert transition.status_code == 200 and transition.json()["follows"] is False


@pytest.mark.parametrize("missing", ["numeric", "tolerance"])
def test_check_thresholds_come_from_the_caller(missing: str) -> None:
    """ТЗ Ф4 §0.3, D-B-124: no threshold defaults in compute — the API sends its `Settings`."""
    body = {k: v for k, v in CHECK_BASE.items() if k != missing}
    with pytest.raises(ValidationError):
        MathCheckRequest.model_validate({**body, "reference": {"sympy": "1"}})


@pytest.mark.parametrize("field", ["simplify_share", "rel_tol"])
def test_numeric_fields_are_required(field: str) -> None:
    numeric = {k: v for k, v in NUMERIC.items() if k != field}
    with pytest.raises(ValidationError):
        TransitionRequest.model_validate(
            {**BASE, "numeric": numeric, "previous": {"sympy": "x"}, "current": {"sympy": "x"}}
        )
