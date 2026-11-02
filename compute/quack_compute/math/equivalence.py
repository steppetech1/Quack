"""Symbolic equivalence by the modes of the spec (Т-§6.6; ТЗ Ф4 WP11.5).

1. Numeric check at `numeric_points` random points, high precision; points where either side
   is undefined are skipped. An explicit mismatch is final (not equivalent). Too few defined
   points — a failure (`too_few_points`), which becomes `check.failed` (Т-§22.7).
2. If numerically equal everywhere — `simplify(a − b) == 0` within its own budget; out of
   budget — equivalent with `numeric_only` (Т-§6.6).

Modes: `equal`, `up_to_constant` (the derivative of the difference is zero),
`solution_set` (solution sets; if a set is not found symbolically — substitution of the
learner's roots and the count), `interval` (inequalities as sets), `form` (structural check
for `factored`; no structural check for the target — a failure, the model decides).
The random points are seeded by the input: the same input always gets the same verdict.
"""

from __future__ import annotations

import hashlib
import math
import random
import signal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import FrameType
from typing import Any, Literal

import sympy

from quack_compute.math.grammar import ParseRefused, Relation, parse

type Mode = Literal["equal", "up_to_constant", "solution_set", "interval", "form"]
type FormTarget = Literal["factored", "simplified"]
type FailReason = Literal["unparsed", "too_few_points", "unsupported"]
type ErrorHint = Literal["sign", "coefficient"]


@dataclass(frozen=True)
class Params:
    """Numbers of the check from the caller's `Settings` (ТЗ Ф4 §0.3): `numeric_points` and
    the budget of one `simplify` call (ms) of Т-§6.6; precision of evaluation, the sampling
    range, attempts per point and the comparison tolerance (D-B-124)."""

    numeric_points: int
    simplify_budget_ms: int
    precision_digits: int
    sample_low: float
    sample_high: float
    positive_sample_min: float
    max_attempts_per_point: int
    rel_tol: float
    abs_tol: float


@dataclass(frozen=True)
class Equivalent:
    equivalent: bool
    numeric_only: bool = False
    error_hint: ErrorHint | None = None


@dataclass(frozen=True)
class Failed:
    reason: FailReason
    detail: str


type Result = Equivalent | Failed


class _TooFewPoints(Exception):
    pass


class _Unsupported(Exception):
    pass


class _Budget(Exception):
    pass


def compare(
    student: str,
    reference: str,
    *,
    mode: Mode = "equal",
    variables: Mapping[str, Mapping[str, bool]] | None = None,
    form_target: FormTarget | None = None,
    params: Params,
) -> Result:
    """Compare the learner's string with the reference (both SymPy syntax, Т-§22.7)."""
    try:
        a, b = parse(student, variables), parse(reference, variables)
    except ParseRefused as exc:
        return Failed("unparsed", str(exc))
    rng = random.Random(_seed(mode, student, reference))
    try:
        match mode:
            case "equal":
                return _equal(_expr(a), _expr(b), rng, params, hint=True)
            case "up_to_constant":
                return _up_to_constant(_expr(a), _expr(b), rng, params)
            case "solution_set":
                return _solution_sets(a, b, rng, params)
            case "interval":
                return _intervals(a, b, params)
            case "form":
                return _form(_expr(a), _expr(b), form_target, rng, params)
    except _TooFewPoints as exc:
        return Failed("too_few_points", str(exc))
    except _Unsupported as exc:
        return Failed("unsupported", str(exc))


# --- modes ---


def _equal(
    a: sympy.Expr, b: sympy.Expr, rng: random.Random, params: Params, *, hint: bool
) -> Equivalent:
    symbols = _symbols(a, b)
    if not _numeric_equal(a, b, symbols, rng, params):
        return Equivalent(False, error_hint=_hint(a, b, symbols, rng, params) if hint else None)
    confirmed = _simplify_within(a - b, params.simplify_budget_ms)
    return Equivalent(True, numeric_only=confirmed is None or confirmed != 0)


def _up_to_constant(a: sympy.Expr, b: sympy.Expr, rng: random.Random, params: Params) -> Equivalent:
    symbols = _symbols(a, b)
    if not symbols:
        raise _Unsupported("up_to_constant needs a variable")
    variable = symbols[0]
    return _equal(sympy.diff(a, variable), sympy.diff(b, variable), rng, params, hint=False)


