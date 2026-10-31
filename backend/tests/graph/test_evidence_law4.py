"""Law 4: evidence strength is assigned by code (ТЗ Фазы 3 §8.6a, WP12: T-W1…T-W6)."""

from __future__ import annotations

import typing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from quack.config import FoldParams, Settings
from quack.events.catalog import payload_model
from quack.events.payloads import ExtractedElement, GapType, TurnExtractedV2
from quack.graph.evidence_validation import (
    UNCLEAR_SKILL,
    ExtractionContext,
    SourceEvent,
    to_extracted_element,
)
from quack.graph.evidence_weights import (
    MS_PER_MINUTE,
    EvidenceWeightParams,
    WeightedEvidence,
    assign_tier,
    evidence_weight,
)
from quack.graph.memory import InMemoryGraph
from quack.graph.projector import project_in_memory
from tests.factories import EventLog

P = FoldParams()


# --- T-W1 ---------------------------------------------------------------------------------


def _numeric_fields(schema: dict[str, Any], defs: dict[str, Any], path: str = "") -> set[str]:
    """Dotted names of numeric fields in a JSON schema (refs resolved, arrays entered)."""
    if "$ref" in schema:
        return _numeric_fields(defs[schema["$ref"].rsplit("/", 1)[-1]], defs, path)
    found: set[str] = set()
    for branch in schema.get("anyOf", []) + schema.get("allOf", []):
        found |= _numeric_fields(branch, defs, path)
    kind = schema.get("type")
    if kind in ("integer", "number"):
        found.add(path)
    if kind == "array" and "items" in schema:
        found |= _numeric_fields(schema["items"], defs, path)
    for name, sub in schema.get("properties", {}).items():
        found |= _numeric_fields(sub, defs, f"{path}.{name}" if path else name)
    return found


def _open_strings(schema: dict[str, Any], defs: dict[str, Any], path: str = "") -> set[str]:
    if "$ref" in schema:
        return _open_strings(defs[schema["$ref"].rsplit("/", 1)[-1]], defs, path)
    found: set[str] = set()
    for branch in schema.get("anyOf", []):
        found |= _open_strings(branch, defs, path)
    if schema.get("type") == "array" and "items" in schema:
        found |= _open_strings(schema["items"], defs, path)
    closed = "enum" in schema or "const" in schema or "format" in schema  # ids and times
    if schema.get("type") == "string" and not closed:
        found.add(path)
    for name, sub in schema.get("properties", {}).items():
        found |= _open_strings(sub, defs, f"{path}.{name}" if path else name)
    return found


def _schema(model: type[BaseModel]) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = model.model_json_schema()
    return schema, schema.get("$defs", {})


def test_extractor_schema_has_no_weights() -> None:
    """T-W1: the `extractor@2` element written into `turn.extracted` v2 has no numeric field
    but `level`, `hint_step` and `source_seqs`; its enumerations are closed. (The prompt-side
    schema `ExtractorOutputV2` of stream B is checked after its PR, D-B-011.)"""
    element, defs = _schema(ExtractedElement)
    assert _numeric_fields(element, defs) == {"level", "hint_step", "source_seqs"}
    assert _open_strings(element, defs) == {"skill_label", "misconception_label", "quote"} | {
        "skill.new_skill.name"
    }


def test_turn_extracted_v2_is_in_the_catalog() -> None:
    assert payload_model("turn.extracted", 2) is TurnExtractedV2
    assert payload_model("turn.extracted", 1).__name__ == "TurnExtracted"


# --- T-W2 ---------------------------------------------------------------------------------


def test_tier_assignment_by_source() -> None:
    """T-W2: the tier depends only on the source and the second opinion (`Т-§7.8`)."""
    assert assign_tier("code_verdict") == 1
    assert assign_tier("model_verdict") == 2
    assert assign_tier("model_verdict", "agreed") == 2
    assert assign_tier("model_verdict", "disagreed") == 3
    assert assign_tier("extractor") == 3
    assert assign_tier("conversation") == 4
    # No parameter carries the model's confidence: it cannot change the tier.
    hints = typing.get_type_hints(assign_tier)
    assert set(hints) == {"source", "second_opinion", "return"}


# --- T-W3 ---------------------------------------------------------------------------------

AT = 10 * 24 * 60 * MS_PER_MINUTE


