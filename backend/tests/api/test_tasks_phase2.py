"""Phase 2 task transport with fake B1 apply handlers."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.auth import issue_token
from app.config import KnowledgeParams
from app.events import store
from app.events.dispatch import RuleDeps
from app.keys import knowledge_version
from app.main import create_app
from app.schemas.events import EventIn, EventType, TaskIssuedPayload
from app.schemas.tasks import AnswerResult, Grade, TaskInstanceOut

pytestmark = pytest.mark.phase2


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = str(value)

    async def expire(self, key, seconds):
        return key in self.values


class FakeSession:
    def __init__(self, student_id: UUID, instance_id: UUID, now: datetime):
        self.student_id = student_id
        self.row = SimpleNamespace(
            id=instance_id,
            student_id=student_id,
            mode="topic",
            exam_id="SAT_MATH",
            skill_id="skill-a",
            answered_at=None,
            solution_rendered=["step 1", "step 2"],
        )
        self.now = now
        self.first_event_at: datetime | None = None
        self.events: list[dict] = []

    async def scalar(self, statement):
        sql = str(statement.compile())
        params = statement.compile().params.values()
        if "FROM task_instances" in sql:
            return (
                self.row
                if self.student_id in params and self.row.id in params
                else None
            )
        if "min(events.occurred_at)" in sql:
            return self.first_event_at
        if "FROM events" in sql:
            for event in self.events:
                if event["type"] in {"task.skipped", "task.timed_out"} and event[
                    "payload"
                ]["instance_id"] == str(self.row.id):
                    return event["id"]
            return None
        raise AssertionError(sql)

    async def execute(self, statement):
        assert statement.is_insert
        values = statement.compile().params
        event_id = len(self.events) + 1
        self.events.append({"id": event_id, **values})
        return SimpleNamespace(one=lambda: (event_id, self.now))


@pytest.fixture
def task_app(fake_apply):
    student_id, instance_id = uuid4(), uuid4()
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    session = FakeSession(student_id, instance_id, now)
    redis = FakeRedis()
    redis.values[knowledge_version(str(student_id))] = "7"
    app = create_app()

    async def session_override():
        yield session

    deps_value = RuleDeps(
        graph=None, redis=redis, params=KnowledgeParams(), now=lambda: now
    )
    app.dependency_overrides[deps.get_session] = session_override
    app.dependency_overrides[deps.get_rule_deps] = lambda: deps_value

    issued = TaskInstanceOut(
        id=instance_id,
        template_id="template-a",
        exam_id="SAT_MATH",
        type="mcq4",
        skill_id="skill-a",
        stem_rendered="Question?",
        options=[{"key": "A", "text": "one"}, {"key": "B", "text": "two"}],
        figure_url=None,
        time_reference_sec=60,
        difficulty=1,
        tags=[],
        mode="topic",
        provenance="template",
    )
    issue_mock = fake_apply("app.apply.tasks.issue", issued)

    async def issue_side_effect(received_session, received_deps, owner, request):
        assert received_session is session
        assert received_deps is deps_value
        assert owner == student_id
        await store.append(
            session,
            redis,
            EventIn(
                type=EventType.task_issued,
                payload=TaskIssuedPayload(
                    instance_id=instance_id,
                    template_id="template-a",
                    skill_id="skill-a",
                    mode=request.mode,
                    via="topic",
                ).model_dump(mode="json"),
                student_id=student_id,
            ),
        )
        return issued

    issue_mock.side_effect = issue_side_effect
    answer_result = AnswerResult(
        grade=Grade(correct=True),
        solution=["step 1", "step 2"],
        state_after=None,
        state_words="good",
        knowledge_version=7,
    )
    answer_mock = fake_apply(
        "app.apply.task_answered.apply_task_answered", answer_result
    )

    async def answer_side_effect(received_session, event, received_deps):
        assert received_session is session
        assert received_deps is deps_value
        session.row.answered_at = now
        return answer_result

    answer_mock.side_effect = answer_side_effect
    skip_mock = fake_apply("app.apply.task_answered.apply_task_skipped", None)
    return app, session, student_id, instance_id, issue_mock, answer_mock, skip_mock


def _answer_body(instance_id: UUID) -> dict:
    return {
        "instance_id": str(instance_id),
        "answer": "A",
        "time_spent_sec": 30,
        "mode": "topic",
        "after_guideline": False,
        "hint_level_before": 0,
    }


def _task_body() -> dict:
    return {
        "skill_id": "skill-a",
        "set_id": None,
        "mode": "topic",
        "with_trap": None,
        "exclude_seen": True,
    }


def test_issue_answer_event_and_version_without_answer_leakage(task_app):
    app, session, student_id, instance_id, issue_mock, answer_mock, _ = task_app
    session.first_event_at = session.now - timedelta(minutes=5, seconds=10)
    with TestClient(app) as client:
        assert client.post("/tasks", json=_task_body()).status_code == 401
        client.cookies.set("quack_token", issue_token(student_id, "s@quack.kz"))
        issued = client.post("/tasks", json=_task_body())
        answered = client.post(
            f"/tasks/{instance_id}/answer", json=_answer_body(instance_id)
        )
        duplicate = client.post(
            f"/tasks/{instance_id}/answer", json=_answer_body(instance_id)
        )

    assert issued.status_code == 201
    assert issue_mock.await_count == 1
    assert "answer" not in issued.json()
    assert "solution_rendered" not in issued.json()
    assert all("correct" not in option for option in issued.json()["options"])
    assert answered.status_code == 200
    assert answered.json()["state_words"] == "good"
    assert answered.headers["X-Knowledge-Version"] == "7"
    assert answer_mock.await_count == 1
    assert [event["type"] for event in session.events] == [
        "task.issued",
        "task.answered",
    ]
    assert session.events[1]["payload"]["session_minute"] == 5
    assert duplicate.status_code == 409


def test_foreign_instance_and_solution_gate(task_app):
    app, session, student_id, instance_id, _, _, _ = task_app
    with TestClient(app) as client:
        client.cookies.set("quack_token", issue_token(student_id, "s@quack.kz"))
        before = client.get(f"/tasks/{instance_id}/solution")
        mismatch = client.post(
            f"/tasks/{instance_id}/answer",
            json=_answer_body(uuid4()),
        )
        client.cookies.set("quack_token", issue_token(uuid4(), "other@quack.kz"))
        foreign_answer = client.post(
            f"/tasks/{instance_id}/answer", json=_answer_body(instance_id)
        )
        foreign_solution = client.get(f"/tasks/{instance_id}/solution")
        client.cookies.set("quack_token", issue_token(student_id, "s@quack.kz"))
        answered = client.post(
            f"/tasks/{instance_id}/answer", json=_answer_body(instance_id)
        )
        solution = client.get(f"/tasks/{instance_id}/solution")

    assert before.status_code == 409
    assert mismatch.status_code == 400
    assert foreign_answer.status_code == 404
    assert foreign_solution.status_code == 404
    assert answered.status_code == 200
    assert solution.json() == {"solution": ["step 1", "step 2"]}
    assert len(session.events) == 1


@pytest.mark.parametrize(
    ("reason", "event_type"),
    [("skipped", "task.skipped"), ("timed_out", "task.timed_out")],
)
def test_skip_and_timeout_are_dispatched_once(task_app, reason, event_type):
    app, session, student_id, instance_id, _, _, skip_mock = task_app
    body = {
        "instance_id": str(instance_id),
        "reason": reason,
        "time_spent_sec": 15,
    }
    with TestClient(app) as client:
        client.cookies.set("quack_token", issue_token(student_id, "s@quack.kz"))
        skipped = client.post(f"/tasks/{instance_id}/skip", json=body)
        duplicate = client.post(f"/tasks/{instance_id}/skip", json=body)
        solution = client.get(f"/tasks/{instance_id}/solution")
        answer_after_skip = client.post(
            f"/tasks/{instance_id}/answer", json=_answer_body(instance_id)
        )

    assert skipped.status_code == 200
    assert skipped.json() == {}
    assert duplicate.status_code == 409
    assert answer_after_skip.status_code == 409
    assert solution.json() == {"solution": ["step 1", "step 2"]}
    assert [event["type"] for event in session.events] == [event_type]
    assert skip_mock.await_count == 1


def test_answer_survives_a_deferred_projection(task_app, monkeypatch):
    """§11 A2: проекция отложена — ученик всё равно получает оценку.

    `grade` читает у вариантов поля (`opt.key`), а в строке БД они лежат
    словарями из JSON-колонки. Пока их никто не разбирал, этот путь отвечал
    500 при каждой деградации — то есть ровно тогда, когда он и нужен.
    """
    app, session, student_id, instance_id, _, answer_mock, _ = task_app
    session.row.template_id = "template-a"
    session.row.seed = 1
    session.row.type = "mcq4"
    session.row.stem_rendered = "Question?"
    session.row.options = [
        {"key": "A", "text": "one", "correct": True},
        {"key": "B", "text": "two", "correct": False, "misconception_id": "m1"},
    ]
    session.row.answer = "A"
    session.row.trap_answers = []
    session.row.figure_url = None
    session.row.time_reference_sec = 60
    session.row.difficulty = 1
    session.row.tags = []

    async def refused(received_session, event, received_deps):
        # Диспетчер отказался проецировать: граф лёг или ждёт своей очереди.
        return {}

    monkeypatch.setattr("app.api._answer.dispatch.dispatch", refused)

    with TestClient(app) as client:
        client.cookies.set("quack_token", issue_token(student_id, "s@quack.kz"))
        answered = client.post(
            f"/tasks/{instance_id}/answer", json=_answer_body(instance_id)
        )

    assert answered.status_code == 200
    body = answered.json()
    assert body["grade"]["correct"] is True
    assert body["projection_status"] == "pending"
    assert body["solution"] == ["step 1", "step 2"]
    assert answer_mock.await_count == 0
