"""`ReferenceSolution` — one format of `core.task_bank.reference_solution` (ТЗ Фазы 3 WP6.4 п.1,
D-B-001, seam S10)."""

from __future__ import annotations

from typing import Any

import pytest

from quack.practice.schemas import (
    ChoiceReference,
    MathReference,
    OpenReference,
    parse_reference_solution,
    reference_solution_payload,
)

MATH = {"expression": "3*x**2", "mode": "expression_equiv", "variables": ["x"], "tolerance": 1e-6}
CHOICE = {"option": "B", "answer": "3x^2", "options": ["A", "B", "C"]}
OPEN = {"key_points": ["state the rule", "apply it"], "sample_answer": "By the power rule..."}


@pytest.mark.parametrize(
    ("kind", "data", "model"),
    [
        ("math", MATH, MathReference),
        ("choice", CHOICE, ChoiceReference),
        ("open", OPEN, OpenReference),
        ("code", OPEN, OpenReference),
    ],
)
def test_table_formats_parse_and_round_trip(
    kind: str, data: dict[str, Any], model: type[object]
) -> None:
    """Each kind of the WP6.4 table parses into its model; the stored dict is the table's."""
    parsed = parse_reference_solution(kind, data)
    assert isinstance(parsed, model)
    assert reference_solution_payload(parsed) == data
    assert parse_reference_solution(kind, reference_solution_payload(parsed)) == parsed


def test_hints_and_latex_are_the_compatible_extension() -> None:
    data = {**MATH, "latex": "3x^{2}", "hints": ["power rule", "bring the exponent down"]}
    parsed = parse_reference_solution("math", data)
    assert isinstance(parsed, MathReference)
    assert parsed.hints == ["power rule", "bring the exponent down"]
    assert reference_solution_payload(parsed) == data


def test_math_defaults_follow_the_table() -> None:
    parsed = parse_reference_solution("math", {"expression": "x + 1"})
    assert reference_solution_payload(parsed) == {
        "expression": "x + 1",
        "mode": "expression_equiv",
        "variables": ["x"],
        "tolerance": 1e-6,
    }


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        ("math", {"sympy": "3*x**2", "check_mode": "expression_equiv"}),  # Phase 2 keys
        ("math", {**MATH, "solution": "..."}),  # unknown key
        ("math", {"expression": ""}),
        ("choice", {"option": "D", "answer": "x", "options": ["A", "B"]}),  # not among options
        ("choice", {"option": "A", "answer": "x"}),
        ("open", {"answer": "by the power rule"}),  # Phase 2 keys
        ("open", {"key_points": [], "sample_answer": "x"}),
        ("essay", OPEN),  # unknown kind
    ],
)
def test_wrong_format_is_rejected(kind: str, data: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_reference_solution(kind, data)
