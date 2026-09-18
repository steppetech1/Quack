"""Phase 2 sets and knowledge transport with fake B1 apply functions."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api import knowledge as knowledge_api
from app.api import sets as sets_api
from app.api.auth import issue_token
from app.config import KnowledgeParams
from app.events import store
from app.events.dispatch import RuleDeps
from app.keys import knowledge_version
from app.main import create_app
from app.schemas.events import Event
from app.schemas.knowledge import (
    EvidenceOut,
    MisconceptionStateOut,
    RootCauseOut,
    SkillStateView,
)
from app.schemas.sets import SetOut, SetProgress, SetsByExam, TopicOut

pytestmark = pytest.mark.phase2


class FakeRedis:
    def __init__(self, student_id):
        self.values = {knowledge_version(str(student_id)): "9"}

    async def get(self, key):
        return self.values.get(key)


def _set(student_id, *, status="upcoming", position=0, skills=("skill-a",)):
    del student_id
    return SetOut(
        id=uuid4(),
        exam_id="SAT_MATH",
        area_ids=[],
        status=status,
        kind="regular",
        position=position,
        deadline=date.today() + timedelta(days=7),
        reason="practice",
        topics=[
            TopicOut(
                skill_id=skill,
                name=skill,
                kind="topic",
                position=index,
                status="open",
                level="weak",
                is_root=False,
                misconception_labels=[],
                subtitle=None,
            )
            for index, skill in enumerate(skills)
        ],
        progress=SetProgress(
            topics_closed=0,
            topics_total=len(skills),
            tasks_answered=0,
            tasks_correct=0,
        ),
    )


@pytest.fixture
def transport(monkeypatch, fake_apply):
    student_id = uuid4()
    session = object()
    redis = FakeRedis(student_id)
    deps_value = RuleDeps(
        graph=None,
        redis=redis,
        params=KnowledgeParams(),
        now=lambda: datetime(2026, 9, 18, 12, tzinfo=UTC),
    )
    app = create_app()
    rows: dict = {}
    events: list[Event] = []
    calls = SimpleNamespace(forecast=0, list_sets=0)

    async def session_override():
        yield session

    async def list_sets(received_session, owner, exam_id):
        assert received_session is session
        calls.list_sets += 1
        return [
            item
            for (student, _), item in rows.items()
            if student == owner and item.exam_id == exam_id
        ]

    async def get_set(received_session, owner, set_id):
        assert received_session is session
        return rows.get((owner, set_id))

    async def update_set(received_session, owner, set_id, skill_ids, deadline):
        item = rows[(owner, set_id)]
        if deadline is not None:
            item.deadline = deadline
        if skill_ids is not None:
            item.topics = [
                TopicOut(
                    skill_id=skill,
                    name=skill,
                    kind="topic",
                    position=index,
                    status="open",
                    level="weak",
                    is_root=False,
                    misconception_labels=[],
                    subtitle=None,
                )
                for index, skill in enumerate(skill_ids)
            ]

    async def set_topic_status(received_session, set_id, skill_id, status):
        item = rows[(student_id, set_id)]
        next(
            topic for topic in item.topics if topic.skill_id == skill_id
        ).status = status

    async def set_status(received_session, owner, set_id, status):
        rows[(owner, set_id)].status = status

    async def get_forecast(received_session, owner, exam_id):
        calls.forecast += 1
        return None

    async def append(received_session, received_redis, event_in, **kwargs):
        assert received_session is session
        assert received_redis is redis
        assert kwargs.get("dispatch_event") is False
        event = Event(
            **event_in.model_dump(exclude={"occurred_at"}),
            occurred_at=deps_value.now(),
            ingested_at=deps_value.now(),
            id=len(events) + 1,
        )
        events.append(event)
        return event

    app.dependency_overrides[deps.get_session] = session_override
    app.dependency_overrides[deps.get_rule_deps] = lambda: deps_value
    monkeypatch.setattr(sets_api.set_repo, "list_sets", list_sets)
    monkeypatch.setattr(sets_api.set_repo, "get_set", get_set)
    monkeypatch.setattr(sets_api.set_repo, "update_set", update_set)
    monkeypatch.setattr(sets_api.set_repo, "set_topic_status", set_topic_status)
    monkeypatch.setattr(sets_api.set_repo, "set_status", set_status)
    monkeypatch.setattr(sets_api.forecast_repo, "get", get_forecast)
    monkeypatch.setattr(store, "append", append)

    rebuild = fake_apply("app.apply.sets.rebuild_sets", None)
    opened = fake_apply("app.apply.sets.open_set", None)
    switched = fake_apply("app.apply.sets.on_set_change", None)

    async def switch_effect(received_session, event, received_deps):
        if event.type.value == "set.switched_by_user":
            for (owner, _), item in rows.items():
                if owner == student_id and item.status == "current":
                    item.status = "upcoming"
            rows[(student_id, event.set_id)].status = "current"

    switched.side_effect = switch_effect
    return SimpleNamespace(
        app=app,
        student_id=student_id,
        session=session,
        deps=deps_value,
        rows=rows,
        events=events,
        calls=calls,
        rebuild=rebuild,
        opened=opened,
        switched=switched,
        fake_apply=fake_apply,
    )


def _client(transport):
    client = TestClient(transport.app)
    client.cookies.set(
        "quack_token", issue_token(transport.student_id, "student@quack.kz")
    )
    return client


def test_empty_sets_rebuild_once_and_nonempty_read(transport):
    item = _set(transport.student_id)
    rebuilt = SetsByExam(
        exam_id="SAT_MATH", forecast=None, current=None, upcoming=[item], done=[]
    )
    transport.rebuild.return_value = rebuilt
    with _client(transport) as client:
        empty = client.get("/sets?exam_id=SAT_MATH")
        transport.rows[(transport.student_id, item.id)] = item
        nonempty = client.get("/sets?exam_id=SAT_MATH")

    assert empty.status_code == 200
    assert [row["id"] for row in empty.json()["upcoming"]] == [str(item.id)]
    assert nonempty.status_code == 200
    assert nonempty.headers["X-Knowledge-Version"] == "9"
    assert transport.rebuild.await_count == 1
    assert transport.calls.forecast == 2


def test_set_ownership_switch_and_deadline_validation(transport):
    current = _set(transport.student_id, status="current")
    done = _set(transport.student_id, status="done", position=1)
    upcoming = _set(transport.student_id, position=2)
    for item in (current, done, upcoming):
        transport.rows[(transport.student_id, item.id)] = item
    with _client(transport) as client:
        foreign = client.get(f"/sets/{uuid4()}")
        done_switch = client.post("/sets/switch", json={"set_id": str(done.id)})
        past = client.patch(
            f"/sets/{current.id}",
            json={"skill_ids": None, "deadline": "2020-01-01"},
        )
        switched = client.post("/sets/switch", json={"set_id": str(upcoming.id)})

    assert foreign.status_code == 404
    assert done_switch.status_code == 409
    assert past.status_code == 400
    assert switched.status_code == 200
    assert switched.json()["current"]["id"] == str(upcoming.id)
    assert switched.headers["X-Knowledge-Version"] == "9"
    assert [event.type.value for event in transport.events] == ["set.switched_by_user"]
    assert transport.switched.await_count == 1


def test_open_edit_and_topic_completion_do_not_open_next(transport):
    item = _set(transport.student_id, status="current", skills=("a", "b"))
    next_item = _set(transport.student_id, position=1)
    transport.rows[(transport.student_id, item.id)] = item
    transport.rows[(transport.student_id, next_item.id)] = next_item
    transport.opened.return_value = item
    deadline = date.today() + timedelta(days=10)
    with _client(transport) as client:
        opened = client.post(f"/sets/{item.id}/open")
        edited = client.patch(
            f"/sets/{item.id}",
            json={"skill_ids": None, "deadline": deadline.isoformat()},
        )
        topic = client.post(f"/sets/{item.id}/topics/a/open")
        first = client.post(f"/sets/{item.id}/topics/a/complete")
        last = client.post(f"/sets/{item.id}/topics/b/complete")
        after = client.get("/sets?exam_id=SAT_MATH")

    assert opened.status_code == 200
    assert edited.status_code == 200
    assert topic.json()["skill_id"] == "a"
    assert first.json()["status"] == "current"
    assert last.json()["status"] == "done"
    assert after.json()["current"] is None
    assert [row["id"] for row in after.json()["upcoming"]] == [str(next_item.id)]
    assert [event.type.value for event in transport.events] == [
        "set.opened",
        "set.deadline_changed",
        "topic.opened",
        "topic.completed",
        "topic.completed",
        "set.completed",
    ]
    assert transport.events[1].payload["old"] != transport.events[1].payload["new"]
    assert all(
        response.headers["X-Knowledge-Version"] == "9"
        for response in (opened, edited, topic, first, last, after)
    )


def test_knowledge_lists_explain_dispute_and_version(transport, monkeypatch):
    now = datetime(2026, 9, 18, 12, tzinfo=UTC)
    skill = SkillStateView(
        skill_id="skill-a",
        name="Skill A",
        area_id="area-a",
        exam_id="SAT_MATH",
        weight=1,
        p_target=0.8,
        level="weak",
        p_recall=0.4,
        confidence=0.7,
        trend="flat",
        due_at=None,
        is_root=True,
        n_evidence=1,
    )
    misconception = MisconceptionStateOut(
        misconception_id="misc-a",
        name="Mistake",
        status="confirmed",
        occurrence_count=2,
        strong_count=2,
        consecutive_avoided=0,
        triggers={},
        first_seen_at=now,
        updated_at=now,
        skill_ids=["skill-a"],
    )
    root = RootCauseOut(
        from_skill_id="skill-a",
        root_skill_id="skill-root",
        confidence=0.8,
        source="rule",
        created_at=now,
    )
    evidence = EvidenceOut(
        evidence_id="1:skill-a",
        event_id=1,
        skill_id="skill-a",
        kind="task",
        tier=2,
        source="task",
        weight=1,
        direction=1,
        observed_at=now,
        summary="observed",
        instance_id=None,
        message_id=None,
    )
    states = transport.fake_apply("app.apply.knowledge.states_view", [skill])
    miscs = transport.fake_apply(
        "app.apply.knowledge.misconceptions_view", [misconception]
    )
    explain = transport.fake_apply("app.apply.knowledge.explain", [evidence])
    disputed = transport.fake_apply("app.apply.dispute.apply_dispute", misconception)

    async def roots(driver, owner, window_days):
        assert owner == transport.student_id
        assert window_days == transport.deps.params.root_window_days
        return [root]

    monkeypatch.setattr(knowledge_api.personal, "list_root_causes", roots)
    transport.deps.graph = object()

    async def mark_processed(session, event_ids):
        pass

    monkeypatch.setattr(store, "mark_processed", mark_processed)
    with _client(transport) as client:
        knowledge = client.get("/knowledge?exam_id=SAT_MATH")
        explained = client.get("/knowledge/explain/skill-a")
        dispute = client.post(
            "/knowledge/misconceptions/misc-a/dispute", json={"disputed": True}
        )
        undispute = client.post(
            "/knowledge/misconceptions/misc-a/dispute", json={"disputed": False}
        )

    assert knowledge.status_code == 200
    assert [item["skill_id"] for item in knowledge.json()["skills"]] == ["skill-a"]
    assert [
        item["misconception_id"] for item in knowledge.json()["misconceptions"]
    ] == ["misc-a"]
    assert [item["root_skill_id"] for item in knowledge.json()["roots"]] == [
        "skill-root"
    ]
    assert explained.json()["items"][0]["evidence_id"] == "1:skill-a"
    assert [event.type.value for event in transport.events] == [
        "misconception.disputed",
        "misconception.undisputed",
    ]
    assert dispute.status_code == 200
    assert undispute.status_code == 200
    assert disputed.await_count == 2
    assert all(
        response.headers["X-Knowledge-Version"] == "9"
        for response in (knowledge, explained, dispute, undispute)
    )
    assert states.await_count == miscs.await_count == explain.await_count == 1
