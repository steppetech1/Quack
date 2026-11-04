"""ТЗ Ф4 §8.7 T-CH5: the parser whitelist refuses malicious strings; `sympify` and
`parse_expr` on raw input are never called (Т-§6.5, Т-§22.7)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import sympy

from quack_compute.math.grammar import ParseRefused, Relation, parse

MATH_DIR = Path(__file__).resolve().parents[2] / "quack_compute" / "math"

MALICIOUS = [
    "x.__class__",
    "__import__('os').system('ls')",
    "().__class__.__bases__[0].__subclasses__()",
    "lambda: 1",
    "eval('1')",
    "x if 1 else 2",
    "a__b",
    "_x",
    "x_",
    'f"{x}"',
    "'x'",
    "x; y",
    "xа",  # Cyrillic а
    "х**2",  # Cyrillic х
    "x＋1",  # full-width plus
    "1..2",
    "x[::-1]",
    "{x}",
    "x @ y",
    "getattr(x, 'y')",
]


@pytest.mark.parametrize("text", MALICIOUS)
def test_parser_whitelist_malicious(text: str) -> None:
    """T-CH5: every string is refused before or by SymPy; nothing is executed."""
    with pytest.raises(ParseRefused):
        parse(text)


def test_names_become_symbols_or_functions() -> None:
    x, v0, f_net = sympy.symbols("x v_0 F_net", real=True)
    assert parse("x**2 + sin(2*x)") == x**2 + sympy.sin(2 * x)
    assert parse("v_0*t").has(v0)
    assert parse("F_net").has(f_net)
    # Unknown names, Python builtins included, are plain symbols, not calls of builtins.
    assert parse("eval*x") == sympy.Symbol("eval", real=True) * x
    assert parse("2x") == 2 * x
    assert parse("x^2") == x**2


def test_relations_and_assumptions() -> None:
    x = sympy.Symbol("x", real=True)
    relation = parse("x**2 = 4")
    assert isinstance(relation, Relation) and relation.op == "="
    assert relation.lhs == x**2 and relation.rhs == 4
    assert parse("x >= 1").op == ">="
    positive = parse("sqrt(a**2)", {"a": {"positive": True}})
    assert positive == sympy.Symbol("a", positive=True)


def test_no_raw_sympify_or_parse_expr() -> None:
    """T-CH5 (AST): `sympify` is never called in `math/`; `parse_expr` only in `grammar.py`,
    after the token check."""
    for path in MATH_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            assert name not in ("sympify", "S"), f"{path.name}: {name}"
            if name == "parse_expr":
                assert path.name == "grammar.py", path.name