def _params(**overrides: Any) -> EvidenceWeightParams:
    return EvidenceWeightParams.from_settings(Settings().model_copy(update=overrides))


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (WeightedEvidence(tier=1, at_ms=AT), 1.0),
        (WeightedEvidence(tier=1, at_ms=AT, options_count=4), 0.75),
        (WeightedEvidence(tier=2, at_ms=AT, hint_step=2), 0.4),
        (WeightedEvidence(tier=3, at_ms=AT), 0.4),
        (WeightedEvidence(tier=4, at_ms=AT), 0.15),
        (WeightedEvidence(tier=1, at_ms=AT, last_exposure_at_ms=AT - 5 * MS_PER_MINUTE), 0.5),
        (WeightedEvidence(tier=1, at_ms=AT, last_exposure_at_ms=AT - 10 * MS_PER_MINUTE), 0.5),
        (WeightedEvidence(tier=1, at_ms=AT, last_exposure_at_ms=AT - 11 * MS_PER_MINUTE), 1.0),
        (WeightedEvidence(tier=1, at_ms=AT, shown_solution=True), 0.0),
        (WeightedEvidence(tier=1, at_ms=AT, options_count=2, hint_step=1), 0.375),
    ],
)
def test_evidence_weight_formula(evidence: WeightedEvidence, expected: float) -> None:
    """T-W3: `Т-§7.4` table."""
    assert evidence_weight(evidence, _params()) == pytest.approx(expected)


def test_evidence_weight_changes_only_through_settings() -> None:
    e = WeightedEvidence(tier=2, at_ms=AT, hint_step=1, last_exposure_at_ms=AT - MS_PER_MINUTE)
    base = evidence_weight(e, _params())
    assert base == pytest.approx(0.8 * 0.75 * 0.5)
    tiers = {"T1": 1.0, "T2": 0.6, "T3": 0.4, "T4": 0.15}
    assert evidence_weight(e, _params(evidence_tier_weights=tiers)) == pytest.approx(
        0.6 * 0.75 * 0.5
    )
    assert evidence_weight(e, _params(hint_step_penalty=0.1)) == pytest.approx(0.8 * 0.9 * 0.5)
    assert evidence_weight(e, _params(hot_window_factor=1.0)) == pytest.approx(0.8 * 0.75)
    assert evidence_weight(e, _params(hot_window_min=0)) == pytest.approx(0.8 * 0.75)


# --- T-W4 (validation) --------------------------------------------------------------------

T_REPLY = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Item:
    """Shape of stream B's `ExtractedItem` (WP12.1)."""

    kind: Literal["evidence", "misconception_hypothesis"] = "evidence"
    skill_label: str = "S1"
    misconception_label: str | None = None
    level: int | None = 2
    outcome: Literal["correct", "incorrect", "partial"] | None = "correct"
    assistance: Literal["none", "hint", "shown_solution"] | None = "none"
    hint_step: int | None = None
    gap_type: GapType | None = "none"
    quote: str = "x = 4"
    source_seqs: list[int] = field(default_factory=lambda: [7])


SKILL = uuid4()
MISC = uuid4()


def _ctx(**overrides: Any) -> ExtractionContext:
    base = ExtractionContext(
        skill_labels={"S1": SKILL},
        misconception_labels={"M1": MISC},
        episode={
            6: SourceEvent(6, "Solve 2x = 8", T_REPLY - timedelta(minutes=1)),
            7: SourceEvent(7, "I think x = 4 because 8 / 2", T_REPLY),
        },
        task_levels={6: 3},
    )
    return replace(base, **overrides)


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        (Item(skill_label="S9"), "unknown_label"),
        (Item(skill_label=UNCLEAR_SKILL), "unclear_skill"),
        (Item(quote="x = 5"), "quote_not_in_source"),
        (Item(source_seqs=[3]), "source_outside_episode"),
        (Item(level=4, source_seqs=[6, 7]), "level_above_task"),
        (Item(outcome="incorrect", gap_type="execution", quote=""), "missing_quote"),
        (
            Item(kind="misconception_hypothesis", misconception_label="M1", quote=""),
            "missing_quote",
        ),
        (Item(level=None), "incomplete_element"),
        (Item(kind="misconception_hypothesis", misconception_label="M7"), "unknown_label"),
    ],
)
def test_observation_validation_rejects_bad_elements(item: Item, reason: str) -> None:
    """T-W4 (code checks of `Т-§7.6`): each bad element keeps a rejection reason."""
    element = to_extracted_element(item, _ctx())
    assert element.rejected_reason == reason


def test_valid_elements_are_resolved() -> None:
    ok = to_extracted_element(Item(level=3, source_seqs=[6, 7]), _ctx())
    assert ok.rejected_reason is None
    assert ok.skill is not None and ok.skill.skill_id == SKILL
    assert ok.occurred_at == T_REPLY  # WP12.2 п.2: time of the latest source event
    hypothesis = to_extracted_element(
        Item(kind="misconception_hypothesis", misconception_label="M1", level=None, outcome=None),
        _ctx(),
    )
    assert hypothesis.rejected_reason is None
    assert hypothesis.misconception_id == MISC
    new_belief = to_extracted_element(
        Item(
            kind="misconception_hypothesis",
            misconception_label="Dividing flips the sign",
            level=None,
            outcome=None,
        ),
        _ctx(),
    )
    assert new_belief.rejected_reason is None and new_belief.misconception_id is None
    new_skill_topic = uuid4()
    described = to_extracted_element(
        Item(skill_label="Solving linear equations"), _ctx(new_skill_topic_id=new_skill_topic)
    )
    assert described.rejected_reason is None
    assert described.skill is not None and described.skill.new_skill is not None
    assert described.skill.new_skill.topic_id == new_skill_topic