def _solution_sets(a: Any, b: Any, rng: random.Random, params: Params) -> Result:
    """Т-§6.6 `solution_set`: the reference is an equation (or `expr` = 0); the learner gives an
    equation or a comma list of roots."""
    reference = _as_equation(b)
    symbols = _symbols(reference)
    if len(symbols) != 1:
        raise _Unsupported("solution_set needs exactly one variable")
    variable = symbols[0]
    expected = sympy.solveset(reference, variable, sympy.S.Reals)
    if isinstance(a, Relation) or (isinstance(a, sympy.Expr) and a.free_symbols):
        given = sympy.solveset(_as_equation(a), variable, sympy.S.Reals)
    else:
        given = sympy.FiniteSet(*(a if isinstance(a, tuple) else (a,)))
    if isinstance(expected, sympy.FiniteSet) and isinstance(given, sympy.FiniteSet):
        return Equivalent(_same_roots(list(given), list(expected), params))
    if isinstance(given, sympy.FiniteSet):
        # The reference set is not found symbolically: substitute the learner's roots and
        # compare their count with the numeric roots of the reference (Т-§6.6).
        roots = [r for r in given if _is_root(reference, variable, r, params)]
        if len(roots) != len(given):
            return Equivalent(False)
        found = _numeric_roots(reference, variable, rng, params)
        return Equivalent(len(found) == len(roots))
    raise _Unsupported(f"solution set is not finite: {type(given).__name__}")


def _intervals(a: Any, b: Any, params: Params) -> Result:
    if not (isinstance(a, Relation) and isinstance(b, Relation)):
        raise _Unsupported("interval mode needs two inequalities")
    symbols = _symbols(_relational(a), _relational(b))
    if len(symbols) != 1:
        raise _Unsupported("interval mode needs exactly one variable")
    variable = symbols[0]
    given = sympy.solveset(_relational(a), variable, sympy.S.Reals)
    expected = sympy.solveset(_relational(b), variable, sympy.S.Reals)
    return Equivalent(
        bool(
            sympy.Complement(given, expected) == sympy.S.EmptySet
            and sympy.Complement(expected, given) == sympy.S.EmptySet
        )
    )


def _form(
    a: sympy.Expr, b: sympy.Expr, target: FormTarget | None, rng: random.Random, params: Params
) -> Result:
    value = _equal(a, b, rng, params, hint=False)
    if not value.equivalent:
        return value
    if target != "factored":
        raise _Unsupported(f"no structural check for form {target!r}: the model decides")
    factored = sympy.factor(a)
    return Equivalent(sympy.srepr(factored) == sympy.srepr(a), numeric_only=value.numeric_only)


# --- numeric core ---


def _numeric_equal(
    a: sympy.Expr,
    b: sympy.Expr,
    symbols: Sequence[sympy.Symbol],
    rng: random.Random,
    params: Params,
) -> bool:
    """True if equal at `numeric_points` defined points; False at the first explicit
    mismatch."""
    points = params.numeric_points
    matched = 0
    complex_ok = a.has(sympy.I) or b.has(sympy.I)
    for _ in range(points * params.max_attempts_per_point):
        point = {s: _sample(s, rng, params) for s in symbols}
        va, vb = _value(a, point, params, complex_ok), _value(b, point, params, complex_ok)
        if va is None or vb is None:
            continue
        if not _close(va, vb, params):
            return False
        matched += 1
        if matched == points:
            return True
    raise _TooFewPoints(f"{matched} of {points} points defined")


def _hint(
    a: sympy.Expr,
    b: sympy.Expr,
    symbols: Sequence[sympy.Symbol],
    rng: random.Random,
    params: Params,
) -> ErrorHint | None:
    """Т-§6.2 ориентир: equal to the reference with the opposite sign — `sign`; differs by a
    simple rational factor — `coefficient`."""
    try:
        if b != 0 and _numeric_equal(a, -b, symbols, rng, params):
            return "sign"
    except _TooFewPoints:
        return None
    if b == 0:
        return None
    ratio = _simplify_within(a / b, params.simplify_budget_ms)
    if ratio is not None and ratio.is_Rational and ratio not in (0, 1):
        return "coefficient"
    return None


