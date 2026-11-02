"""Safe parse of a math string (Т-§6.5, Т-§22.7; ТЗ Ф4 WP11.5).

The whitelist is a grammar of tokens: numbers (a dot only inside a number), operators,
round and square brackets, comma, `=`, `<`/`>` (inequalities, `interval` mode) and
identifiers of ASCII letters and digits with one optional subscript (`v_0`, `F_net`) — no
leading or double `_`. Anything else is a refusal before SymPy sees the string. Allowed
SymPy functions and constants become those objects; every other identifier becomes a
`Symbol` through `local_dict` (with the assumptions of the spec); `global_dict` holds SymPy
objects only, no Python builtins. `sympify` and `parse_expr` on raw input are forbidden.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, NamedTuple

import sympy
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication,
    parse_expr,
    standard_transformations,
)

_TOKEN = re.compile(
    r"\s+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+|[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)?"
    r"|\*\*|<=|>=|[*/+\-^()\[\],=<>]",
    re.ASCII,
)
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)?", re.ASCII)

# Т-§6.5: the functions and constants of SymPy a learner or a model may use.
ALLOWED_NAMES: Mapping[str, Any] = {
    "sin": sympy.sin,
    "cos": sympy.cos,
    "tan": sympy.tan,
    "cot": sympy.cot,
    "asin": sympy.asin,
    "acos": sympy.acos,
    "atan": sympy.atan,
    "sinh": sympy.sinh,
    "cosh": sympy.cosh,
    "tanh": sympy.tanh,
    "exp": sympy.exp,
    "log": sympy.log,
    "ln": sympy.log,
    "sqrt": sympy.sqrt,
    "Abs": sympy.Abs,
    "factorial": sympy.factorial,
    "Derivative": sympy.Derivative,
    "Integral": sympy.Integral,
    "Limit": sympy.Limit,
    "Sum": sympy.Sum,
    "Matrix": sympy.Matrix,
    "pi": sympy.pi,
    "E": sympy.E,
    "I": sympy.I,
    "oo": sympy.oo,
}

# What `parse_expr` itself needs to build numbers and symbols; nothing else.
_GLOBALS: Mapping[str, Any] = {
    "Integer": sympy.Integer,
    "Float": sympy.Float,
    "Rational": sympy.Rational,
    "Symbol": sympy.Symbol,
    "__builtins__": {},
}
_TRANSFORMATIONS = (*standard_transformations, implicit_multiplication, convert_xor)
_RELATIONS = ("<=", ">=", "<", ">", "=")


class ParseRefused(Exception):
    """The string is not an allowed math expression; nothing of it was executed."""


class Relation(NamedTuple):
    """`lhs op rhs`: an equation (`=`) or an inequality."""

    lhs: Any
    op: str
    rhs: Any


def tokens_ok(text: str) -> bool:
    """True if `text` is a sequence of allowed tokens."""
    if not text.strip():
        return False
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            return False
        pos = match.end()
    return True


def parse(text: str, variables: Mapping[str, Mapping[str, bool]] | None = None) -> Any:
    """SymPy object of `text`: an expression, a `Relation`, a tuple (comma list) or a list
    (square brackets). `variables` — name → SymPy assumptions (`{"real": True}`); other
    identifiers are real symbols by default (Т-§6.6). `ParseRefused` on anything else."""
    if not tokens_ok(text):
        raise ParseRefused(f"not a sequence of allowed tokens: {text!r}")
    for relation in _RELATIONS:
        if relation in text:
            lhs, sep, rhs = text.partition(relation)
            if any(r in rhs for r in _RELATIONS):
                raise ParseRefused("more than one relation")
            return Relation(_parse_side(lhs, variables), sep, _parse_side(rhs, variables))
    return _parse_side(text, variables)


def _parse_side(text: str, variables: Mapping[str, Mapping[str, bool]] | None) -> Any:
    local: dict[str, Any] = dict(ALLOWED_NAMES)
    for name in set(_IDENTIFIER.findall(text)) - set(ALLOWED_NAMES):
        assumptions = (variables or {}).get(name, {"real": True})
        local[name] = sympy.Symbol(name, **assumptions)
    try:
        return parse_expr(
            text,
            local_dict=local,
            global_dict=dict(_GLOBALS),
            transformations=_TRANSFORMATIONS,
        )
    except Exception as exc:
        raise ParseRefused(f"{type(exc).__name__}: {exc}") from exc
