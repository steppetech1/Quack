"""The action graph of a session, the read side (owner decision 2026-11-19): the landmarks the
code builds from memory (`quack.session.plan_view`) and `GET /v1/sessions/{id}/plan`.

The calculation is pure and tested without infrastructure; the route needs Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from neo4j import AsyncDriver
from psycopg.types.json import Jsonb
from redis.asyncio import Redis

from quack.api.app import create_app
from quack.api.auth import SESSION_COOKIE
from quack.auth.sessions import open_session
from quack.config import Settings
from quack.db.db import Db
from quack.events.log import NewEvent, append_events
from quack.events.records import LandmarkRefRecord
from quack.graph.projection.runner import project_learner
from quack.graph.read import (
    HubGoal,
    HubTopicGoal,
    LevelFact,
    MemorySlice,
    RequiresLink,
    SkillFact,
    SliceFocus,
    TopicMemory,
)
from quack.practice.session_plan import lift_plan
from quack.session.plan_view import (
    TopicSource,
    apply_edits,
    build_points,
    view_of,
    visited_landmarks,
    visited_steps,
)
from quack.session.sessions import StartSession, start_session
from tests.support.broken import BrokenNeo4jDriver

pytest_plugins = ["tests.session.fixtures"]


# --- data -----------------------------------------------------------------------------------


def _sid(n: int) -> UUID:
    return UUID(int=0x5100 + n)


def _tid(n: int) -> UUID:
    return UUID(int=0x7100 + n)


def _skill(
    n: int, *, level3: str = "not_tested", needs: Sequence[int] = (), name: str | None = None
) -> SkillFact:
    return SkillFact(
        skill_id=_sid(n),
        name=name or f"skill {n}",
        levels=[LevelFact(level=3, status=level3)],  # type: ignore[arg-type]
        requires=[RequiresLink(skill_id=_sid(k), confidence=0.9) for k in needs],
    )


def _topic(n: int, skills: Sequence[SkillFact], levels: Sequence[int] = (3,)) -> TopicSource:
    return TopicSource(_tid(n), f"topic {n}", list(levels), list(skills))


def _settings(limit: int = 7) -> Settings:
    return Settings(plan_landmarks_max=limit)


def _ids(points: Sequence[Any]) -> list[UUID]:
    return [p.id for p in points]


# --- the calculation (no infrastructure) ----------------------------------------------------


def test_hub_skills_follow_requires_and_levels_set_status() -> None:
    """The order is by `REQUIRES` (a prerequisite first, whatever the listing order), `done` is
    the required level proven or implied, the skill of the current step is `current`."""
    skills = [
        _skill(3, needs=[2]),
        _skill(2, level3="proven", needs=[1]),
        _skill(1, level3="implied"),
        _skill(4, level3="failed"),
    ]
    points = build_points([_topic(1, skills)], group_by_topic=True, settings=_settings())
    assert _ids(points) == [_sid(1), _sid(2), _sid(3), _sid(4)]
    assert [p.reached for p in points] == [True, True, False, False]
    shown = view_of(points, ("skill", _sid(3)))
    assert [m.status for m in shown] == ["done", "done", "current", "upcoming"]
    assert [m.title for m in shown][:2] == ["skill 1", "skill 2"]
    assert not any(m.added_by_teacher for m in shown)


def test_required_levels_of_the_topic_decide_done() -> None:
    """Every required level counts: level 3 proven is not enough for a goal of levels 3 and 4."""
    skill = SkillFact(
        skill_id=_sid(1),
        name="s",
        levels=[LevelFact(level=3, status="proven"), LevelFact(level=4, status="not_tested")],
    )
    (point,) = build_points(
        [_topic(1, [skill], levels=[3, 4])], group_by_topic=True, settings=_settings()
    )
    assert not point.reached
    (done,) = build_points(
        [_topic(1, [skill], levels=[3])], group_by_topic=True, settings=_settings()
    )
    assert done.reached


def test_a_requires_cycle_does_not_hang_and_weak_links_do_not_order() -> None:
    cyclic = [_skill(1, needs=[2]), _skill(2, needs=[1])]
    assert len(build_points([_topic(1, cyclic)], group_by_topic=False, settings=_settings())) == 2
    weak = SkillFact(
        skill_id=_sid(1), name="a", requires=[RequiresLink(skill_id=_sid(2), confidence=0.1)]
    )
    ordered = build_points(
        [_topic(1, [weak, _skill(2)])], group_by_topic=False, settings=_settings()
    )
    assert _ids(ordered) == [_sid(1), _sid(2)]  # a link below `requires_min_confidence` is ignored


def test_topic_session_uses_skills_of_the_topic_and_the_default_level() -> None:
    skills = [_skill(i) for i in range(1, 4)]
    points = build_points([_topic(1, skills)], group_by_topic=False, settings=_settings())
    assert [p.kind for p in points] == ["skill"] * 3


def test_no_goal_has_no_landmarks() -> None:
    assert build_points([], group_by_topic=True, settings=_settings()) == []
    assert view_of([], ("skill", _sid(1))) == []


def test_more_skills_than_the_limit_in_a_topic_take_the_nearest_unreached() -> None:
    """Ten skills, limit 4 (a topic session): the reached ones go first out, the order stays;
    the point of the current step stays even when it is reached."""
    skills = [_skill(i, level3="proven" if i <= 4 else "not_tested", needs=[i - 1] if i > 1 else [])
              for i in range(1, 11)]  # fmt: skip
    topics = [_topic(1, skills)]
    points = build_points(topics, group_by_topic=False, settings=_settings(4))
    assert _ids(points) == [_sid(5), _sid(6), _sid(7), _sid(8)]
    kept = build_points(
        topics, group_by_topic=False, settings=_settings(4), current=("skill", _sid(2))
    )
    assert _ids(kept) == [_sid(2), _sid(5), _sid(6), _sid(7)]
    assert [m.status for m in view_of(kept, ("skill", _sid(2)))][:2] == ["current", "upcoming"]


def test_more_skills_than_the_limit_in_a_hub_group_by_topic() -> None:
    """A hub of three topics, nine skills, limit 7: the landmarks are the topics, in the order
    of the links between their skills; a topic is `done` when all its skills are; the current
    skill makes its topic current."""
    one = [_skill(i, level3="proven") for i in (1, 2, 3)]
    two = [_skill(i, needs=[3]) for i in (4, 5, 6)]
    three = [_skill(i, needs=[6]) for i in (7, 8, 9)]
    topics = [_topic(3, three), _topic(1, one), _topic(2, two)]
    points = build_points(topics, group_by_topic=True, settings=_settings(7))
    assert [(p.kind, p.id) for p in points] == [("topic", _tid(i)) for i in (1, 2, 3)]
    assert [p.reached for p in points] == [True, False, False]
    shown = view_of(points, ("skill", _sid(5)))
    assert [m.status for m in shown] == ["done", "current", "upcoming"]
    assert [m.title for m in shown] == ["topic 1", "topic 2", "topic 3"]
    # Still more topics than the limit: the nearest not reached ones.
    capped = build_points(topics, group_by_topic=True, settings=_settings(2))
    assert _ids(capped) == [_tid(2), _tid(3)]


def test_teacher_edits_add_and_remove_landmarks() -> None:
    points = build_points(
        [_topic(1, [_skill(1), _skill(2), _skill(3)])], group_by_topic=False, settings=_settings()
    )
    extra = build_points(
        [_topic(2, [_skill(9, name="foundation")])], group_by_topic=False, settings=_settings()
    )[0]
    doc = lift_plan(
        {
            "v": 2,
            "landmark_edits": {
                "added": [
                    {
                        "kind": "skill",
                        "id": str(_sid(9)),
                        "before_kind": "skill",
                        "before_id": str(_sid(2)),
                    },
                    {"kind": "skill", "id": str(uuid4()), "before_kind": None, "before_id": None},
                ],
                "removed": [{"kind": "skill", "id": str(_sid(3))}],
            },
        }
    )
    edited = apply_edits(points, doc, {("skill", _sid(9)): extra})
    # The unknown added landmark is dropped, the removed one is gone, the added goes before #2.
    assert _ids(edited) == [_sid(1), _sid(9), _sid(2)]
    shown = view_of(edited, None)
    assert [m.added_by_teacher for m in shown] == [False, True, False]
    assert shown[1].title == "foundation"
    # An added landmark without `before` goes to the end; adding twice does not duplicate.
    tail = lift_plan(
        {"v": 2, "landmark_edits": {"added": [{"kind": "skill", "id": str(_sid(9))}] * 2}}
    )
    assert _ids(apply_edits(points, tail, {("skill", _sid(9)): extra}))[-1] == _sid(9)
    assert len(apply_edits(points, tail, {("skill", _sid(9)): extra})) == 4


def test_parse_v2_document() -> None:
    step = {
        "id": "limits-intro",
        "text": "Limits",
        "landmark": {"kind": "skill", "id": str(_sid(1))},
    }
    doc = lift_plan(
        {
            "v": 2,
            "goal": "Pass the exam",
            "current_step": step,
            "next_steps": [{"id": "n1", "text": "Next", "landmark": None}, "broken", {"id": 1}],
            "landmark_edits": {"added": [], "removed": []},
        }
    )
    assert doc.goal == "Pass the exam"
    assert doc.current_step is not None
    assert doc.current_step.landmark == LandmarkRefRecord(kind="skill", id=_sid(1))
    assert [s.id for s in doc.next_steps] == ["n1"]  # a malformed step is skipped, not a 500


def test_parse_old_document_has_no_goal_and_no_edits() -> None:
    """The form before the owner decision: no `v`, steps are strings, no landmarks."""
    doc = lift_plan(
        {
            "completed_steps": ["a"],
            "current_step": "Solve the first task",
            "next_steps": ["Second", "", "Third"],
            "branches": [],
        }
    )
    assert doc.goal is None
    assert doc.current_step is not None
    assert (doc.current_step.id, doc.current_step.text, doc.current_step.landmark) == (
        "Solve the first task",
        "Solve the first task",
        None,
    )
    assert [s.text for s in doc.next_steps] == ["Second", "Third"]
    assert not doc.landmark_edits.added and not doc.landmark_edits.removed


@pytest.mark.parametrize("junk", [None, [], "x", 3, {"v": 2, "next_steps": "no"}])
def test_parse_junk_is_an_empty_plan(junk: object) -> None:
    doc = lift_plan(junk)
    assert doc.goal is None and doc.current_step is None and doc.next_steps == []


def test_topic_step_matches_first_unreached_or_first_skill_landmark() -> None:
    """Edge case A: when landmarks are skills, a `("topic", topic_id)` step picks the first
    unreached skill of that topic, or its first skill when all are reached."""
    t1_skills = [_skill(1, level3="proven"), _skill(2, needs=[1]), _skill(3, needs=[2])]
    t2_skills = [_skill(4, level3="proven"), _skill(5, level3="proven")]
    points = build_points(
        [_topic(1, t1_skills), _topic(2, t2_skills)],
        group_by_topic=True,
        settings=_settings(7),
    )
    assert [p.kind for p in points] == ["skill"] * 5

    # Topic 1 has reached skill 1, unreached skills 2 and 3 -> picks skill 2.
    shown = view_of(points, ("topic", _tid(1)))
    assert [(m.id, m.status) for m in shown[:3]] == [
        (_sid(1), "done"),
        (_sid(2), "current"),
        (_sid(3), "upcoming"),
    ]

    # Topic 2 has all skills reached -> picks its first skill (skill 4).
    all_done = view_of(points, ("topic", _tid(2)))
    assert [(m.id, m.status) for m in all_done[3:]] == [
        (_sid(4), "current"),
        (_sid(5), "done"),
    ]


def test_visited_landmarks_from_turns_mark_passed_points_done() -> None:
    """Edge case B: `visited_landmarks` folds `turn.committed` (v1 & v2) and `view_of` marks
    visited non-current points as `done`."""
    from tests.events.samples import sample_payload

    points = build_points(
        [_topic(1, [_skill(1), _skill(2, needs=[1]), _skill(3, needs=[2])])],
        group_by_topic=False,
        settings=_settings(),
    )
    turn1 = sample_payload("turn.committed")
    turn1["markup"]["plan_update"] = {
        "goal": None,
        "current_step": {
            "id": "s1",
            "text": "Step 1",
            "landmark": {"kind": "skill", "id": str(_sid(1))},
        },
        "next_steps": None,
        "landmark_edits": None,
        "branches": [],
        "cancel_request_ids": [],
    }
    turn2 = sample_payload("turn.committed")
    turn2["markup"]["plan_update"] = {
        "completed_steps": ["old"],
        "current_step": "Old step",
        "next_steps": [],
        "branches": [],
        "cancel_request_ids": [],
    }
    visited = visited_landmarks([turn1, turn2, {"markup": {"other": 1}}])
    assert visited == {("skill", _sid(1))}
    shown = view_of(points, ("skill", _sid(2)), visited=visited)
    assert [(m.id, m.status) for m in shown] == [
        (_sid(1), "done"),
        (_sid(2), "current"),
        (_sid(3), "upcoming"),
    ]


def test_visited_steps_collects_prior_steps_in_first_order_and_updates_details() -> None:
    """`visited_steps` folds v1 & v2 `turn.committed` payloads, keeps first-appearance order,
    updates `text`/`landmark` on repeats, and excludes `doc.current_step` and `doc.next_steps`."""
    from tests.events.samples import sample_payload

    def v2_turn(step_id: str, text: str, skill: int | None) -> dict[str, Any]:
        p = sample_payload("turn.committed")
        p["markup"]["plan_update"] = {
            "goal": None,
            "current_step": {
                "id": step_id,
                "text": text,
                "landmark": None if skill is None else {"kind": "skill", "id": str(_sid(skill))},
            },
            "next_steps": None,
            "landmark_edits": None,
            "branches": [],
            "cancel_request_ids": [],
        }
        return p

    v1_turn = sample_payload("turn.committed")
    v1_turn["markup"]["plan_update"] = {
        "completed_steps": ["old"],
        "current_step": "Warm up",
        "next_steps": [],
        "branches": [],
        "cancel_request_ids": [],
    }
    no_step_turn = sample_payload("turn.committed")
    no_step_turn["markup"]["plan_update"] = {
        "goal": None,
        "current_step": None,
        "next_steps": None,
        "landmark_edits": None,
        "branches": [],
        "cancel_request_ids": [],
    }

    doc = lift_plan(
        {
            "v": 2,
            "current_step": {"id": "s2", "text": "Step 2", "landmark": None},
            "next_steps": [{"id": "s3", "text": "Step 3", "landmark": None}],
        }
    )
    payloads = [
        v1_turn,
        v2_turn("s1", "First draft", 1),
        v2_turn("s2", "Step 2", 2),
        v2_turn("s1", "First updated", 3),
        v2_turn("s3", "Step 3", None),
        no_step_turn,
        {"markup": {"plan_update": "broken"}},
    ]
    done = visited_steps(payloads, doc)
    assert [(s.id, s.text, s.landmark_id, s.status) for s in done] == [
        ("Warm up", "Warm up", None, "done"),
        ("s1", "First updated", _sid(3), "done"),
    ]


# --- the route ------------------------------------------------------------------------------

HUB_ID, TOPIC_ID = uuid4(), uuid4()
SK_A, SK_B, SK_C = uuid4(), uuid4(), uuid4()
SK_EXTRA = uuid4()
LEVELS_3 = [3]


class _Reader:
    """The memory of the route tests: one hub with one topic of three skills (A proven, B needs
    A, C needs B) and one skill outside of it for the teacher's edits."""

    def __init__(self) -> None:
        self.skills = {
            SK_A: SkillFact(
                skill_id=SK_A, name="Limit", levels=[LevelFact(level=3, status="proven")]
            ),
            SK_B: SkillFact(
                skill_id=SK_B,
                name="Derivative",
                levels=[LevelFact(level=3, status="not_tested")],
                requires=[RequiresLink(skill_id=SK_A, confidence=0.9)],
            ),
            SK_C: SkillFact(
                skill_id=SK_C,
                name="Chain rule",
                requires=[RequiresLink(skill_id=SK_B, confidence=0.9)],
            ),
            SK_EXTRA: SkillFact(skill_id=SK_EXTRA, name="Fractions"),
        }

    async def domain_goals(self, domain_id: UUID) -> list[HubGoal]:
        topic = HubTopicGoal(topic_id=TOPIC_ID, name="Calculus", required_levels=LEVELS_3)
        return [HubGoal(hub_id=HUB_ID, name="Exam", kind="request", control_type="tasks",
                        topics=[topic])]  # fmt: skip

    async def topic_memory(self, topic_ids: Sequence[UUID]) -> list[TopicMemory]:
        if TOPIC_ID not in topic_ids:
            return []
        skills = [self.skills[k] for k in (SK_C, SK_B, SK_A)]  # listed against the order
        return [TopicMemory(topic_id=TOPIC_ID, name="Calculus", skills=skills)]

    async def slice_for_turn(
        self, learner_id: UUID, session_id: UUID, focus: SliceFocus, overlay: object = None
    ) -> MemorySlice:
        return MemorySlice(
            learner_id=learner_id,
            projected_seq=1,
            skills=[self.skills[s] for s in focus.skill_ids if s in self.skills],
        )


