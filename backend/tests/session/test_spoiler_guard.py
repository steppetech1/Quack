"""Spoiler guard of the teacher turn (ТЗ Фазы 3 §8.6d, WP15)."""

from __future__ import annotations

from uuid import uuid4

from quack.config import Settings
from quack.practice.evaluation import TaskToCheck
from quack.session.spoiler_guard import find_spoiler


def _math_task(reference: str) -> TaskToCheck:
    return TaskToCheck(
        task_id=uuid4(),
        kind="math",
        prompt_text="Solve",
        reference_solution={"expression": reference},
        target_skills=[],
        target_misconceptions=[],
        difficulty=0.5,
        topic_id=uuid4(),
        family_id="f",
    )


def test_guard_ignores_small_numbers_and_single_letters(settings: Settings) -> None:
    """T-G2: reference «7» or «x» → a text with «7» and «x» is not a violation; reference
    «42» → it is."""
    text = "So x equals 7, and 42 is the answer."
    assert find_spoiler(text, [_math_task("7")], settings) is None
    assert find_spoiler(text, [_math_task("x")], settings) is None
    task_42 = _math_task("42")
    assert find_spoiler(text, [task_42], settings) is task_42