# --- projector: T-W4 (application), T-W5, T-W6 --------------------------------------------


@dataclass
class History:
    log: EventLog
    domain: UUID
    topic: UUID
    skill: UUID
    session: UUID


def _history() -> History:
    log = EventLog()
    domain, topic, skill, session = uuid4(), uuid4(), uuid4(), uuid4()
    d = str(domain)
    log.add("learner.registered", {"email": "l@q.x", "display_name": "L", "preferences": {}})
    log.add("domain.created", {"domain_id": d, "name": "Algebra", "slug": "algebra"})
    log.add(
        "structure.proposed",
        {
            "domain_id": d,
            "structure_version": 1,
            "topics": [{"topic_id": str(topic), "name": "Equations"}],
            "skills": [{"skill_id": str(skill), "topic_id": str(topic), "name": "Linear"}],
            "prereqs": [],
            "skill_deps": [],
        },
    )
    log.add("structure.confirmed", {"domain_id": d, "structure_version": 1})
    log.add(
        "session.started",
        {
            "session_id": str(session),
            "domain_id": d,
            "topic_id": str(topic),
            "mode": "learn",
            "goal": "equations",
            "plan": [{"step": "linear"}],
        },
    )
    return History(log, domain, topic, skill, session)


def _reply(h: History, text: str) -> tuple[int, datetime, str]:
    turn = str(uuid4())
    event = h.log.add(
        "turn.student_replied",
        {"session_id": str(h.session), "turn_id": turn, "turn_index": 0, "text": text},
        actor="learner",
    )
    return event.seq, event.occurred_at, turn


def _element(h: History, seq: int, at: datetime, **fields: Any) -> dict[str, Any]:
    element = {
        "kind": "evidence",
        "skill_label": "S1",
        "skill": {"skill_id": str(h.skill)},
        "level": 2,
        "outcome": "correct",
        "assistance": "none",
        "gap_type": "none",
        "quote": "x = 4",
        "source_seqs": [seq],
        "occurred_at": at.isoformat(),
    }
    element.update(fields)
    return element


def _extracted(h: History, turn: str, elements: list[dict[str, Any]], hours_later: float) -> None:
    h.log.advance(hours=hours_later)
    h.log.add(
        "turn.extracted",
        {"session_id": str(h.session), "turn_id": turn, "elements": elements},
        actor="extractor@2",
        schema_version=2,
    )


def _evidence(graph: InMemoryGraph) -> list[dict[str, Any]]:
    return [props for _, props in graph.nodes_with_label("Evidence")]


async def _project(h: History) -> InMemoryGraph:
    return await project_in_memory(h.log.learner_id, h.log.events, P, batch_size=3)


async def test_rejected_elements_create_no_evidence() -> None:
    """T-W4 (projector): rejected elements are kept in the event and not applied; the valid
    elements of the same answer are applied."""
    h = _history()
    seq, at, turn = _reply(h, "x = 4")
    good = _element(h, seq, at)
    bad = _element(h, seq, at, rejected_reason="quote_not_in_source", quote="x = 5")
    _extracted(h, turn, [bad, good], hours_later=0)
    evidence = _evidence(await _project(h))
    assert len(evidence) == 1
    assert evidence[0]["tier"] == 3 and evidence[0]["sourceType"] == "extractor"


async def test_evidence_time_from_source_event() -> None:
    """T-W5: evidence written an hour after the reply gets `at` = `occurred_at` of the reply."""
    h = _history()
    seq, at, turn = _reply(h, "x = 4")
    _extracted(h, turn, [_element(h, seq, at)], hours_later=1)
    (evidence,) = _evidence(await _project(h))
    assert evidence["at"] == int(at.timestamp() * 1000)
    assert h.log.events[-1].occurred_at - at >= timedelta(hours=1)


async def test_v1_and_v2_extracted_events_both_apply() -> None:
    """T-W6: `turn.extracted` v1 and v2 in one log both apply; the stream does not stop."""
    h = _history()
    _, _, turn = _reply(h, "x = 4")
    h.log.add(
        "turn.extracted",
        {
            "session_id": str(h.session),
            "turn_id": turn,
            "observations": [
                {
                    "skill": {"skill_id": str(h.skill)},
                    "direction": "positive",
                    "strength": 0.9,
                    "independence": 1.0,
                    "tested_depth": 0.5,
                    "polarity_weight": 1.0,
                    "evidence_text": "x = 4",
                }
            ],
        },
        actor="extractor@1",
    )
    seq2, at2, turn2 = _reply(h, "then y = 3")
    _extracted(h, turn2, [_element(h, seq2, at2, quote="y = 3")], hours_later=0)
    graph = await _project(h)
    assert len(_evidence(graph)) == 2
    learner = graph.get("Learner", str(h.log.learner_id))
    assert learner is not None and learner["appliedSeq"] == h.log.events[-1].seq
