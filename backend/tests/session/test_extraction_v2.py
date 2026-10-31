"""extractor@2 entries → WP12.1 elements (D-B-011)."""

from __future__ import annotations

from typing import Any

import pytest

from quack.llm.strict_schema import to_strict_json_schema
from quack.session.extraction import to_extracted_item, to_extracted_items
from quack.session.schemas import ExtractorEntry, ExtractorOutputV2


def _observation(**fields: Any) -> ExtractorEntry:
    base: dict[str, Any] = {
        "kind": "skill_observation",
        "skill": "S1",
        "misconception": None,
        "level": 3,
        "outcome": "success",
        "help": "none",
        "gap_type": None,
        "quote": "x = 2",
        "events": [4],
    }
    return ExtractorEntry.model_validate(base | fields)


@pytest.mark.parametrize(
    ("help_", "assistance", "hint_step"),
    [
        ("none", "none", None),
        ("after_example", "none", None),
        ("hint_direction", "hint", 1),
        ("hint_key_step", "hint", 2),
        ("hint_near_solution", "hint", 3),
    ],
)
def test_help_maps_to_assistance(help_: str, assistance: str, hint_step: int | None) -> None:
    item = to_extracted_item(_observation(help=help_))
    assert item is not None
    assert (item.kind, item.outcome, item.gap_type) == ("evidence", "correct", "none")
    assert (item.assistance, item.hint_step) == (assistance, hint_step)
    assert (item.skill_label, item.level, item.quote, item.source_seqs) == ("S1", 3, "x = 2", [4])


@pytest.mark.parametrize(
    ("gap", "expected"),
    [
        ("no_idea", "no_idea"),
        ("not_recognizing_when", "when_to_apply"),
        ("confuses_similar", "confusion"),
        ("execution_error", "execution"),
        ("slip", "slip"),
        ("misconception", "misconception"),
    ],
)
def test_failure_gap_types(gap: str, expected: str) -> None:
    item = to_extracted_item(_observation(outcome="failure", gap_type=gap))
    assert item is not None
    assert (item.outcome, item.gap_type) == ("incorrect", expected)


def test_bypass_is_shown_solution_not_failure() -> None:
    item = to_extracted_item(_observation(outcome="bypass", help="solution_shown"))
    assert item is not None
    assert (item.outcome, item.assistance, item.gap_type) == ("partial", "shown_solution", "none")


def test_misconception_suspicion() -> None:
    entry = ExtractorEntry(
        kind="misconception_suspicion",
        skill="S2",
        misconception="new: believes a series converges when its terms tend to zero",
        level=None,
        outcome=None,
        help=None,
        gap_type=None,
        quote="terms go to 0 so it converges",
        events=[7, 9],
    )
    item = to_extracted_item(entry)
    assert item is not None
    assert item.kind == "misconception_hypothesis"
    assert item.misconception_label == entry.misconception
    assert (item.level, item.outcome, item.assistance) == (None, None, None)
    assert item.source_seqs == [7, 9]


def test_incomplete_entries_are_dropped() -> None:
    incomplete = _observation(level=None)
    no_belief = _observation(kind="misconception_suspicion", misconception=None)
    assert to_extracted_items([incomplete, no_belief, _observation()]) == [
        to_extracted_item(_observation())
    ]


def test_output_schema_is_strict() -> None:
    schema = to_strict_json_schema(ExtractorOutputV2)
    assert schema["additionalProperties"] is False
