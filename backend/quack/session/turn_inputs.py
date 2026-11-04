"""Inputs of the teacher context read by stream B itself (ТЗ Ф4 WP14.1; Т-§12.5, Т-§17.8).
D-B-174.

- **Plan** (slot 6): the document `content.session_plans` of the session (WP13.6).
- **Learner skill** (Т-§17.8): `/name` gives the instruction of the last version of the skill of
  that name, read by own SQL (no import of `artifacts`, WP14.1).

- **Profile and goals** (slots 3, 4; Т-§9.5, Т-§8.5): read from memory by
  `GraphReader.profile` and `domain_goals` (S4, #170) and turned into the slot models; the
  learned part is named in words, «no data yet» while the code has too few samples (law 8).
- **Snapshot** of slots 3 and 4 (Т-§10.1): the text frozen on the first turn of the session,
  read from the earliest `ops.turn_manifests` row of the session with `frozen_slots` (D-A-137).
D-B-180.
"""

from __future__ import annotations

from uuid import UUID

from quack.db.db import Db
from quack.events.records import PlanUpdateRecord
from quack.graph.read import GraphReader, ProfileFacts
from quack.session.teacher_context import (
    GoalTopic,
    HubGoal,
    LearnerSkill,
    LearnerState,
    Profile,
)

_PLAN = (
    "SELECT plan FROM content.session_plans "
    "WHERE session_id = %(session_id)s AND learner_id = %(learner_id)s"
)
_LEARNER_SKILL = (
    "SELECT name, instruction FROM content.learner_skills "
    "WHERE learner_id = %(learner_id)s AND name = %(name)s ORDER BY version DESC LIMIT 1"
)
_SNAPSHOT = (
    "SELECT frozen_slots FROM ops.turn_manifests "
    "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s "
    "AND frozen_slots IS NOT NULL ORDER BY created_at, manifest_id LIMIT 1"
)

NO_DATA = "no data yet"
_BIAS_WORDS = {
    "over": "overestimates own answers",
    "under": "underestimates own answers",
    "calibrated": "rates own answers about right",
}


async def load_plan(db: Db, *, learner_id: UUID, session_id: UUID) -> PlanUpdateRecord | None:
    row = await db.fetch_one(_PLAN, {"learner_id": learner_id, "session_id": session_id})
    return None if row is None else PlanUpdateRecord.model_validate(row["plan"])


async def load_learner_skill(db: Db, *, learner_id: UUID, name: str) -> LearnerSkill | None:
    """`/name` of the action (`skill_invocation`); an unknown name gives nothing."""
    row = await db.fetch_one(_LEARNER_SKILL, {"learner_id": learner_id, "name": name})
    return None if row is None else LearnerSkill(name=row["name"], text=row["instruction"])


def _share(value: float | None) -> str:
    return NO_DATA if value is None else f"{round(value * 100)}%"


def learned_lines(facts: ProfileFacts) -> list[str]:
    """The learned part of slot 3 (Т-§9.5): every item named, «no data yet» while there are
    fewer samples than the code needs (absence is not zero, law 8)."""
    approach = facts.learned_helpful_approach
    bias = facts.learned_confidence_bias
    return [
        f"what helps: {approach.replace('_', ' ') if approach else NO_DATA}",
        f"confidence: {_BIAS_WORDS[bias] if bias else NO_DATA}",
        f"corrective branches: {_share(facts.learned_branch_rate)} of turns",
        f"reasoning shown: {_share(facts.learned_reasoning_share)} of answers",
    ]


async def learner_state(reader: GraphReader, *, learner_id: UUID, domain_id: UUID) -> LearnerState:
    """Profile and goals of the learner now (slots 3, 4), from memory (S4)."""
    facts = await reader.profile(learner_id)
    hubs = await reader.domain_goals(domain_id)
    return LearnerState(
        profile=Profile(preferences=facts.prefs, learned=learned_lines(facts), manner=facts.manner),
        hubs=[
            HubGoal(
                hub_id=hub.hub_id,
                name=hub.name,
                control_type=hub.control_type,
                deadline_at_ms=hub.deadline_at_ms,
                topics=[
                    GoalTopic(topic_id=t.topic_id, name=t.name, required_levels=t.required_levels)
                    for t in hub.topics
                ],
            )
            for hub in hubs
        ],
    )


async def load_snapshot(db: Db, *, learner_id: UUID, session_id: UUID) -> dict[str, str] | None:
    """The slots 3 and 4 frozen on the session; `None` — the session has no turn yet."""
    row = await db.fetch_one(_SNAPSHOT, {"learner_id": learner_id, "session_id": session_id})
    return None if row is None else dict(row["frozen_slots"])
