"""Six check modes (ТЗ §2.1 п.3) and their run in a child process killed on timeout
(ТЗ §2.1 п.2). Numeric check first, then `simplify` (Т-§6.6)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import multiprocessing
import random
import signal
import time
from multiprocessing.connection import Connection
from types import FrameType
from typing import Any, Literal

from sympy import Expr, FiniteSet, Matrix, S, Symbol, diff, simplify, solveset, sstr
from sympy.matrices import MatrixBase

from quack_compute.parser import Equation, ParseError, parse
from quack_compute.schemas import MathCheckRequest, MathCheckResponse

NUMERIC_POINTS = 20
NUMERIC_MAX_ATTEMPTS = 200
NUMERIC_PRECISION_DIGITS = 30
NUMERIC_REL_TOL = 1e-9
NUMERIC_ABS_TOL = 1e-12
SAMPLE_LOW, SAMPLE_HIGH = -10.0, 10.0
COMPUTE_WORKERS = 4
SYMBOLIC_BUDGET_SHARE = 0.8

log = logging.getLogger(__name__)

# forkserver, not fork: the uvicorn process is multithreaded. The server preloads this
# module, so a child starts without importing SymPy again.
_MP_CONTEXT = multiprocessing.get_context("forkserver")
_MP_CONTEXT.set_forkserver_preload(["quack_compute.checker"])
_WORKERS = asyncio.Semaphore(COMPUTE_WORKERS)


class _Unsupported(Exception):
    """Code cannot give a verdict on this input in this mode."""


class _BudgetExceeded(Exception):
    """The own budget of a symbolic step ran out."""


def check(request: MathCheckRequest) -> MathCheckResponse:
    """Runs the check in the current process; `run_check` isolates it in a child."""
    deadline = time.monotonic() + request.timeout_ms / 1000
    try:
        student = parse(request.student_expr, request.variables)
        reference = parse(request.reference_expr, request.variables)
    except ParseError as exc:
        return _response("parse_error", error_message=str(exc))
    normalized = _normalized(student)
    try:
        is_equivalent, diff_hint = _verdict(request, student, reference, deadline)
    except _Unsupported as exc:
        return _response("unsupported", normalized_student=normalized, error_message=str(exc))
    except Exception as exc:
        return _response(
            "unsupported",
            normalized_student=normalized,
            error_message=f"{type(exc).__name__}: {exc}",
        )
    return MathCheckResponse(
        status="completed",
        is_equivalent=is_equivalent,
        normalized_student=normalized,
        diff_hint=diff_hint,
        error_message=None,
    )


async def run_check(request: MathCheckRequest) -> MathCheckResponse:
    """`check` in a child process; past `timeout_ms` the child is killed."""
    async with _WORKERS:
        receiver, sender = _MP_CONTEXT.Pipe(duplex=False)
        process = _MP_CONTEXT.Process(target=_child, args=(sender, request.model_dump()))
        process.start()
        sender.close()
        try:
            if await asyncio.to_thread(receiver.poll, request.timeout_ms / 1000):
                return MathCheckResponse.model_validate(receiver.recv())
            return _response("timeout", error_message=f"no result in {request.timeout_ms} ms")
        except EOFError:
            return _response("unsupported", error_message="the check process exited abnormally")
        finally:
            if process.exitcode is None:
                process.kill()
            process.join()
            receiver.close()


def _child(conn: Connection, payload: dict[str, Any]) -> None:
    conn.send(check(MathCheckRequest.model_validate(payload)).model_dump())
    conn.close()


def equivalent(
    a: Expr, b: Expr, symbols: list[Symbol], rng: random.Random, budget_sec: float
) -> bool | None:
    """Т-§6.6: a numeric mismatch is final; `None` when too few points are defined."""
    started = time.monotonic()
    matched = 0
    for _ in range(NUMERIC_MAX_ATTEMPTS):
        point = {symbol: rng.uniform(SAMPLE_LOW, SAMPLE_HIGH) for symbol in symbols}
        value_a, value_b = _real_value(a, point), _real_value(b, point)
        if value_a is None or value_b is None:
            continue
        if not _close(value_a, value_b):
            return False
        matched += 1
        if matched == NUMERIC_POINTS:
            break
    else:
        return None
    left = budget_sec - (time.monotonic() - started)
    if _simplify_within(a - b, SYMBOLIC_BUDGET_SHARE * left) != 0:
        log.info("numeric-only equivalence of %s and %s", sstr(a), sstr(b))
    return True


def _verdict(
    request: MathCheckRequest, student: Any, reference: Any, deadline: float
) -> tuple[bool, str | None]:
    symbols = [Symbol(name, real=True) for name in request.variables]
    rng = _seeded_rng(request)

    def same(a: Any, b: Any) -> bool:
        result = equivalent(_as_expr(a), _as_expr(b), symbols, rng, deadline - time.monotonic())
        if result is None:
            raise _Unsupported(
                f"fewer than {NUMERIC_POINTS} points where both sides are finite real numbers"
            )
        return result

    match request.mode:
        case "expression_equiv":
            if same(student, reference):
                return True, None
            return False, _diff_hint(student, reference, symbols, rng, deadline)
        case "equation_roots":
            return _same_roots(_roots(student, symbols[0]), _roots(reference, symbols[0])), None
        case "derivative":
            return same(student, diff(_as_expr(reference), symbols[0])), None
        case "integral":
            variable = symbols[0]
            return same(
                diff(_as_expr(student), variable), diff(_as_expr(reference), variable)
            ), None
        case "matrix_eq":
            a, b = _matrix(student), _matrix(reference)
            if a.shape != b.shape:
                return False, None
            # A list, not a generator: one undecided element makes the check unsupported.
            return all([same(x, y) for x, y in zip(a.flat(), b.flat(), strict=True)]), None
        case "numeric_tol":
            return abs(_finite_real(student) - _finite_real(reference)) <= request.tolerance, None


def _diff_hint(
    student: Expr, reference: Expr, symbols: list[Symbol], rng: random.Random, deadline: float
) -> str | None:
    """errorHint of a wrong answer (Т-§6.2), only while the budget lasts."""
    left = deadline - time.monotonic()
    if left <= 0:
        return None
    if equivalent(student, -reference, symbols, rng, left):
        return "sign"
    ratio = _simplify_within(
        student / reference, SYMBOLIC_BUDGET_SHARE * (deadline - time.monotonic())
    )
    if ratio is not None and ratio.is_Rational and ratio != 0:
        return "coefficient"
    return None


def _roots(parsed: Any, variable: Symbol) -> list[float]:
    """Решение 10: `a = b` is an equation, `2, -2` a root set, an expression is `expr = 0`."""
    if isinstance(parsed, Equation):
        roots = solveset(parsed.lhs - parsed.rhs, variable, S.Reals)
    elif isinstance(parsed, tuple):
        roots = FiniteSet(*parsed)
    elif _as_expr(parsed).is_number:
        roots = FiniteSet(parsed)
    else:
        roots = solveset(parsed, variable, S.Reals)
    if roots is S.EmptySet:
        return []
    if not isinstance(roots, FiniteSet):
        raise _Unsupported(f"the real root set is not finite: {type(roots).__name__}")  # A3
    return [_finite_real(root) for root in roots]


def _same_roots(student: list[float], reference: list[float]) -> bool:
    return len(student) == len(reference) and all(
        sum(_close(root, other) for other in reference) == 1 for root in student
    )


def _matrix(parsed: Any) -> MatrixBase:
    if isinstance(parsed, MatrixBase):
        return parsed
    if isinstance(parsed, list):
        return Matrix(parsed)
    raise _Unsupported(f"expected a matrix, got {type(parsed).__name__}")


def _as_expr(parsed: Any) -> Expr:
    if not isinstance(parsed, Expr):
        raise _Unsupported(f"expected an expression, got {type(parsed).__name__}")
    return parsed


def _real_value(expr: Any, point: dict[Symbol, float]) -> float | None:
    """The value at `point` if it is a finite real number."""
    value = expr.evalf(NUMERIC_PRECISION_DIGITS, subs=point)
    if not (value.is_number and value.is_real):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _finite_real(expr: Any) -> float:
    value = _real_value(_as_expr(expr), {})
    if value is None:
        raise _Unsupported(f"{sstr(expr)} is not a finite real number")
    return value


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(NUMERIC_ABS_TOL, NUMERIC_REL_TOL * max(abs(a), abs(b)))


def _simplify_within(expr: Expr, budget_sec: float) -> Expr | None:
    """`simplify` under its own budget (Т-§6.6); `None` once the budget runs out."""
    if budget_sec <= 0:
        return None
    previous = signal.signal(signal.SIGALRM, _on_budget_alarm)
    try:
        try:
            signal.setitimer(signal.ITIMER_REAL, budget_sec)
            return simplify(expr)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except _BudgetExceeded:
        return None
    finally:
        signal.signal(signal.SIGALRM, previous)


def _on_budget_alarm(signum: int, frame: FrameType | None) -> None:
    raise _BudgetExceeded


def _seeded_rng(request: MathCheckRequest) -> random.Random:
    """A4: the same input always gets the same points, so the verdict is reproducible."""
    key = json.dumps(
        [request.mode, request.student_expr, request.reference_expr, request.variables]
    )
    return random.Random(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big"))


def _normalized(parsed: Any) -> str:
    if isinstance(parsed, Equation):
        return f"{sstr(parsed.lhs)} = {sstr(parsed.rhs)}"
    return sstr(parsed)


def _response(
    status: Literal["parse_error", "timeout", "unsupported"],
    *,
    normalized_student: str | None = None,
    error_message: str | None = None,
) -> MathCheckResponse:
    return MathCheckResponse(
        status=status,
        is_equivalent=None,
        normalized_student=normalized_student,
        diff_hint=None,
        error_message=error_message,
    )
