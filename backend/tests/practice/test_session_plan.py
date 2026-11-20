"""The plan document of a session (`quack.practice.session_plan`): lifting the old form, merging
the update of a turn (`null` is «no change»), the edits of landmarks."""

from __future__ import annotations

from uuid import uuid4

from quack.events.records import (
    BranchRecord,
    LandmarkAddRecord,
    LandmarkEditsRecord,
    LandmarkRefRecord,
    PlanStepRecord,
    PlanUpdateRecord,
)
from quack.practice.session_plan import PlanDoc, lift_plan, merge_plan, prepared_plan

SKILL, TOPIC = uuid4(), uuid4()


def _update(**fields: object) -> PlanUpdateRecord:
    base: dict[str, object] = {
        "goal": None,
        "current_step": None,
        "next_steps": None,
        "landmark_edits": None,
        "branches": [],
        "cancel_request_ids": [],
    }
    return PlanUpdateRecord.model_validate({**base, **fields})


def test_an_empty_or_foreign_document_is_an_empty_plan() -> None:
    assert lift_plan(None) == PlanDoc()
    assert lift_plan("junk") == PlanDoc()
    assert lift_plan({}) == PlanDoc()


def test_the_old_form_is_lifted_without_losing_what_is_shown() -> None:
    old = {
        "completed_steps": ["a"],
        "current_step": "Recall the ratio",
        "next_steps": ["Try a task", 5],
        "branches": [{"description": "why", "type": "curiosity"}, {"junk": 1}],
        "cancel_request_ids": [],
    }
    doc = lift_plan(old)
    assert doc.goal is None and doc.landmark_edits.added == [] and doc.landmark_edits.removed == []
    assert doc.current_step == PlanStepRecord(id="Recall the ratio", text="Recall the ratio")
    assert [s.text for s in doc.next_steps] == ["Try a task"]
    assert doc.branches == [BranchRecord(description="why", type="curiosity")]
    assert lift_plan({"current_step": ""}).current_step is None


def test_a_document_of_version_2_round_trips() -> None:
    doc = merge_plan(
        None,
        _update(
            goal="g",
            current_step={"id": "s", "text": "S", "landmark": {"kind": "skill", "id": SKILL}},
            next_steps=[{"id": "n", "text": "N"}],
        ),
    )
    assert doc.v == 2
    assert lift_plan(doc.to_json()) == doc
    assert doc.to_json()["current_step"]["landmark"] == {"kind": "skill", "id": str(SKILL)}


def test_null_leaves_and_a_value_replaces() -> None:
    first = merge_plan(
        None,
        _update(
            goal="g", current_step={"id": "a", "text": "A"}, next_steps=[{"id": "b", "text": "B"}]
        ),
    )
    same = merge_plan(first.to_json(), _update())
    assert same == first
    moved = merge_plan(
        first.to_json(), _update(current_step={"id": "b", "text": "B"}, next_steps=[])
    )
    assert moved.goal == "g" and moved.current_step is not None and moved.current_step.id == "b"
    assert moved.next_steps == []


def test_branches_are_those_of_the_last_turn() -> None:
    branch = {"description": "d", "type": "external"}
    doc = merge_plan(None, _update(branches=[branch]))
    assert len(doc.branches) == 1
    assert merge_plan(doc.to_json(), _update()).branches == []


def test_landmark_edits_accumulate_and_the_last_word_wins() -> None:
    ref = LandmarkRefRecord(kind="skill", id=SKILL)
    add = LandmarkEditsRecord(add=[LandmarkAddRecord(kind="skill", id=SKILL)], remove=[])
    drop = LandmarkEditsRecord(add=[], remove=[ref])
    other = LandmarkEditsRecord(add=[LandmarkAddRecord(kind="topic", id=TOPIC)], remove=[])
    doc = merge_plan(None, _update(landmark_edits=add))
    doc = merge_plan(doc.to_json(), _update(landmark_edits=other))
    assert [a.id for a in doc.landmark_edits.added] == [SKILL, TOPIC]
    # a repeat of the same update changes nothing (a retried commit)
    assert merge_plan(doc.to_json(), _update(landmark_edits=other)) == doc
    doc = merge_plan(doc.to_json(), _update(landmark_edits=drop))
    assert [a.id for a in doc.landmark_edits.added] == [TOPIC]
    assert doc.landmark_edits.removed == [ref]
    doc = merge_plan(doc.to_json(), _update(landmark_edits=add))
    assert doc.landmark_edits.removed == []
    assert {a.id for a in doc.landmark_edits.added} == {SKILL, TOPIC}


def test_a_prepared_draft_has_the_steps_and_no_goal() -> None:
    steps = [PlanStepRecord(id=f"draft-{n}", text=f"t{n}") for n in (1, 2, 3)]
    doc = prepared_plan(steps)
    assert doc.goal is None and doc.current_step == steps[0] and doc.next_steps == steps[1:]
    assert prepared_plan([]).current_step is None


def test_lift_plan_skips_malformed_v2_parts() -> None:
    doc = lift_plan(
        {
            "v": 2,
            "goal": "   ",
            "current_step": {"id": 123, "text": "bad"},
            "next_steps": [
                {"id": "ok", "text": "Ok", "landmark": {"kind": "bogus", "id": str(SKILL)}},
                "junk",
            ],
            "landmark_edits": {
                "added": [
                    {"kind": "skill", "id": str(SKILL), "before_kind": "skill", "before_id": "bad"},
                    {"kind": "other", "id": str(TOPIC)},
                    12,
                ],
                "removed": [
                    {"kind": "topic", "id": "not-uuid"},
                    {"kind": "topic", "id": str(TOPIC)},
                ],
            },
            "branches": [{"description": "d", "type": "curiosity"}, "bad"],
        }
    )
    assert doc.goal is None
    assert doc.current_step is None
    assert doc.next_steps == [PlanStepRecord(id="ok", text="Ok", landmark=None)]
    assert doc.landmark_edits.added == [
        LandmarkAddRecord(kind="skill", id=SKILL, before_kind=None, before_id=None)
    ]
    assert doc.landmark_edits.removed == [LandmarkRefRecord(kind="topic", id=TOPIC)]
    assert doc.branches == [BranchRecord(description="d", type="curiosity")]
    bad = lift_plan({"v": 2, "next_steps": "not-a-list", "landmark_edits": "not-a-dict"})
    assert bad == PlanDoc()
