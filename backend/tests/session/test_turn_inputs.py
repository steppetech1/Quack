"""Inputs of the teacher context read by stream B (ТЗ Ф4 WP14.1; Т-§12.5, Т-§17.8): the plan
and the learner skill. D-B-174."""

from __future__ import annotations

import json
from uuid import uuid4

from quack.db import Db
from quack.events.records import PlanUpdateRecord
from quack.graph.read import ProfileFacts
from quack.session.teacher_context import LearnerSkill
from quack.session.turn_inputs import (
    NO_DATA,
    learned_lines,
    learner_state,
    load_learner_skill,
    load_plan,
    load_snapshot,
)
from tests.db.fixtures import create_learner
from tests.support import graph_read as fx

DOMAIN = uuid4()
OTHER_DOMAIN = uuid4()


async def test_plan_and_learner_skill(pg_db: Db) -> None:
    """Slot 6 — the plan document of the session; `/name` — the last version of the skill of
    that name; nothing of another learner."""
    learner_id = await create_learner(pg_db)
    stranger = await create_learner(pg_db)
    session_id = uuid4()
    assert await load_plan(pg_db, learner_id=learner_id, session_id=session_id) is None
    plan = PlanUpdateRecord(
        completed_steps=["idea"],
        current_step="example",
        next_steps=["task"],
        branches=[],
        cancel_request_ids=[],
    )
    await pg_db.execute(
        "INSERT INTO content.session_plans (session_id, learner_id, plan, status) "
        "VALUES (%(s)s, %(l)s, %(p)s, 'active')",
        {"s": session_id, "l": learner_id, "p": json.dumps(plan.model_dump(mode="json"))},
    )
    assert await load_plan(pg_db, learner_id=learner_id, session_id=session_id) == plan
    assert await load_plan(pg_db, learner_id=stranger, session_id=session_id) is None

    skill_id = uuid4()
    await pg_db.executemany(
        "INSERT INTO content.learner_skills (skill_id, version, learner_id, name, instruction) "
        "VALUES (%(s)s, %(v)s, %(l)s, 'quiz', %(i)s)",
        [
            {"s": skill_id, "v": 1, "l": learner_id, "i": "three questions"},
            {"s": skill_id, "v": 2, "l": learner_id, "i": "five questions"},
        ],
    )
    found = await load_learner_skill(pg_db, learner_id=learner_id, name="quiz")
    assert found == LearnerSkill(name="quiz", text="five questions")
    assert await load_learner_skill(pg_db, learner_id=learner_id, name="other") is None
    assert await load_learner_skill(pg_db, learner_id=stranger, name="quiz") is None


def test_learned_lines_name_no_data() -> None:
    """Т-§9.5, law 8: the learned part is said in words; too few samples is «no data yet»."""
    facts = ProfileFacts(
        learner_id=uuid4(),
        learned_helpful_approach="small_steps",
        learned_confidence_bias="over",
        learned_reasoning_share=0.4,
    )
    assert learned_lines(facts) == [
        "what helps: small steps",
        "confidence: overestimates own answers",
        f"corrective branches: {NO_DATA} of turns",
        "reasoning shown: 40% of answers",
    ]


async def test_learner_state_from_memory() -> None:
    """Slots 3 and 4 from S4: preferences, learned, manner; hubs with their topics."""
    state = await learner_state(fx.FixedGraphReader(), learner_id=fx.LEARNER, domain_id=fx.DOMAIN)
    assert state.profile.preferences["language"] == "ru"
    assert state.profile.manner == "short steps, no lectures"
    assert "what helps: small steps" in state.profile.learned
    (hub,) = state.hubs
    assert hub.hub_id == fx.HUB and hub.deadline_at_ms == fx.GOAL_DEADLINE_MS
    assert [t.topic_id for t in hub.topics] == [fx.TOPIC_READY, fx.TOPIC_OPEN]


async def test_snapshot_is_the_earliest_frozen_row(pg_db: Db) -> None:
    """D-A-137: the snapshot is the earliest manifest of the session with `frozen_slots`; rows
    without it and other sessions do not count."""
    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    assert await load_snapshot(pg_db, learner_id=learner_id, session_id=session_id) is None
    rows = [
        (session_id, None, "2026-11-03 10:00"),
        (session_id, {"profile": "first", "domain_and_goals": "g"}, "2026-11-03 10:01"),
        (session_id, {"profile": "later", "domain_and_goals": "g"}, "2026-11-03 10:02"),
        (uuid4(), {"profile": "other session", "domain_and_goals": "g"}, "2026-11-03 09:00"),
    ]
    await pg_db.executemany(
        "INSERT INTO ops.turn_manifests (manifest_id, learner_id, session_id, prompt_version, "
        "role, model, projected_seq, frozen_slots, created_at) VALUES (%(m)s, %(l)s, %(s)s, "
        "'teacher@2', 'teacher', 'm', 0, %(f)s, %(at)s)",
        [
            {
                "m": uuid4(),
                "l": learner_id,
                "s": s,
                "f": json.dumps(f) if f is not None else None,
                "at": at,
            }
            for s, f, at in rows
        ],
    )
    found = await load_snapshot(pg_db, learner_id=learner_id, session_id=session_id)
    assert found == {"profile": "first", "domain_and_goals": "g"}
