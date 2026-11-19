"""`turn.committed` schema version 2: the plan of the session as a graph (`markup.plan_update`).

Version 1 events stay in the journal as they were written; the one payload model reads both —
the old plan is lifted when read (`upcast_plan_update`, Т-§2.1: «старые события не переписываются»).
The version-1 sample of `tests/events/samples.py` is frozen on purpose: it is what the journal has.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from quack.events.event_types import TurnCommitted
from quack.events.records import PlanUpdateRecord
from quack.events.registry import SPECS, validate_payload
from tests.events.samples import sample_payload, uid

V1_PLAN = {
    "completed_steps": ["recalled the rule"],
    "current_step": "practice",
    "next_steps": ["review"],
    "branches": [{"description": "why", "type": "curiosity"}],
    "cancel_request_ids": [uid("request")],
}
V2_PLAN: dict[str, Any] = {
    "goal": "learn the ratio test",
    "current_step": {
        "id": "practice",
        "text": "practice",
        "landmark": {"kind": "skill", "id": uid("skill")},
    },
    "next_steps": [{"id": "review", "text": "review", "landmark": None}],
    "landmark_edits": {
        "add": [
            {"kind": "skill", "id": uid("skill"), "before_kind": "topic", "before_id": uid("topic")}
        ],
        "remove": [{"kind": "topic", "id": uid("topic")}],
    },
    "branches": [],
    "cancel_request_ids": [],
}


def _turn(plan: dict[str, Any]) -> dict[str, Any]:
    payload = sample_payload("turn.committed")
    payload["markup"]["plan_update"] = plan
    return payload


def test_both_versions_are_registered_with_the_same_flags() -> None:
    v1, v2 = SPECS[("turn.committed", 1)], SPECS[("turn.committed", 2)]
    assert (v1.projector, v1.dispatcher) == (v2.projector, v2.dispatcher) == (True, True)


def test_a_version_1_event_is_read_in_the_new_form() -> None:
    """The frozen sample is the old form; reading it gives the plan of version 2: no goal, the
    step is its own text, no landmarks."""
    sample = sample_payload("turn.committed")
    assert "completed_steps" in sample["markup"]["plan_update"]
    plan = validate_payload("turn.committed", sample, 1)
    assert isinstance(plan, TurnCommitted)
    update = plan.markup.plan_update
    assert update.goal is None and update.landmark_edits is None
    assert update.current_step is not None
    assert (update.current_step.id, update.current_step.text) == ("practice", "practice")
    assert update.current_step.landmark is None
    assert [s.id for s in update.next_steps or []] == ["review"]


def test_the_old_plan_keeps_branches_and_cancelled_requests() -> None:
    update = PlanUpdateRecord.model_validate(V1_PLAN)
    assert [(b.description, b.type) for b in update.branches] == [("why", "curiosity")]
    assert [str(r) for r in update.cancel_request_ids] == [uid("request")]


def test_a_version_2_event_round_trips_and_null_is_no_change() -> None:
    payload = validate_payload("turn.committed", _turn(V2_PLAN), 2)
    assert isinstance(payload, TurnCommitted)
    assert payload.model_dump(mode="json")["markup"]["plan_update"] == V2_PLAN
    quiet = {**V2_PLAN, "goal": None, "current_step": None, "next_steps": None}
    quiet["landmark_edits"] = None
    update = PlanUpdateRecord.model_validate(quiet)
    assert update.current_step is None and update.next_steps is None


def test_step_ids_are_not_checked_by_the_record() -> None:
    """The slug is the guard's rule; the journal holds the free text of old steps as ids."""
    plan = {**V2_PLAN, "current_step": {"id": "Recall the ratio!", "text": "t", "landmark": None}}
    assert PlanUpdateRecord.model_validate(plan).current_step is not None


@pytest.mark.parametrize("missing", ["goal", "current_step", "next_steps", "landmark_edits"])
def test_a_version_2_plan_names_every_field(missing: str) -> None:
    plan = dict(V2_PLAN)
    plan.pop(missing)
    with pytest.raises(ValidationError):
        PlanUpdateRecord.model_validate(plan)
