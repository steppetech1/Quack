"""Safe parser of math input: forbidden substrings, token grammar, then `parse_expr`
with restricted name dictionaries (ТЗ §2.1 п.1, Т-§6.5)."""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from sympy import (
    Abs,
    Add,
    E,
    Float,
    Integer,
    Matrix,
    Mul,
    Pow,
    Rational,
    Symbol,
    cos,
    exp,
    log,
    oo,
    pi,
    sin,
    sqrt,
    tan,
)
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_application,
    implicit_multiplication,
    parse_expr,
    standard_transformations,
)

FORBIDDEN_SUBSTRINGS = ("__", "import", "eval", "exec", "lambda", "open", "getattr")

# Whitespace, numbers (a dot only inside a number), identifiers with one optional
# subscript (`v_0`, `F_net`), operators and brackets. ASCII only: no Unicode look-alikes.
_TOKEN = re.compile(
    r"\s+|\d+(?:\.\d+)?|\.\d+|[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)?|\*\*|[*/+\-^()\[\],=]",
    re.ASCII,
)

_NAMES: dict[str, Any] = {
    "sin": sin,
    "cos": cos,
    "tan": tan,
    "exp": exp,
    "log": log,
    "ln": log,
    "sqrt": sqrt,
    "Abs": Abs,
    "pi": pi,
    "E": E,
    "oo": oo,
    "Matrix": Matrix,
}
# `Add`, `Mul`, `Pow`: with `evaluate=False` SymPy rewrites operators into these calls.
_GLOBALS: dict[str, Any] = {
    "Integer": Integer,
    "Float": Float,
    "Rational": Rational,
    "Symbol": Symbol,
    "Add": Add,
    "Mul": Mul,
    "Pow": Pow,
}
_TRANSFORMATIONS = (
    *standard_transformations,
    implicit_multiplication,
    implicit_application,
    convert_xor,
)


class ParseError(Exception):
    """The input is not an allowed math expression; nothing of it was executed."""


class Equation(NamedTuple):
    lhs: Any
    rhs: Any


def parse(text: str, variables: list[str]) -> Any:
    """SymPy object of `text`; `Equation` when it contains `=`. Variables are real (A2)."""
    lowered = text.lower()
    for forbidden in FORBIDDEN_SUBSTRINGS:
        if forbidden in lowered:
            raise ParseError(f"forbidden substring {forbidden!r}")
    position = 0
    while position < len(text):
        token = _TOKEN.match(text, position)
        if token is None:
            raise ParseError(f"unexpected character {text[position]!r} at position {position}")
        position = token.end()
    if text.count("=") > 1:
        raise ParseError("more than one '='")
    if "=" in text:
        lhs, rhs = text.split("=")
        return Equation(_parse_side(lhs, variables), _parse_side(rhs, variables))
    return _parse_side(text, variables)


def _parse_side(text: str, variables: list[str]) -> Any:
    local_dict = {**_NAMES, **{name: Symbol(name, real=True) for name in variables}}
    global_dict = {**_GLOBALS, "__builtins__": {}}
    try:
        return parse_expr(
            text,
            local_dict=local_dict,
            global_dict=global_dict,
            transformations=_TRANSFORMATIONS,
            evaluate=False,
        )
    except Exception as exc:
        raise ParseError(f"{type(exc).__name__}: {exc}") from exc