@pytest.fixture
async def app(
    settings: Settings, neo4j: AsyncDriver, redis: Redis, pg_db: Db
) -> AsyncIterator[FastAPI]:
    from tests.support.resources import build_app_resources

    yield create_app(settings, resources=build_app_resources(settings, neo4j, redis, db=pg_db))


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


@pytest.fixture(autouse=True)
def memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("quack.session.router.create_graph_reader", lambda *a, **k: _Reader())


async def _learner(
    pg_db: Db, settings: Settings, *, hub: bool = True, goal: bool = True
) -> tuple[UUID, UUID, dict[str, str]]:
    learner_id, domain_id = uuid4(), uuid4()
    await pg_db.execute(
        "INSERT INTO core.learners (learner_id, email) VALUES (%(l)s, %(e)s)",
        {"l": learner_id, "e": f"{learner_id}@test.invalid"},
    )
    await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="domain.created",
                payload={"domain_id": str(domain_id), "name": "Calculus"},
                actor="learner",
                idempotency_key=f"domain:{domain_id}",
            )
        ],
    )
    started = await start_session(
        pg_db,
        learner_id=learner_id,
        body=StartSession(
            domain_id=domain_id,
            intent="learn",
            hub_id=HUB_ID if hub and goal else None,
            topic_id=TOPIC_ID if not hub and goal else None,
            idempotency_key=uuid4(),
        ),
    )
    token = await open_session(pg_db, settings, learner_id=learner_id, now=datetime.now(UTC))
    return learner_id, started.session_id, {SESSION_COOKIE: token}