def _sample(symbol: sympy.Symbol, rng: random.Random, params: Params) -> float:
    if symbol.is_positive:
        return rng.uniform(params.positive_sample_min, params.sample_high)
    if symbol.is_nonnegative:
        return rng.uniform(0.0, params.sample_high)
    if symbol.is_integer:
        return float(rng.randint(int(params.sample_low), int(params.sample_high)))
    return rng.uniform(params.sample_low, params.sample_high)


def _value(
    expr: sympy.Expr, point: Mapping[sympy.Symbol, Any], params: Params, complex_ok: bool = False
) -> complex | None:
    """The value at `point`; `None` where undefined. Variables are real (Т-§6.6), so a
    non-real value is «undefined» unless the expressions use `I` themselves."""
    try:
        value = sympy.N(expr.subs(list(point.items())), params.precision_digits)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not value.is_number or value.has(sympy.zoo, sympy.nan, sympy.oo, -sympy.oo):
        return None
    number = complex(value)
    if not (math.isfinite(number.real) and math.isfinite(number.imag)):
        return None
    if not complex_ok and abs(number.imag) > params.abs_tol * max(1.0, abs(number.real)):
        return None
    return number


def _close(a: complex, b: complex, params: Params) -> bool:
    return abs(a - b) <= max(params.abs_tol, params.rel_tol * max(abs(a), abs(b)))


def _simplify_within(expr: sympy.Expr, budget_ms: int) -> sympy.Expr | None:
    """`simplify` under its own budget (Т-§6.6); `None` once the budget runs out. Uses
    `SIGALRM`: called in the main thread of a pool worker process (WP24)."""
    if budget_ms <= 0:
        return None
    previous = signal.signal(signal.SIGALRM, _on_alarm)
    try:
        signal.setitimer(signal.ITIMER_REAL, budget_ms / 1000)
        try:
            return sympy.simplify(expr)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except _Budget:
        return None
    finally:
        signal.signal(signal.SIGALRM, previous)


def _on_alarm(signum: int, frame: FrameType | None) -> None:
    raise _Budget


# --- helpers ---


def _expr(parsed: Any) -> sympy.Expr:
    if not isinstance(parsed, sympy.Expr):
        raise _Unsupported(f"expected an expression, got {type(parsed).__name__}")
    return parsed


def _as_equation(parsed: Any) -> sympy.Expr:
    if isinstance(parsed, Relation):
        if parsed.op != "=":
            raise _Unsupported("expected an equation")
        return _expr(parsed.lhs) - _expr(parsed.rhs)
    return _expr(parsed)


def _relational(parsed: Relation) -> sympy.Basic:
    ops = {"<": sympy.Lt, "<=": sympy.Le, ">": sympy.Gt, ">=": sympy.Ge}
    if parsed.op not in ops:
        raise _Unsupported("expected an inequality")
    return ops[parsed.op](_expr(parsed.lhs), _expr(parsed.rhs))


def _symbols(*exprs: Any) -> list[sympy.Symbol]:
    found: set[sympy.Symbol] = set()
    for expr in exprs:
        found |= getattr(expr, "free_symbols", set())
    return sorted(found, key=lambda s: s.name)


def _same_roots(given: list[Any], expected: list[Any], params: Params) -> bool:
    if len(given) != len(expected):
        return False
    remaining = [complex(sympy.N(r)) for r in expected]
    for root in given:
        value = complex(sympy.N(root))
        match = next((r for r in remaining if _close(value, r, params)), None)
        if match is None:
            return False
        remaining.remove(match)
    return True


def _is_root(equation: sympy.Expr, variable: sympy.Symbol, root: Any, params: Params) -> bool:
    value = _value(equation, {variable: complex(sympy.N(root))}, params, complex_ok=True)
    return value is not None and _close(value, 0, params)


def _numeric_roots(
    equation: sympy.Expr, variable: sympy.Symbol, rng: random.Random, params: Params
) -> list[complex]:
    roots: list[complex] = []
    for _ in range(params.numeric_points):
        try:
            found = sympy.nsolve(
                equation, variable, rng.uniform(params.sample_low, params.sample_high)
            )
            root = complex(sympy.N(found))
        except (ValueError, ZeroDivisionError, TypeError):
            continue
        if abs(root.imag) < params.abs_tol and not any(_close(root, r, params) for r in roots):
            roots.append(root)
    return roots


def _seed(*parts: str) -> int:
    return int.from_bytes(hashlib.sha256("\x00".join(parts).encode()).digest()[:8], "big")
