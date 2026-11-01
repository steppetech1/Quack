"""extractor@2 entries → WP12.1 elements (D-B-011)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from quack.config import Settings
from quack.events.catalog import validate_payload
from quack.events.models import StoredEvent
from quack.events.payloads import TurnExtractedV2
from quack.llm.labels import LabelMap
from quack.llm.strict_schema import to_strict_json_schema
from quack.session.extraction import (
    extracted_elements,
    extraction_context,
    to_extracted_item,
    to_extracted_items,
    turn_extracted_v2,
)
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
    # The "new: " prefix of the prompt contract is dropped (seam S6).
    assert item.misconception_label == "believes a series converges when its terms tend to zero"
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


# --- turn.extracted v2 (WP12, seams S5/S6) ---------------------------------------------


def _stored(seq: int, event_type: str, payload: dict[str, Any]) -> StoredEvent:
    return StoredEvent(
        learner_id=uuid4(),
        seq=seq,
        event_id=uuid4(),
        type=event_type,
        schema_version=1,
        actor="learner",
        session_id=None,
        turn_id=None,
        domain_id=None,
        correlation_id=None,
        causation_id=None,
        idempotency_key=f"k{seq}",
        payload_hash="h",
        payload=payload,
        occurred_at=datetime(2026, 10, 31, 12, seq, tzinfo=UTC),
    )


def test_new_prefix_is_dropped() -> None:
    item = to_extracted_item(_observation(skill="new: chain rule for nested roots"))
    assert item is not None and item.skill_label == "chain rule for nested roots"
    suspicion = to_extracted_item(
        _observation(kind="misconception_suspicion", misconception="new: drops the inner factor")
    )
    assert suspicion is not None and suspicion.misconception_label == "drops the inner factor"


def test_turn_extracted_v2_elements_checked_by_code() -> None:
    skill, misc, topic = uuid4(), uuid4(), uuid4()
    labels = LabelMap.build(Settings(), skills=[skill], misconceptions=[misc])
    events = [
        _stored(4, "turn.student_replied", {"text": "so the derivative is 2x cos x^2"}),
        _stored(5, "turn.teacher_replied", {"text": "check the inner factor"}),
        _stored(6, "task.submitted", {"answer": "2x*cos(x**2)"}),
    ]
    ctx = extraction_context(labels, events, topic_id=topic)
    assert set(ctx.episode) == {4, 6}  # only the learner's events may be cited

    output = ExtractorOutputV2(
        entries=[
            _observation(quote="2x*cos(x**2)", events=[6]),
            _observation(
                outcome="failure", gap_type="slip", quote="check the inner factor", events=[5]
            ),
            _observation(skill="S7", quote="2x cos x^2", events=[4]),
            ExtractorEntry(
                kind="misconception_suspicion",
                skill="S1",
                misconception="M1",
                level=None,
                outcome=None,
                help=None,
                gap_type=None,
                quote="derivative is 2x",
                events=[4],
            ),
        ]
    )
    elements = extracted_elements(output, ctx)
    accepted, teacher_quote, unknown, hypothesis = elements
    assert accepted.rejected_reason is None
    assert accepted.skill is not None and accepted.skill.skill_id == skill
    assert accepted.occurred_at == events[2].occurred_at
    assert teacher_quote.rejected_reason == "source_outside_episode"
    assert unknown.rejected_reason == "unknown_label"
    assert hypothesis.rejected_reason is None and hypothesis.misconception_id == misc

    payload = turn_extracted_v2(uuid4(), uuid4(), elements)
    stored = validate_payload("turn.extracted", payload.model_dump(mode="json"), 2)
    assert isinstance(stored, TurnExtractedV2)
    assert len(stored.elements) == 4