async def _store(pg_db: Db, learner_id: UUID, session_id: UUID, plan: dict[str, Any]) -> None:
    await pg_db.execute(
        "INSERT INTO content.session_plans (session_id, learner_id, plan, status) "
        "VALUES (%(s)s, %(l)s, %(p)s, 'active')",
        {"s": session_id, "l": learner_id, "p": Jsonb(plan)},
    )


async def test_plan_of_a_hub_session_with_a_v2_document(
    client: httpx.AsyncClient, pg_db: Db, settings: Settings
) -> None:
    learner, session, cookies = await _learner(pg_db, settings)
    await _store(
        pg_db,
        learner,
        session,
        {
            "v": 2,
            "goal": "Pass the calculus exam",
            "current_step": {
                "id": "derive",
                "text": "Derive x^2",
                "landmark": {"kind": "skill", "id": str(SK_B)},
            },
            "next_steps": [
                {
                    "id": "chain",
                    "text": "Chain rule",
                    "landmark": {"kind": "skill", "id": str(SK_C)},
                }
            ],
            "landmark_edits": {
                "added": [
                    {
                        "kind": "skill",
                        "id": str(SK_EXTRA),
                        "before_kind": "skill",
                        "before_id": str(SK_A),
                    }
                ],
                "removed": [],
            },
            "branches": [],
        },
    )
    seen = await client.get(f"/v1/sessions/{session}/plan", cookies=cookies)
    assert seen.status_code == 200, seen.text
    body = seen.json()
    assert body["goal"] == "Pass the calculus exam"
    assert [(m["title"], m["status"], m["added_by_teacher"]) for m in body["landmarks"]] == [
        ("Fractions", "upcoming", True),
        ("Limit", "done", False),
        ("Derivative", "current", False),
        ("Chain rule", "upcoming", False),
    ]
    # `done` counts the landmarks reached by the levels: Limit; Fractions has no level yet.
    assert body["progress"] == {"done": 1, "total": 4}
    assert body["done_steps"] == []
    assert body["current_step"] == {
        "id": "derive",
        "text": "Derive x^2",
        "landmark_id": str(SK_B),
        "status": "current",
    }
    assert body["next_steps"] == [
        {"id": "chain", "text": "Chain rule", "landmark_id": str(SK_C), "status": "upcoming"}
    ]
    assert body["updated_at"] is not None


