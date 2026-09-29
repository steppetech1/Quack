"""T10, T16: a graph outage must not cost the student their answer (§11 A2)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api import _answer
from app.config import KnowledgeParams
from app.errors import Conflict
from app.events.dispatch import RuleDeps
from app.events.outbox import JobOutbox
from app.schemas.auth import StudentCtx
from app.schemas.tasks import AnswerIn, AnswerResult, Grade

pytestmark = pytest.mark.phase5

CLOCK = datetime(2026, 9, 19, 12, tzinfo=UTC)


class _Session:
    async def commit(self):
        pass

    async def flush(self):
        pass


class _Redis:
    async def get(self, _key):
        return None

    async def set(self, *_args, **_kwargs):
        return True

    async def delete(self, *_keys):
        return None

    async def incr(self, _key):
        return 1


def _instance(instance_id, student_id):
    """Строка `task_instances` целиком: деградация разбирает её в TaskInstance,
    чтобы выставить оценку без графа, и неполная заглушка это скрывала."""
    return SimpleNamespace(
        id=instance_id,
        student_id=student_id,
        mode="topic",
        exam_id="SAT_MATH",
        skill_id="alg.linear",
        answered_at=None,
        solution_rendered=["шаг 1"],
        template_id="t1",
        seed=1,
        type="mcq4",
        stem_rendered="Сколько?",
        options=[
            {"key": "A", "text": "один", "correct": True},
            {"key": "B", "text": "два", "correct": False},
        ],
        answer="A",
        trap_answers=[],
        figure_url=None,
        time_reference_sec=60,
        difficulty=1,
        tags=[],
    )


@pytest.fixture
def harness(monkeypatch):
    student = StudentCtx(student_id=uuid4(), email="s@example.com")
    instance_id = uuid4()
    instance = _instance(instance_id, student.student_id)
    outbox = JobOutbox()
    deps = RuleDeps(
        graph=None,
        redis=_Redis(),
        params=KnowledgeParams(),
        now=lambda: CLOCK,
        jobs=outbox,
    )
    state = SimpleNamespace(results={}, appended=[])

    async def owned_instance(_session, _student_id, _instance_id, *, lock=False):
        return instance

    async def skipped_instance(*_args, **_kwargs):
        return False

    async def current_session_id(*_args, **_kwargs):
        return uuid4()

    async def session_minute(*_args, **_kwargs):
        return 1

    async def append(_session, _redis, event_in, *args, **kwargs):
        state.appended.append(event_in)
        return SimpleNamespace(
            id=42, type=event_in.type, student_id=event_in.student_id
        )

    async def dispatch(_session, _event, _deps):
        return state.results

    def grade(_instance, answer):
        return Grade(correct=answer == "A")

    monkeypatch.setattr(_answer, "owned_instance", owned_instance)
    monkeypatch.setattr(_answer, "skipped_instance", skipped_instance)
    monkeypatch.setattr(_answer, "current_session_id", current_session_id)
    monkeypatch.setattr(_answer, "session_minute", session_minute)
    monkeypatch.setattr(_answer.store, "append", append)
    monkeypatch.setattr(_answer.dispatch, "dispatch", dispatch)
    monkeypatch.setattr("app.tasks.answer.grade", grade)
    return SimpleNamespace(
        student=student,
        instance_id=instance_id,
        instance=instance,
        deps=deps,
        outbox=outbox,
        state=state,
    )


async def _answer_once(harness, answer="A"):
    return await _answer.record_answer(
        _Session(),
        harness.deps,
        harness.student,
        harness.instance_id,
        AnswerIn(
            instance_id=harness.instance_id,
            answer=answer,
            mode="topic",
            time_spent_sec=30,
        ),
        "topic",
    )


async def test_t10_a_graph_outage_keeps_the_answer_and_owes_the_projection(harness):
    """Событие записано, оценка выдана, знание — не выдумано."""
    harness.state.results = {}  # обработчик графа не отработал
    result = await _answer_once(harness)

    assert result.grade.correct is True
    assert result.solution == ["шаг 1"]
    assert result.projection_status == "pending"
    assert result.state_after is None
    assert result.state_words == ""
    assert len(harness.state.appended) == 1


async def test_a_pending_answer_queues_exactly_one_catch_up(harness):
    harness.state.results = {}
    await _answer_once(harness)
    await _answer_once(harness)
    # Дедупликация по детерминированному job_id: буря ответов во время
    # аварии ставит одно намерение, а не одно на ответ (§13.3).
    [entry] = harness.outbox.entries
    assert entry.fn_name == "recover_graph_events"
    assert entry.job_id == f"graph-recover:{harness.student.student_id}"
    assert entry.kwargs["through_event_id"] == 42


async def test_a_handler_that_reports_pending_also_queues_the_catch_up(harness):
    harness.state.results = {
        "apply_task_answered": AnswerResult(
            grade=Grade(correct=True),
            solution=["шаг 1"],
            state_words="",
            knowledge_version=0,
            projection_status="pending",
        )
    }
    result = await _answer_once(harness)
    assert result.projection_status == "pending"
    assert [entry.fn_name for entry in harness.outbox.entries] == [
        "recover_graph_events"
    ]


async def test_an_applied_answer_queues_nothing(harness):
    harness.state.results = {
        "apply_task_answered": AnswerResult(
            grade=Grade(correct=True),
            solution=["шаг 1"],
            state_words="уверенно",
            knowledge_version=3,
        )
    }
    result = await _answer_once(harness)
    assert result.projection_status == "applied"
    assert result.knowledge_version == 3
    assert harness.outbox.entries == []


async def test_t16_a_second_answer_to_the_same_instance_is_a_conflict(harness):
    harness.instance.answered_at = CLOCK
    with pytest.raises(Conflict):
        await _answer_once(harness)
    assert harness.state.appended == []
