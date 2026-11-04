"""LaTeX from the formula editor or the recognizer → SymPy (Т-§6.5): `parse_latex` with the
ANTLR backend and `strict=True` — without strict mode ANTLR may parse an expression only
partly and raise no error. Used only when no `sympy` string is given (Т-§22.7); the result
is a SymPy object, nothing of the input is executed. Relations become `Relation`."""

from __future__ import annotations

from typing import Any

import sympy
from sympy.parsing.latex import parse_latex

from quack_compute.math.grammar import ParseRefused, Relation

_OPS: dict[type, str] = {
    sympy.Equality: "=",
    sympy.StrictLessThan: "<",
    sympy.LessThan: "<=",
    sympy.StrictGreaterThan: ">",
    sympy.GreaterThan: ">=",
}


def parse_latex_strict(text: str) -> Any:
    """SymPy object of `text`; `ParseRefused` on any parse error."""
    try:
        parsed = parse_latex(text, backend="antlr", strict=True)
    except Exception as exc:
        raise ParseRefused(f"{type(exc).__name__}: {exc}") from exc
    op = _OPS.get(type(parsed))
    if op is not None and isinstance(parsed, sympy.core.relational.Relational):
        return Relation(_real(parsed.lhs), op, _real(parsed.rhs))
    if parsed is None:
        raise ParseRefused("empty LaTeX")
    return _real(parsed)


def _real(expr: Any) -> Any:
    """Symbols of the LaTeX parser carry no assumptions; variables are real (Т-§6.6)."""
    if not isinstance(expr, sympy.Basic):
        return expr
    symbols = [s for s in expr.free_symbols if isinstance(s, sympy.Symbol)]
    return expr.xreplace({s: sympy.Symbol(s.name, real=True) for s in symbols})