async def test_plan_of_a_topic_session_and_of_an_old_document(
    client: httpx.AsyncClient, pg_db: Db, settings: Settings
) -> None:
    learner, session, cookies = await _learner(pg_db, settings, hub=False)
    await _store(
        pg_db,
        learner,
        session,
        {"completed_steps": ["a"], "current_step": "Warm up", "next_steps": ["Then this"]},
    )
    body = (await client.get(f"/v1/sessions/{session}/plan", cookies=cookies)).json()
    assert body["goal"] is None
    assert body["current_step"] == {
        "id": "Warm up",
        "text": "Warm up",
        "landmark_id": None,
        "status": "current",
    }
    assert [s["text"] for s in body["next_steps"]] == ["Then this"]
    assert [s["status"] for s in body["next_steps"]] == ["upcoming"]
    assert [m["status"] for m in body["landmarks"]] == ["done", "upcoming", "upcoming"]
    assert [m["kind"] for m in body["landmarks"]] == ["skill"] * 3


async def test_empty_plan_and_a_session_without_a_goal(
    client: httpx.AsyncClient, pg_db: Db, settings: Settings
) -> None:
    """No document and no hub or topic: 200 with nulls and empty lists, not a 404."""
    _, session, cookies = await _learner(pg_db, settings, goal=False)
    seen = await client.get(f"/v1/sessions/{session}/plan", cookies=cookies)
    assert seen.status_code == 200, seen.text
    assert seen.json() == {
        "goal": None,
        "landmarks": [],
        "done_steps": [],
        "current_step": None,
        "next_steps": [],
        "progress": None,
        "updated_at": None,
        "demos": [],
    }


