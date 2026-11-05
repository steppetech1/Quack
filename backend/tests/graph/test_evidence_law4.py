"""Law 4: evidence strength is assigned by code (ТЗ Фазы 3 §8.6a, WP12: T-W1…T-W6; ТЗ Ф4 `T-W1`
extended to the schemas of Phase 4, D-A-229).

T-W1 — no schema the model fills carries a strength (the payload and the prompt schema of
`extractor@2` while stream B still writes it; `extractor@3` of Phase 4). T-W3 — the weight of
`Т-§7.4` computed by the fold of `rules@3`. T-W4 — the code checks of the elements of
`extractor@2` while `quack.graph.evidence_validation.to_extracted_element` is live; the checks of
`extractor@3` are `graph/test_extracted_check.py`. The projector tests of Phase 3 (T-W4
application, T-W5–T-W7) went with the projector, D-A-229.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from quack.agents.schemas import ExtractorOutputV3
from quack.config import Settings
from quack.events.payloads import ExtractedElement, GapType, SkillRef
from quack.graph.evidence_validation import (
    UNCLEAR_SKILL,
    ExtractionContext,
    SourceEvent,
    to_extracted_element,
)
from quack.graph.projection.skill_fold import FoldEvidence, FoldExposure, FoldParams, fold_skill
from quack.session.schemas import ExtractorOutputV2

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


def test_extractor_output_schema_has_no_weights() -> None:
    """T-W1 on the prompt-side schema of stream B (`ExtractorOutputV2`, D-B-011): numbers are
    only the level and the source event numbers (`events`); everything else is closed or text."""
    schema, defs = _schema(ExtractorOutputV2)
    assert _numeric_fields(schema, defs) == {"entries.level", "entries.events"}
    assert _open_strings(schema, defs) == {
        "entries.skill",
        "entries.misconception",
        "entries.quote",
    }


def test_extractor_v3_output_schema_has_no_weights() -> None:
    """T-W1 on the output of `extractor@3` (Phase 4, `Т-§7.6`): numbers are the level, the hint
    step and the numbers of the source events; the root cause's confidence is a category the code
    turns into a number; every other string is a label or text."""
    schema, defs = _schema(ExtractorOutputV3)
    assert _numeric_fields(schema, defs) == {
        "elements.level",
        "elements.hint_step",
        "elements.events",
    }
    assert _open_strings(schema, defs) == {
        "elements.skill",
        "elements.new_skill.name",
        "elements.new_skill.topic_hint",
        "elements.misconception",
        "elements.new_misconception",
        "elements.root_cause.skill",
        "elements.quote",
    }


def test_skill_ref_requires_exactly_one_form() -> None:
    """The skill of an `extractor@2` element is an id or a new skill, never both or none
    (carried over from the catalog test of Phase 3, D-A-229)."""
    SkillRef.model_validate({"skill_id": str(uuid4())})
    SkillRef.model_validate({"new_skill": {"name": "Chain rule", "topic_id": str(uuid4())}})
    with pytest.raises(ValidationError):
        SkillRef.model_validate({})
    with pytest.raises(ValidationError):
        SkillRef.model_validate(
            {"skill_id": str(uuid4()), "new_skill": {"name": "x", "topic_id": str(uuid4())}}
        )
    with pytest.raises(ValidationError):
        SkillRef.model_validate({"new_skill": {"name": "", "topic_id": str(uuid4())}})


# --- T-W3 ---------------------------------------------------------------------------------

MS_PER_MINUTE = 60_000
AT = 10 * 24 * 60 * MS_PER_MINUTE


def _weight(
    *,
    tier: int = 1,
    exposure_minutes_before: int | None = None,
    params: FoldParams | None = None,
    **fields: Any,
) -> float:
    """The weight the fold of `rules@3` gives one correct piece of evidence at `AT`."""
    evidence = FoldEvidence(
        id="e", at_ms=AT, seq=1, level=3, outcome="correct", tier=tier, verification="code",
        **fields,
    )  # fmt: skip
    shown = (
        []
        if exposure_minutes_before is None
        else [FoldExposure(AT - exposure_minutes_before * MS_PER_MINUTE, 0, "example")]
    )
    fold = fold_skill([evidence], shown, params or FoldParams())
    return float(fold.evidence["e"]["weight"])  # type: ignore[arg-type]


def _params(**overrides: Any) -> FoldParams:
    return FoldParams.from_settings(Settings(**overrides))


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"tier": 1}, 1.0),
        ({"tier": 1, "options": 4}, 0.75),
        ({"tier": 2, "assistance": "hint", "hint_step": 2}, 0.4),
        ({"tier": 3}, 0.4),
        ({"tier": 4}, 0.15),
        ({"tier": 1, "exposure_minutes_before": 5}, 0.5),
        ({"tier": 1, "exposure_minutes_before": 10}, 0.5),
        ({"tier": 1, "exposure_minutes_before": 11}, 1.0),
        ({"tier": 1, "assistance": "shown_solution"}, 0.0),
        ({"tier": 1, "options": 2, "assistance": "hint", "hint_step": 1}, 0.375),
    ],
)
def test_evidence_weight_formula(fields: dict[str, Any], expected: float) -> None:
    """T-W3: `Т-§7.4` table, by the fold of `rules@3`."""
    assert _weight(**fields) == pytest.approx(expected)


def test_evidence_weight_changes_only_through_settings() -> None:
    kwargs: dict[str, Any] = {
        "tier": 2, "assistance": "hint", "hint_step": 1, "exposure_minutes_before": 1,
    }  # fmt: skip
    assert _weight(**kwargs) == pytest.approx(0.8 * 0.75 * 0.5)
    tiers = {"T1": 1.0, "T2": 0.6, "T3": 0.4, "T4": 0.15}
    assert _weight(**kwargs, params=_params(evidence_tier_weights=tiers)) == pytest.approx(
        0.6 * 0.75 * 0.5
    )
    assert _weight(**kwargs, params=_params(hint_step_penalty=0.1)) == pytest.approx(
        0.8 * 0.9 * 0.5
    )
    assert _weight(**kwargs, params=_params(hot_window_factor=1.0)) == pytest.approx(0.8 * 0.75)
    assert _weight(**kwargs, params=_params(hot_window_min=0)) == pytest.approx(0.8 * 0.75)


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
