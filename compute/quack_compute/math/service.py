"""Bodies of `/v1/math/check` and `/v1/math/transition` (ТЗ Ф4 WP11.5); they run in a process of
the compute pool (WP24), which kills a call past `timeout_ms` (Т-§6.1)."""

from __future__ import annotations

from typing import Any

from quack_compute.math.equivalence import Equivalent, Failed, Params, compare_parsed
from quack_compute.math.grammar import ParseRefused, Relation, parse
from quack_compute.math.latex import parse_latex_strict
from quack_compute.math.schemas import (
    MathCheckRequest,
    MathCheckResponse,
    MathInput,
    TransitionRequest,
    TransitionResponse,
    Variable,
)
from quack_compute.math.units import QuantityRefused, compare_quantity


def check_math(request: MathCheckRequest) -> MathCheckResponse:
    if request.kind == "quantity":
        return _quantity(request)
    try:
        student = _parsed(request.student, request.variables)
        reference = _parsed(request.reference, request.variables)
    except ParseRefused as exc:
        return MathCheckResponse(status="failed", reason="unparsed", detail=str(exc))
    result = compare_parsed(
        student,
        reference,
        mode=request.mode,
        form_target=request.form_target,
        params=_params(request.numeric_points, request.simplify_share, request.timeout_ms),
    )
    if isinstance(result, Failed):
        return MathCheckResponse(status="failed", reason=result.reason, detail=result.detail)
    return MathCheckResponse(
        status="completed",
        outcome="correct" if result.equivalent else "incorrect",
        method="numeric" if result.numeric_only else "symbolic",
        numeric_only=result.numeric_only,
        error_hint=result.error_hint,
    )


def check_transition(request: TransitionRequest) -> TransitionResponse:
    """Т-§15.4 п.1: expressions — equivalence; equations — equivalence of solution sets."""
    try:
        previous = _parsed(request.previous, request.variables)
        current = _parsed(request.current, request.variables)
    except ParseRefused as exc:
        return TransitionResponse(status="failed", reason="unparsed", detail=str(exc))
    mode = "solution_set" if isinstance(previous, Relation) and previous.op == "=" else "equal"
    if mode == "equal" and isinstance(previous, Relation):
        mode = "interval"
    result = compare_parsed(
        current,
        previous,
        mode=mode,
        params=_params(request.numeric_points, request.simplify_share, request.timeout_ms),
    )
    if isinstance(result, Failed):
        return TransitionResponse(status="failed", reason=result.reason, detail=result.detail)
    assert isinstance(result, Equivalent)
    return TransitionResponse(
        status="completed", follows=result.equivalent, numeric_only=result.numeric_only
    )


def _quantity(request: MathCheckRequest) -> MathCheckResponse:
    if request.student_text is None or request.unit is None or request.reference.sympy is None:
        return MathCheckResponse(
            status="failed", reason="unparsed", detail="quantity needs the answer and the unit"
        )
    try:
        reference = float(parse(request.reference.sympy).evalf())
        verdict = compare_quantity(request.student_text, reference, request.unit, request.tolerance)
    except (ParseRefused, QuantityRefused, TypeError) as exc:
        return MathCheckResponse(status="failed", reason="unparsed", detail=str(exc))
    return MathCheckResponse(
        status="completed",
        outcome=verdict.outcome,
        method="units",
        error_hint="dimension" if verdict.dimension_mismatch else None,
    )


def _parsed(value: MathInput | None, variables: list[Variable]) -> Any:
    """Т-§22.7: `sympy` first through the grammar; else LaTeX through strict ANTLR."""
    if value is None:
        raise ParseRefused("no input")
    if value.sympy is not None:
        return parse(value.sympy, {v.name: {a: True for a in v.assumptions} for v in variables})
    if value.latex is not None:
        return parse_latex_strict(value.latex)
    raise ParseRefused("neither sympy nor latex")


def _params(points: int, share: float, timeout_ms: int) -> Params:
    return Params(numeric_points=points, simplify_budget_ms=int(timeout_ms * share))
