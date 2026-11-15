"""LaTeX from the formula editor or the recognizer → SymPy (Т-§6.5): `parse_latex` with the
ANTLR backend and `strict=True` — without strict mode ANTLR may parse an expression only
partly and raise no error. Used only when no `sympy` string is given (Т-§22.7); the result
is a SymPy object, nothing of the input is executed. Relations become `Relation`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import sympy
from sympy.parsing.latex import parse_latex

from quack_compute.math.grammar import ParseRefused, Relation, is_euler

_UPRIGHT_E = re.compile(r"\\mathrm\s*\{\s*e\s*\}")

_OPS: dict[type, str] = {
    sympy.Equality: "=",
    sympy.StrictLessThan: "<",
    sympy.LessThan: "<=",
    sympy.StrictGreaterThan: ">",
    sympy.GreaterThan: ">=",
}


def parse_latex_strict(text: str, variables: Mapping[str, Mapping[str, bool]] | None = None) -> Any:
    """SymPy object of `text`; `ParseRefused` on any parse error. `variables` — name → SymPy
    assumptions of the spec, the same the grammar gives a `sympy` string (Т-§6.6): the symbols
    of both inputs must be one and the same object, or the check samples them independently."""
    if is_euler("e", variables):
        # `\mathrm{e}` (upright e, the constant) is read by the ANTLR grammar as `mathrm * e`.
        text = _UPRIGHT_E.sub("e", text)
    try:
        parsed = parse_latex(text, backend="antlr", strict=True)
    except Exception as exc:
        raise ParseRefused(f"{type(exc).__name__}: {exc}") from exc
    op = _OPS.get(type(parsed))
    if op is not None and isinstance(parsed, sympy.core.relational.Relational):
        return Relation(_assumed(parsed.lhs, variables), op, _assumed(parsed.rhs, variables))
    if parsed is None:
        raise ParseRefused("empty LaTeX")
    return _assumed(parsed, variables)


def _assumed(expr: Any, variables: Mapping[str, Mapping[str, bool]] | None) -> Any:
    """Symbols of the LaTeX parser carry no assumptions; they get those of the spec, and
    variables the spec does not name are real (Т-§6.6), as in `grammar.parse`."""
    if not isinstance(expr, sympy.Basic):
        return expr
    symbols = [s for s in expr.free_symbols if isinstance(s, sympy.Symbol)]
    return expr.xreplace({s: _symbol(s.name, variables) for s in symbols})


def _symbol(name: str, variables: Mapping[str, Mapping[str, bool]] | None) -> Any:
    """`e` is Euler's number unless the spec names a variable `e` — the rule of `grammar.parse`."""
    if is_euler(name, variables):
        return sympy.E
    return sympy.Symbol(name, **(variables or {}).get(name, {"real": True}))