async def test_plan_lists_the_demos_ordered_ahead_with_where_each_stands(
    client: httpx.AsyncClient, pg_db: Db, settings: Settings
) -> None:
    """A demo ordered for a step is a pair of events of its request: `agent.requested` with the
    step id, then `agent.completed {ok}` once the job finished. No second event — still `preparing`;
    `ok: false` — `failed`. Requests of the teacher (no step id) are not listed."""
    from quack.events.event_types import AgentCompleted, AgentRequested
    from quack.events.records import AgentParamsRecord

    learner, session, cookies = await _learner(pg_db, settings)
    ids = {name: uuid4() for name in ("ready", "failed", "preparing", "teacher")}
    events: list[NewEvent] = []
    for name, request_id in ids.items():
        params = AgentParamsRecord(
            description="demo",
            step_id=None if name == "teacher" else f"step-{name}",
            title=None if name == "ready" else f"Demo {name}",
        )
        events.append(
            NewEvent(
                type="agent.requested",
                payload=AgentRequested(
                    request_id=request_id, kind="viz", params=params, needed_in_session=False
                ),
                actor="viz_planner@1",
                idempotency_key=f"req:{request_id}",
                session_id=session,
            )
        )
    for name, ok in (("ready", True), ("failed", False)):
        events.append(
            NewEvent(
                type="agent.completed",
                payload=AgentCompleted(request_id=ids[name], kind="viz", ok=ok),
                actor="viz_builder@2",
                idempotency_key=f"done:{ids[name]}",
                session_id=session,
            )
        )
    # a step the planner turned down is an `agent.completed` of a job (no request id): not a demo
    events.append(
        NewEvent(
            type="agent.completed",
            payload=AgentCompleted(job_key="viz.plan.declined:s:step-no", kind="viz_plan", ok=True),
            actor="viz_planner@1",
            idempotency_key="viz.plan.declined:s:step-no",
            session_id=session,
        )
    )
    await append_events(pg_db, learner, events)

    seen = await client.get(f"/v1/sessions/{session}/plan", cookies=cookies)

    assert seen.status_code == 200, seen.text
    assert seen.json()["demos"] == [
        {
            "request_id": str(ids["ready"]),
            "step_id": "step-ready",
            "title": "step-ready",  # no title: the step id stands in
            "status": "ready",
        },
        {
            "request_id": str(ids["failed"]),
            "step_id": "step-failed",
            "title": "Demo failed",
            "status": "failed",
        },
        {
            "request_id": str(ids["preparing"]),
            "step_id": "step-preparing",
            "title": "Demo preparing",
            "status": "preparing",
        },
    ]


