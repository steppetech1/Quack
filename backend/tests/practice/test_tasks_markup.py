"""ТЗ Ф4 §8.8 T-TK2: markup checked by code against the table Т-§5.3 (WP12.3)."""

from __future__ import annotations

from typing import Any

import pytest

from quack.practice.markup import TaskMarkup, validate_markup

LABELS = {"skill_labels": {"S1", "S2"}, "misconception_labels": {"M1"}, "task_labels": {"K1"}}

NUMBER = {"type": "number", "value": {"latex": "\\frac{1}{3}", "sympy": "1/3"}}
CHOICE = {
    "type": "choice",
    "options": [
        {"option_id": "A", "text": "converges", "correct": True},
        {"option_id": "B", "text": "diverges", "correct": False},
    ],
}


def _markup(level: int, answer: dict[str, Any] = NUMBER, **extra: Any) -> TaskMarkup:
    data = {"tests": [{"skill": "S1", "level": level}], "methodNamed": False, "answer": answer}
    return TaskMarkup.model_validate({**data, **extra})


def _rules(markup: TaskMarkup) -> set[str]:
    return {v.rule for v in validate_markup(markup, **LABELS)}


@pytest.mark.parametrize(
    ("markup", "rule"),
    [
        (_markup(1), "level_1_needs_options"),
        (_markup(2, CHOICE), "level_2_not_choice"),
        (_markup(4, methodNamed=True), "level_4_method_named"),
        (_markup(5), "level_5_needs_pair_or_neighbor"),
        (
            _markup(5, CHOICE, options=[{"option_id": "B", "neighbor_idea": False}]),
            "level_5_needs_pair_or_neighbor",
        ),
        (_markup(6), "level_6_needs_form"),
    ],
)
def test_markup_level_rules(markup: TaskMarkup, rule: str) -> None:
    """T-TK2: each violation of the level table → a refusal with that rule."""
    assert rule in _rules(markup)


@pytest.mark.parametrize(
    "markup",
    [
        _markup(1, CHOICE),
        _markup(2),
        _markup(3),
        _markup(4),
        _markup(5, pairTaskId="K1"),
        _markup(5, CHOICE, options=[{"option_id": "B", "neighbor_idea": True}]),
        _markup(6, form="explain_why"),
    ],
)
def test_markup_level_rules_pass(markup: TaskMarkup) -> None:
    assert _rules(markup) == set()


def test_markup_common_rules() -> None:
    """T-TK2: a pair in `tests`; exactly one correct option; the spec parses; labels exist."""
    no_tests = TaskMarkup.model_validate({"tests": [], "methodNamed": False, "answer": NUMBER})
    assert "no_tests" in _rules(no_tests)

    two_correct = {
        "type": "choice",
        "options": [
            {"option_id": "A", "text": "a", "correct": True},
            {"option_id": "B", "text": "b", "correct": True},
        ],
    }
    assert "choice_one_correct" in _rules(_markup(1, two_correct))

    bad_expr = {"type": "expression", "value": {"latex": "x", "sympy": "x.__class__"}}
    assert "expression_unparsed" in _rules(_markup(3, bad_expr))
    good_expr = {"type": "expression", "value": {"latex": "x^2", "sympy": "x**2 + sin(2*x)"}}
    assert _rules(_markup(3, good_expr)) == set()

    no_unit = {"type": "quantity", "value": {"latex": "9.8", "sympy": "9.8"}}
    assert "quantity_without_unit" in _rules(_markup(3, no_unit))

    unknown = TaskMarkup.model_validate(
        {"tests": [{"skill": "S9", "level": 3}], "methodNamed": False, "answer": NUMBER}
    )
    assert "unknown_label" in _rules(unknown)
    assert "unknown_label" in _rules(_markup(3, variantOf="K7"))
    trap = [{"option_id": "B", "misconception": "M5"}]
    assert "unknown_label" in _rules(_markup(1, CHOICE, options=trap))

    both = TaskMarkup.model_validate(
        {
            "tests": [{"skill": "S1", "new_skill": {"name": "n", "topic_hint": "t"}, "level": 3}],
            "methodNamed": False,
            "answer": NUMBER,
        }
    )
    assert "skill_label_and_new" in _rules(both)
    new_only = TaskMarkup.model_validate(
        {
            "tests": [{"new_skill": {"name": "n", "topic_hint": "t"}, "level": 3}],
            "methodNamed": False,
            "answer": NUMBER,
        }
    )
    assert _rules(new_only) == set()
