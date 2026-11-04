"""ТЗ Ф4 WP12.3, WP11.5, D-B-137: the API-side token grammar (D-B-115) accepts the same
relations as the compute grammar (`<`, `<=`, `>`, `>=`, `=`), so a spec in `interval` mode —
an inequality, Т-§6.6 — passes `validate_markup` instead of being dropped as unparsed."""

from __future__ import annotations

import pytest

from quack.practice.grammar import grammar_ok
from quack.practice.markup import TaskMarkup, validate_markup

LABELS = {"skill_labels": {"S1"}, "misconception_labels": set[str](), "task_labels": set[str]()}


@pytest.mark.parametrize("text", ["x > 2", "x<=3", "2*x + 1 >= 0", "x < -1", "y = 2*x"])
def test_relations_are_tokens(text: str) -> None:
    assert grammar_ok(text)


@pytest.mark.parametrize("text", ["x ≥ 2", "x != 2", "x < 2 !"])
def test_other_comparisons_stay_refused(text: str) -> None:
    """Only the tokens of the compute grammar: no Unicode signs, no `!`."""
    assert not grammar_ok(text)


def test_interval_spec_passes_markup_check() -> None:
    markup = TaskMarkup.model_validate(
        {
            "tests": [{"skill": "S1", "level": 3}],
            "methodNamed": False,
            "answer": {
                "type": "expression",
                "value": {"latex": "x > 2", "sympy": "x > 2"},
                "mode": "interval",
                "variables": [{"name": "x", "assumptions": ["real"]}],
            },
        }
    )
    rules = {v.rule for v in validate_markup(markup, **LABELS)}
    assert "expression_unparsed" not in rules
