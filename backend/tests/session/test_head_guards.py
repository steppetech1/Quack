"""ТЗ Ф4 §8.9 T-TT4 (pure part): head and markup rules of Т-§12.4 (WP13.5) — honest verdict,
the turn is marked. «Do not jump the boundary» needs `REQUIRES` from memory (S4); the retry
before the body is shown is the turn graph (WP13.2)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from quack.config import Settings
from quack.graph.read.models import RequiresLink
from quack.llm.labels import LabelMap
from quack.practice.check import Verdict
from quack.session.guards import (
    Boundary,
    GuardViolation,
    drop_unknown_head_labels,
    drop_unknown_markup_labels,
    head_violations,
    markup_violations,
    settle_stance,
)
from quack.session.teacher_output import TurnHead, TurnMarkup

SKILLS = {"S1", "S2"}
MISCONCEPTIONS = {"M1"}


def _head(**fields: Any) -> TurnHead:
    base: dict[str, Any] = {
        "intent": "feedback",
        "targetSkills": ["S1"],
        "verdict_stance": None,
        "branchType": None,
        "topicLabel": None,
        "approach": "none",
    }
    return TurnHead.model_validate({**base, **fields})


def _verdict(verification: str) -> Verdict:
    return Verdict.model_validate(
        {
            "answer_seq": 4,
            "part": "main",
            "outcome": "incorrect",
            "method": "exact" if verification == "code" else "model",
            "verification": verification,
        }
    )


def _markup(**fields: Any) -> TurnMarkup:
    base: dict[str, Any] = {
        "tasks": [],
        "elements": [],
        "exposures": [],
        "targetsMisconception": None,
        "planUpdate": {
            "completed_steps": [],
            "current_step": "s",
            "next_steps": [],
            "branches": [],
            "cancel_requests": [],
        },
        "agentRequests": [],
        "correctionsAcknowledged": [],
        "decisionReason": "r",
        "deadline": None,
    }
    return TurnMarkup.model_validate({**base, **fields})


def _reasons(violations: list[GuardViolation]) -> list[str]:
    return [v.reason for v in violations]


@pytest.mark.parametrize(
    ("stance", "verdict", "ok"),
    [
        ("code", "code", True),
        ("code", "model", False),
        ("code", None, False),
        ("formative", "model", True),
        ("formative", "code", True),
        (None, None, True),
    ],
)
def test_honest_verdict(stance: str | None, verdict: str | None, ok: bool) -> None:
    violations = head_violations(
        _head(verdict_stance=stance),
        answered_verdict=None if verdict is None else _verdict(verdict),
        skill_labels=SKILLS,
    )
    assert _reasons(violations) == ([] if ok else ["stance_without_code_verdict"])


def test_turn_without_target_skills_is_not_marked() -> None:
    violations = head_violations(_head(targetSkills=[]), answered_verdict=None, skill_labels=SKILLS)
    assert _reasons(violations) == ["no_target_skills"]
    split = _head(targetSkills=[], intent="split_topic", topicLabel="T1")  # the topic is the object
    assert head_violations(split, answered_verdict=None, skill_labels=SKILLS) == []


def test_domain_without_skills_may_have_no_target_skills() -> None:
    """No skills in the label table: nothing to target, so an empty list is fine and a hub
    label in it is told to leave the list empty."""
    assert head_violations(_head(targetSkills=[]), answered_verdict=None, skill_labels=set()) == []
    [violation] = head_violations(
        _head(targetSkills=["H1"]), answered_verdict=None, skill_labels=set()
    )
    assert violation.reason == "unknown_skill_label" and "leave the list empty" in violation.detail


def test_unknown_labels_are_dropped_after_the_retries() -> None:
    head = drop_unknown_head_labels(_head(targetSkills=["S1", "H1"]), SKILLS)
    assert head.target_skills == ["S1"]
    same = _head(targetSkills=["S2"])
    assert drop_unknown_head_labels(same, SKILLS) is same
    markup = _markup(
        exposures=[
            {"skill": "S2", "kind": "explained", "approach": "none"},
            {"skill": "S7", "kind": "example", "approach": "none"},
        ],
        targetsMisconception="M5",
    )
    settled = drop_unknown_markup_labels(
        markup, skill_labels=SKILLS, misconception_labels=MISCONCEPTIONS
    )
    assert [e.skill for e in settled.exposures] == ["S2"] and settled.targets_misconception is None
    assert (
        markup_violations(settled, skill_labels=SKILLS, misconception_labels=MISCONCEPTIONS) == []
    )


def test_unknown_target_skill_label() -> None:
    violations = head_violations(
        _head(targetSkills=["S1", "S9"]), answered_verdict=None, skill_labels=SKILLS
    )
    assert _reasons(violations) == ["unknown_skill_label"]
    assert "S9" in violations[0].detail


def test_markup_labels_must_exist() -> None:
    markup = _markup(
        exposures=[
            {"skill": "S2", "kind": "explained", "approach": "none"},
            {"skill": "S7", "kind": "example", "approach": "none"},
        ],
        targetsMisconception="M5",
    )
    violations = markup_violations(markup, skill_labels=SKILLS, misconception_labels=MISCONCEPTIONS)
    assert _reasons(violations) == ["unknown_skill_label", "unknown_misconception_label"]


def test_marked_markup_passes() -> None:
    markup = _markup(
        exposures=[{"skill": "S1", "kind": "explained", "approach": "none"}],
        targetsMisconception="M1",
    )
    assert markup_violations(markup, skill_labels=SKILLS, misconception_labels=MISCONCEPTIONS) == []


@pytest.mark.parametrize(
    ("stance", "verdict", "settled"),
    [
        ("code", None, "formative"),
        ("code", "model", "formative"),
        ("code", "code", "code"),
        ("formative", "code", "formative"),  # code never raises the stance
        (None, "code", None),
    ],
)
def test_settle_stance_only_lowers(
    stance: str | None, verdict: str | None, settled: str | None
) -> None:
    head = settle_stance(
        _head(verdict_stance=stance), None if verdict is None else _verdict(verdict)
    )
    assert head.verdict_stance == settled


# --- «do not jump the boundary» (Т-§12.4, WP13.5; S4 `RequiresLink`, D-B-153) ---

PRE, PRE2, TARGET = uuid4(), uuid4(), uuid4()


def _boundary(*links: RequiresLink, min_confidence: float = 0.6) -> Boundary:
    return Boundary(
        requires={"S1": links},
        skill_ids={"S1": TARGET, "S2": PRE},
        min_confidence=min_confidence,
    )


@pytest.mark.parametrize(
    ("link", "targets", "ok"),
    [
        (RequiresLink(skill_id=PRE, confidence=0.9), ["S1"], False),
        (RequiresLink(skill_id=PRE, confidence=0.9, has_exposure=True), ["S1"], True),
        (RequiresLink(skill_id=PRE, confidence=0.9, has_evidence=True), ["S1"], True),
        (RequiresLink(skill_id=PRE, confidence=0.5), ["S1"], True),
        (RequiresLink(skill_id=PRE, confidence=0.6), ["S1"], False),
        (RequiresLink(skill_id=PRE, confidence=0.9), ["S1", "S2"], True),
    ],
    ids=["jump", "shown", "evidenced", "unsure-link", "at-threshold", "turn-takes-it"],
)
def test_boundary(link: RequiresLink, targets: list[str], ok: bool) -> None:
    """Every sure prerequisite (`confidence >= requires_min_confidence`) of a target skill has
    a showing or evidence (the slice reads them with the session overlay), or this turn
    takes it in `targetSkills`; unsure links are a warning in the context, not a rule."""
    violations = head_violations(
        _head(targetSkills=targets),
        answered_verdict=None,
        skill_labels=SKILLS,
        boundary=_boundary(link),
    )
    assert _reasons(violations) == ([] if ok else ["boundary_jump"])


def test_boundary_names_the_missing_prerequisites_once() -> None:
    links = (
        RequiresLink(skill_id=PRE, confidence=0.9),
        RequiresLink(skill_id=PRE2, confidence=0.8),
        RequiresLink(skill_id=PRE, confidence=0.7),
    )
    [violation] = head_violations(
        _head(), answered_verdict=None, skill_labels=SKILLS, boundary=_boundary(*links)
    )
    assert violation.reason == "boundary_jump"
    # The model sees labels, never ids (Т-§4.5): PRE2 has no label and is only counted.
    assert str(PRE) not in violation.detail and str(PRE2) not in violation.detail
    assert "S1 needs S2 and 1 unlabeled first" in violation.detail
    assert violation.detail.count("S2") == 1


# --- every label the write resolves (Т-§12.4 «короткие метки существуют», WP13.5) ---


def _label_table() -> LabelMap:
    return LabelMap.build(
        Settings(env="test"),
        skills=[uuid4()],
        topics=[uuid4()],
        hubs=[uuid4()],
        chunks=[uuid4()],
        requests=[uuid4()],
        tasks=[uuid4()],
    )


def _request(**params: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "skill": None,
        "level": None,
        "topic": None,
        "task": None,
        "misconception": None,
        "description": "d",
    }
    return {"kind": "viz", "params": {**base, **params}, "needed_in_session": True}


def _plan(*cancel: str) -> dict[str, Any]:
    return {
        "completed_steps": [],
        "current_step": "s",
        "next_steps": [],
        "branches": [],
        "cancel_requests": list(cancel),
    }


def _deadline(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "hub_label": None,
        "new_hub_name": "Exam",
        "deadline_at": "2026-12-01T09:00:00+00:00",
        "coverage": [],
        "quote": "q",
        "control_type": None,
    }
    return {**base, **fields}


def _link(element_id: str, chunk: str) -> dict[str, Any]:
    return {"element_id": element_id, "kind": "material_link", "chunk": chunk, "page": None}


def _check(markup: TurnMarkup, labels: LabelMap) -> list[GuardViolation]:
    return markup_violations(markup, skill_labels={"S1"}, misconception_labels=set(), labels=labels)


def test_labels_of_requests_cancels_elements_and_deadline_must_exist() -> None:
    """`cancel_requests: ["R9"]` used to pass the guard and fail the write
    (`UnknownLabelError`, no turn in the journal); now it is a violation that the retry
    names, and the same for the other places the write turns a label into an id."""
    labels = _label_table()
    clean = _markup(
        planUpdate=_plan("R1"),
        agentRequests=[_request(skill="S1", topic="T1", task="K1")],
        elements=[_link("e2", "P1")],
        deadline=_deadline(hub_label="H1", new_hub_name=None, coverage=["T1"]),
    )
    assert _check(clean, labels) == []
    assert _check(_markup(), labels) == []

    cases = [
        (_markup(planUpdate=_plan("R9")), "R9"),
        (_markup(agentRequests=[_request(topic="T9")]), "T9"),
        (_markup(agentRequests=[_request(task="K9")]), "K9"),
        # no card has a label in this call
        (_markup(elements=[{"element_id": "e2", "kind": "card_series", "cards": ["C1"]}]), "C1"),
        (
            _markup(
                elements=[
                    {
                        "element_id": "e2",
                        "kind": "visualization",
                        "request": "R9",
                        "plot": None,
                        "is_task": False,
                    }
                ]
            ),
            "R9",
        ),
        (_markup(elements=[_link("e2", "P9")]), "P9"),
        (_markup(deadline=_deadline(hub_label="H9", new_hub_name=None)), "H9"),
        (_markup(deadline=_deadline(coverage=["T7"])), "T7"),
    ]
    for markup, label in cases:
        [violation] = _check(markup, labels)
        assert violation.reason == "unknown_label" and label in violation.detail


def test_unknown_request_cancel_element_and_deadline_labels_are_dropped() -> None:
    labels = _label_table()
    markup = _markup(
        planUpdate=_plan("R1", "R9"),
        agentRequests=[_request(topic="T1"), _request(topic="T9")],
        elements=[_link("e2", "P1"), _link("e3", "P9")],
        deadline=_deadline(coverage=["T1", "T7"]),
    )
    settled = drop_unknown_markup_labels(
        markup, skill_labels={"S1"}, misconception_labels=set(), labels=labels
    )
    assert settled.plan_update.cancel_requests == ["R1"]
    assert [r.params.topic for r in settled.agent_requests] == ["T1"]
    assert [e.element_id for e in settled.elements] == ["e2"]
    assert settled.deadline is not None and settled.deadline.coverage == ["T1"]
    assert _check(settled, labels) == []

    unknown_hub = _markup(deadline=_deadline(hub_label="H9", new_hub_name=None))
    dropped = drop_unknown_markup_labels(
        unknown_hub, skill_labels={"S1"}, misconception_labels=set(), labels=labels
    )
    assert dropped.deadline is None
