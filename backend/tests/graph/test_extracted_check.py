"""ТЗ Ф4 WP15 п.4, п.8 (`Т-§7.6`): code checks every `extractor@3` element before
`observation.extracted` is written; labels become ids, failed elements stay with the reason."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from quack.agents.schemas import ExtractedElement, RootCause
from quack.events.values import NewSkill
from quack.graph.evidence_validation import (
    EpisodeCheckContext,
    EpisodeSource,
    ExtractorElementLike,
    check_extracted,
)

SKILL = UUID(int=0x51)
ROOT = UUID(int=0x52)
MISCONCEPTION = UUID(int=0x61)
TASK_SEQ, ANSWER_SEQ, REPLY_SEQ = 20, 21, 22

CTX = EpisodeCheckContext(
    skill_labels={"S1": SKILL, "S2": ROOT},
    misconception_labels={"M1": MISCONCEPTION},
    episode={
        TASK_SEQ: EpisodeSource(TASK_SEQ, "Solve x^2 = 4", at_ms=1_000),
        ANSWER_SEQ: EpisodeSource(ANSWER_SEQ, "x = 2, so the answer is 2", at_ms=2_000),
        REPLY_SEQ: EpisodeSource(REPLY_SEQ, "I forgot the negative root", at_ms=3_000),
    },
    task_levels={TASK_SEQ: 3, ANSWER_SEQ: 3},
    code_verdicts=frozenset({(SKILL, 2)}),
)


def _el(**changes: Any) -> ExtractedElement:
    base: dict[str, Any] = {
        "kind": "evidence",
        "skill": "S1",
        "new_skill": None,
        "skill_unclear": False,
        "level": 3,
        "outcome": "incorrect",
        "help": "none",
        "hint_step": None,
        "gap_type": "execution",
        "misconception": None,
        "new_misconception": None,
        "root_cause": None,
        "quote": "x = 2",
        "events": [ANSWER_SEQ],
    }
    return ExtractedElement.model_validate({**base, **changes})


def _reasons(*elements: ExtractedElement) -> list[str]:
    return [r.reason for r in check_extracted(elements, CTX).rejected]


def test_stream_b_element_fits_the_protocol() -> None:
    element: ExtractorElementLike = _el()  # pyright checks the shapes match
    assert element.kind == "evidence"


def test_valid_evidence_gets_ids_and_time_from_its_sources() -> None:
    root = RootCause(skill="S2", confidence="high")
    checked = check_extracted([_el(root_cause=root, events=[ANSWER_SEQ, REPLY_SEQ])], CTX)
    assert checked.rejected == []
    (record,) = checked.elements
    assert record.skill_id == SKILL and record.level == 3 and record.outcome == "incorrect"
    assert record.root_cause is not None
    assert (record.root_cause.skill_id, record.root_cause.confidence) == (ROOT, "high")
    assert record.events == [ANSWER_SEQ, REPLY_SEQ]
    assert record.at_ms == 3_000  # `Т-§2.1`: the time of the latest cited learner event


def test_misconception_label_and_new_wording() -> None:
    known = _el(kind="misconception_hypothesis", misconception="M1", level=None, outcome=None)
    new = _el(
        kind="misconception_hypothesis",
        new_misconception="drops the negative root",
        level=None,
        outcome=None,
    )
    checked = check_extracted([known, new], CTX)
    assert checked.rejected == []
    assert [e.misconception_id for e in checked.elements] == [MISCONCEPTION, None]
    assert checked.elements[1].new_misconception == "drops the negative root"


def test_unclear_skill_is_kept_without_a_skill() -> None:
    """`Т-§7.6`: «неясно» is a valid answer of the extractor, not a failed check."""
    checked = check_extracted([_el(skill=None, skill_unclear=True)], CTX)
    assert checked.rejected == []
    assert checked.elements[0].skill_id is None and checked.elements[0].skill_unclear


def test_new_skill_is_kept_with_its_quote() -> None:
    new_skill = NewSkill(name="quadratic roots", topic_hint="quadratics")
    checked = check_extracted([_el(skill=None, new_skill=new_skill)], CTX)
    assert checked.rejected == [] and checked.elements[0].new_skill == new_skill


def test_unknown_labels_are_rejected() -> None:
    assert _reasons(_el(skill="S9")) == ["unknown_label"]
    misconception = _el(kind="misconception_hypothesis", misconception="M7", level=None)
    assert _reasons(misconception) == ["unknown_label"]
    assert _reasons(_el(root_cause=RootCause(skill="S8", confidence="low"))) == ["unknown_label"]


def test_help_and_gap_type_must_agree() -> None:
    """WP15 п.3, `Т-§7.6`: a hint has its step 1–3 and only a hint has one; a positive element
    has gap type `none` (review of B in #133)."""
    assert _reasons(_el(help="hint", hint_step=None)) == ["incomplete_element"]
    assert _reasons(_el(help="none", hint_step=2)) == ["incomplete_element"]
    assert _reasons(_el(help="shown_solution", hint_step=1)) == ["incomplete_element"]
    assert _reasons(_el(outcome="correct", gap_type="slip")) == ["incomplete_element"]
    assert _reasons(_el(help="hint", hint_step=2)) == []
    assert _reasons(_el(outcome="correct", gap_type="none")) == []
    assert _reasons(_el(outcome="partial", gap_type="execution")) == []


def test_incomplete_elements_are_rejected() -> None:
    assert _reasons(_el(level=None)) == ["incomplete_element"]
    assert _reasons(_el(outcome=None)) == ["incomplete_element"]
    assert _reasons(_el(skill=None)) == ["incomplete_element"]  # no skill, new or unclear
    hypothesis = _el(kind="misconception_hypothesis", level=None, outcome=None)
    assert _reasons(hypothesis) == ["incomplete_element"]  # neither label nor wording


def test_sources_must_lie_inside_the_episode() -> None:
    assert _reasons(_el(events=[ANSWER_SEQ, 5])) == ["source_outside_episode"]
    assert _reasons(_el(events=[])) == ["source_outside_episode"]


def test_negative_elements_need_a_quote_from_their_sources() -> None:
    assert _reasons(_el(quote=" ")) == ["missing_quote"]
    hypothesis = _el(kind="misconception_hypothesis", misconception="M1", quote="", level=None)
    assert _reasons(hypothesis) == ["missing_quote"]
    new_skill = NewSkill(name="roots", topic_hint="quadratics")
    positive = {"outcome": "correct", "gap_type": "none"}
    assert _reasons(_el(skill=None, new_skill=new_skill, quote="", **positive)) == [
        "missing_quote"
    ]  # `Т-§4.4`: a new skill only with a quote
    assert _reasons(_el(quote="x = 3")) == ["quote_not_in_source"]
    assert _reasons(_el(quote="negative root", events=[ANSWER_SEQ])) == ["quote_not_in_source"]
    assert _reasons(_el(outcome="correct", quote="", gap_type="none")) == []


def test_level_is_capped_by_the_task_markup() -> None:
    assert _reasons(_el(level=4)) == ["level_above_task"]
    assert _reasons(_el(level=4, events=[REPLY_SEQ], quote="negative root")) == []


def test_extractor_does_not_rejudge_code_verdicts() -> None:
    """WP15 п.8: evidence for a (skill, level) with a code verdict in the episode."""
    assert _reasons(_el(level=2)) == ["code_verdict_exists"]
    hypothesis = _el(kind="misconception_hypothesis", misconception="M1", level=2)
    assert _reasons(hypothesis) == []  # interpretation of a closed answer stays


def test_rejected_keep_their_index_and_quote() -> None:
    checked = check_extracted([_el(), _el(skill="S9", quote="x = 2"), _el()], CTX)
    assert len(checked.elements) == 2
    ((index, reason, quote),) = [(r.index, r.reason, r.quote) for r in checked.rejected]
    assert (index, reason, quote) == (1, "unknown_label", "x = 2")