async def test_plan_of_another_learner_or_no_session_is_404(
    client: httpx.AsyncClient, pg_db: Db, settings: Settings
) -> None:
    learner, session, cookies = await _learner(pg_db, settings)
    await _store(pg_db, learner, session, {"v": 2, "goal": "secret"})
    _, _, other = await _learner(pg_db, settings)
    assert (await client.get(f"/v1/sessions/{session}/plan", cookies=other)).status_code == 404
    assert (await client.get(f"/v1/sessions/{uuid4()}/plan", cookies=cookies)).status_code == 404
    assert (await client.get(f"/v1/sessions/{session}/plan")).status_code == 401


async def test_plan_survives_a_neo4j_outage(
    pg_db: Db,
    settings: Settings,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Memory is down: the goal and the steps still come, no landmarks, no progress (law 9)."""
    from tests.support.resources import build_app_resources

    monkeypatch.undo()  # the real reader over a driver that is not there
    learner, session, cookies = await _learner(pg_db, settings)
    await _store(
        pg_db,
        learner,
        session,
        {"v": 2, "goal": "Pass", "current_step": {"id": "s", "text": "Step", "landmark": None}},
    )
    app = create_app(
        settings, resources=build_app_resources(settings, BrokenNeo4jDriver(), redis, db=pg_db)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        seen = await http.get(f"/v1/sessions/{session}/plan", cookies=cookies)
    assert seen.status_code == 200, seen.text
    body = seen.json()
    assert body["landmarks"] == [] and body["progress"] is None
    assert body["goal"] == "Pass" and body["current_step"]["id"] == "s"


async def test_plan_of_a_hub_session_with_real_neo4j(
    client: httpx.AsyncClient,
    pg_db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end against real Neo4j: projected hub, topic, skills with `REQUIRES`, and a proven
    level drive `GET /v1/sessions/{id}/plan` through `Neo4jGraphReader`."""
    monkeypatch.undo()
    learner_id, domain_id = uuid4(), uuid4()
    hub_id, topic_id, extra_topic_id = uuid4(), uuid4(), uuid4()
    sk_a, sk_b, sk_c, sk_extra = uuid4(), uuid4(), uuid4(), uuid4()
    task_id = uuid4()
    await pg_db.execute(
        "INSERT INTO core.learners (learner_id, email) VALUES (%(l)s, %(e)s)",
        {"l": learner_id, "e": f"{learner_id}@test.invalid"},
    )

    def ev(event_type: str, payload: dict[str, Any], key: str) -> NewEvent:
        return NewEvent(
            type=event_type,
            payload=payload,
            actor="code",
            idempotency_key=key,
            domain_id=domain_id,
        )

    skill_base = {
        "domain_id": str(domain_id),
        "summary": "s",
        "status": "active",
        "origin": "session",
    }
    stored = await append_events(
        pg_db,
        learner_id,
        [
            ev("domain.created", {"domain_id": str(domain_id), "name": "Calculus"}, "d"),
            ev(
                "topic.created",
                {
                    "topic_id": str(topic_id),
                    "domain_id": str(domain_id),
                    "name": "Differentiation",
                    "origin": "session",
                },
                "t1",
            ),
            ev(
                "topic.created",
                {
                    "topic_id": str(extra_topic_id),
                    "domain_id": str(domain_id),
                    "name": "Prerequisites",
                    "origin": "session",
                },
                "t2",
            ),
            # Create skills out of order so REQUIRES topological sort determines order: A -> B -> C.
            ev(
                "skill.created",
                skill_base
                | {"skill_id": str(sk_c), "topic_id": str(topic_id), "name": "Chain rule"},
                "sc",
            ),
            ev(
                "skill.created",
                skill_base
                | {"skill_id": str(sk_b), "topic_id": str(topic_id), "name": "Derivative"},
                "sb",
            ),
            ev(
                "skill.created",
                skill_base | {"skill_id": str(sk_a), "topic_id": str(topic_id), "name": "Limit"},
                "sa",
            ),
            ev(
                "skill.created",
                skill_base
                | {"skill_id": str(sk_extra), "topic_id": str(extra_topic_id), "name": "Fractions"},
                "sx",
            ),
            ev(
                "edge.added",
                {
                    "kind": "requires",
                    "from_id": str(sk_b),
                    "to_id": str(sk_a),
                    "origin": "model",
                    "confidence": 0.9,
                },
                "e1",
            ),
            ev(
                "edge.added",
                {
                    "kind": "requires",
                    "from_id": str(sk_c),
                    "to_id": str(sk_b),
                    "origin": "model",
                    "confidence": 0.9,
                },
                "e2",
            ),
            ev(
                "hub.created",
                {
                    "hub_id": str(hub_id),
                    "domain_id": str(domain_id),
                    "name": "Exam",
                    "kind": "request",
                    "origin": "learner",
                    "control_type": "tasks",
                },
                "h",
            ),
            ev(
                "hub.topics_changed",
                {
                    "hub_id": str(hub_id),
                    "topics": [{"topic_id": str(topic_id), "required_levels": [3]}],
                },
                "ht",
            ),
            ev(
                "task.created",
                {
                    "task_id": str(task_id),
                    "family_id": str(uuid4()),
                    "source": "teacher",
                    "verification": "unchecked",
                    "markup": {
                        "tests": [{"skill_id": str(sk_a), "level": 3}],
                        "method_named": False,
                        "answer": {"type": "number", "value": {"latex": "2", "sympy": "2"}},
                    },
                },
                "k",
            ),
            ev(
                "answer.submitted",
                {
                    "task_id": str(task_id),
                    "answer": {"kind": "number", "value": "2"},
                    "source": "chat",
                },
                "a",
            ),
        ],
    )
    await append_events(
        pg_db,
        learner_id,
        [
            ev(
                "verdict.recorded",
                {
                    "answer_seq": stored[-1].seq,
                    "part": "main",
                    "outcome": "correct",
                    "method": "exact",
                    "verification": "code",
                },
                "v",
            )
        ],
    )
    for _ in range(10):
        outcome = await project_learner(pg_db, neo4j, settings, learner_id)
        if outcome.status != "applied":
            assert outcome.status == "empty", outcome
            break

    started = await start_session(
        pg_db,
        learner_id=learner_id,
        body=StartSession(
            domain_id=domain_id,
            intent="learn",
            hub_id=hub_id,
            idempotency_key=uuid4(),
        ),
    )
    token = await open_session(pg_db, settings, learner_id=learner_id, now=datetime.now(UTC))
    cookies = {SESSION_COOKIE: token}
    await _store(
        pg_db,
        learner_id,
        started.session_id,
        {
            "v": 2,
            "goal": "Pass the calculus exam",
            "current_step": {
                "id": "derive",
                "text": "Derive x^2",
                "landmark": {"kind": "skill", "id": str(sk_b)},
            },
            "next_steps": [
                {
                    "id": "chain",
                    "text": "Chain rule",
                    "landmark": {"kind": "skill", "id": str(sk_c)},
                }
            ],
            "landmark_edits": {
                "added": [
                    {
                        "kind": "skill",
                        "id": str(sk_extra),
                        "before_kind": "skill",
                        "before_id": str(sk_a),
                    }
                ],
                "removed": [],
            },
            "branches": [],
        },
    )

    seen = await client.get(f"/v1/sessions/{started.session_id}/plan", cookies=cookies)
    assert seen.status_code == 200, seen.text
    body = seen.json()
    assert body["goal"] == "Pass the calculus exam"
    assert [(m["title"], m["status"], m["added_by_teacher"]) for m in body["landmarks"]] == [
        ("Fractions", "upcoming", True),
        ("Limit", "done", False),
        ("Derivative", "current", False),
        ("Chain rule", "upcoming", False),
    ]
    assert body["progress"] == {"done": 1, "total": 4}
    assert body["current_step"] == {
        "id": "derive",
        "text": "Derive x^2",
        "landmark_id": str(sk_b),
        "status": "current",
    }
    assert body["next_steps"] == [
        {"id": "chain", "text": "Chain rule", "landmark_id": str(sk_c), "status": "upcoming"}
    ]
    assert body["updated_at"] is not None


async def test_plan_progress_matches_done_landmarks_across_turns(
    client: httpx.AsyncClient, pg_db: Db, settings: Settings
) -> None:
    """`progress.done` always equals the count of `done` landmarks: a reached `current` landmark
    does not inflate `done`, while earlier turns' visited landmarks count as `done`."""
    from tests.events.samples import sample_payload

    learner, session, cookies = await _learner(pg_db, settings)
    # 1. SK_A is reached in memory AND is the current landmark -> status is `current`, done == 0.
    await _store(
        pg_db,
        learner,
        session,
        {
            "v": 2,
            "goal": "Pass",
            "current_step": {
                "id": "review-limit",
                "text": "Review limit",
                "landmark": {"kind": "skill", "id": str(SK_A)},
            },
            "next_steps": [],
            "landmark_edits": {"added": [], "removed": []},
            "branches": [],
        },
    )
    first = (await client.get(f"/v1/sessions/{session}/plan", cookies=cookies)).json()
    assert [(m["title"], m["status"]) for m in first["landmarks"]] == [
        ("Limit", "current"),
        ("Derivative", "upcoming"),
        ("Chain rule", "upcoming"),
    ]
    assert first["progress"] == {"done": 0, "total": 3}

    # 2. A turn visited SK_B (not yet proven in Neo4j), then the plan moves on to SK_C.
    turn_payload = sample_payload("turn.committed")
    turn_payload["markup"]["plan_update"] = {
        "goal": None,
        "current_step": {
            "id": "derive",
            "text": "Derivative",
            "landmark": {"kind": "skill", "id": str(SK_B)},
        },
        "next_steps": None,
        "landmark_edits": None,
        "branches": [],
        "cancel_request_ids": [],
    }
    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="turn.committed",
                payload=turn_payload,
                actor="teacher@3",
                idempotency_key="t1",
                session_id=session,
            )
        ],
    )
    await pg_db.execute(
        "UPDATE content.session_plans SET plan = %(p)s WHERE session_id = %(s)s",
        {
            "s": session,
            "p": Jsonb(
                {
                    "v": 2,
                    "goal": "Pass",
                    "current_step": {
                        "id": "chain",
                        "text": "Chain rule",
                        "landmark": {"kind": "skill", "id": str(SK_C)},
                    },
                    "next_steps": [],
                    "landmark_edits": {"added": [], "removed": []},
                    "branches": [],
                }
            ),
        },
    )
    second = (await client.get(f"/v1/sessions/{session}/plan", cookies=cookies)).json()
    assert [(m["title"], m["status"]) for m in second["landmarks"]] == [
        ("Limit", "done"),
        ("Derivative", "done"),
        ("Chain rule", "current"),
    ]
    assert second["progress"] == {"done": 2, "total": 3}
    assert second["done_steps"] == [
        {"id": "derive", "text": "Derivative", "landmark_id": str(SK_B), "status": "done"}
    ]


