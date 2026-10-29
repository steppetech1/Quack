"""Phase C: pure topic aggregates and displayState (spec §4.3 step 5, Т-§8.6, Т-§22.2)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from quack.config import FoldParams
from quack.graph.types import Props

type DisplayState = Literal["locked", "available", "in_progress", "solid", "rusty"]

INACTIVE_SKILL_STATUSES = frozenset({"archived", "merged"})
INACTIVE_TOPIC_STATUSES = frozenset({"archived", "merged", "skipped"})


@dataclass(frozen=True)
class SkillSnapshot:
    skill_id: str
    status: str
    p_mastery: float
    confidence: float
    evidence_count: int
    exposure_count: int
    mastery_status: str
    next_review_at: int
    last_practiced_at: int


@dataclass(frozen=True)
class PrereqSnapshot:
    topic_id: str
    status: str
    mastery: float


@dataclass(frozen=True)
class TopicDisplayResult:
    mastery: float
    coverage: float
    confidence: float
    hub_score: float
    nearest_review_at: int
    display_state: DisplayState
    skill_count: int

    def to_props(self) -> Props:
        return {
            "mastery": self.mastery,
            "coverage": self.coverage,
            "confidence": self.confidence,
            "hubScore": self.hub_score,
            "nearestReviewAt": self.nearest_review_at,
            "displayState": self.display_state,
            "skillCount": self.skill_count,
        }


def recompute_topic_display(
    skills: Sequence[SkillSnapshot],
    dependents_count: Mapping[str, int],
    prereqs: Sequence[PrereqSnapshot],
    as_of_ms: int,
    params: FoldParams,
) -> TopicDisplayResult:
    """`dependents_count[skill_id]` = number of DEPENDS_ON rels pointing at the skill."""
    active = sorted(
        (s for s in skills if s.status not in INACTIVE_SKILL_STATUSES), key=lambda s: s.skill_id
    )
    hubs = [dependents_count.get(s.skill_id, 0) for s in active]
    weights = [1.0 + params.hub_weight_factor * h for h in hubs]
    if active:
        total = sum(weights)
        mastery = sum(w * s.p_mastery for w, s in zip(weights, active, strict=True)) / total
        confidence = sum(w * s.confidence for w, s in zip(weights, active, strict=True)) / total
        coverage = sum(1 for s in active if s.evidence_count > 0) / len(active)
    else:
        mastery = confidence = coverage = 0.0
    due = [s.next_review_at for s in active if s.next_review_at > 0]
    nearest_review = min(due) if due else 0
    has_activity = any(s.evidence_count > 0 or s.exposure_count > 0 for s in active)
    struggling = any(s.mastery_status == "struggling" for s in active)

    state: DisplayState
    if (
        active
        and mastery >= params.topic_solid_mastery
        and coverage >= params.topic_solid_coverage
        and not struggling
    ):
        # "Time to review" (Т-§8.6): the due date of a topic skill has passed at as_of.
        state = "rusty" if 0 < nearest_review < as_of_ms else "solid"
    elif has_activity:
        state = "in_progress"
    elif any(
        p.status not in INACTIVE_TOPIC_STATUSES and p.mastery < params.prereq_unlock_mastery
        for p in prereqs
    ):
        state = "locked"
    else:
        state = "available"
    return TopicDisplayResult(
        mastery=mastery,
        coverage=coverage,
        confidence=confidence,
        hub_score=float(sum(hubs)),
        nearest_review_at=nearest_review,
        display_state=state,
        skill_count=len(active),
    )
