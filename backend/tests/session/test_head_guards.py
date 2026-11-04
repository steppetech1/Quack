"""ТЗ Ф4 §8.9 T-TT4 (pure part): head and markup rules of Т-§12.4 (WP13.5) — honest verdict,
the turn is marked. «Do not jump the boundary» needs `REQUIRES` from memory (S4); the retry
before the body is shown is the turn graph (WP13.2)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from quack.graph.read.models import RequiresLink
from quack.practice.check import Verdict
from quack.session.guards import (
    Boundary,
    GuardViolation,
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