async def test_free_session_keeps_done_steps_with_demos_and_handles_revisit(
    client: httpx.AsyncClient, pg_db: Db, settings: Settings
) -> None:
    """In a free session (`landmarks == []`), past steps stay in `done_steps` together with
    their demos; returning to an earlier step makes it `current_step` and removes it from
    `done_steps`."""
    from quack.events.event_types import AgentCompleted, AgentRequested
    from quack.events.records import AgentParamsRecord
    from tests.events.samples import sample_payload

    learner, session, cookies = await _learner(pg_db, settings, goal=False)

    def turn_event(step_id: str, text: str, key: str) -> NewEvent:
        payload = sample_payload("turn.committed")
        payload["markup"]["plan_update"] = {
            "goal": None,
            "current_step": {"id": step_id, "text": text, "landmark": None},
            "next_steps": None,
            "landmark_edits": None,
            "branches": [],
            "cancel_request_ids": [],
        }
        return NewEvent(
            type="turn.committed",
            payload=payload,
            actor="teacher@3",
            idempotency_key=key,
            session_id=session,
        )

    demo_req = uuid4()
    await append_events(
        pg_db,
        learner,
        [
            turn_event("step-1", "Explore slope", "t1"),
            NewEvent(
                type="agent.requested",
                payload=AgentRequested(
                    request_id=demo_req,
                    kind="viz",
                    params=AgentParamsRecord(
                        description="slope viz", step_id="step-1", title="Slope graph"
                    ),
                    needed_in_session=False,
                ),
                actor="viz_planner@1",
                idempotency_key=f"req:{demo_req}",
                session_id=session,
            ),
            NewEvent(
                type="agent.completed",
                payload=AgentCompleted(request_id=demo_req, kind="viz", ok=True),
                actor="viz_builder@2",
                idempotency_key=f"done:{demo_req}",
                session_id=session,
            ),
            turn_event("step-2", "Tangent line", "t2"),
        ],
    )
    await _store(
        pg_db,
        learner,
        session,
        {
            "v": 2,
            "goal": "Understand derivatives",
            "current_step": {"id": "step-3", "text": "Derivative rule", "landmark": None},
            "next_steps": [],
            "landmark_edits": {"added": [], "removed": []},
            "branches": [],
        },
    )

    body = (await client.get(f"/v1/sessions/{session}/plan", cookies=cookies)).json()
    assert body["landmarks"] == []
    assert body["done_steps"] == [
        {"id": "step-1", "text": "Explore slope", "landmark_id": None, "status": "done"},
        {"id": "step-2", "text": "Tangent line", "landmark_id": None, "status": "done"},
    ]
    assert body["current_step"] == {
        "id": "step-3",
        "text": "Derivative rule",
        "landmark_id": None,
        "status": "current",
    }
    assert body["demos"] == [
        {
            "request_id": str(demo_req),
            "step_id": "step-1",
            "title": "Slope graph",
            "status": "ready",
        }
    ]

    # Return to step-1: it becomes `current_step`, leaving only step-2 in `done_steps`.
    await pg_db.execute(
        "UPDATE content.session_plans SET plan = %(p)s WHERE session_id = %(s)s",
        {
            "s": session,
            "p": Jsonb(
                {
                    "v": 2,
                    "goal": "Understand derivatives",
                    "current_step": {
                        "id": "step-1",
                        "text": "Explore slope again",
                        "landmark": None,
                    },
                    "next_steps": [],
                    "landmark_edits": {"added": [], "removed": []},
                    "branches": [],
                }
            ),
        },
    )
    revisited = (await client.get(f"/v1/sessions/{session}/plan", cookies=cookies)).json()
    assert revisited["done_steps"] == [
        {"id": "step-2", "text": "Tangent line", "landmark_id": None, "status": "done"}
    ]
    assert revisited["current_step"] == {
        "id": "step-1",
        "text": "Explore slope again",
        "landmark_id": None,
        "status": "current",
    }
