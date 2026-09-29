"""apply.diagnostic — 20-B1-phase2.md §7."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.apply.diagnostic import finish, start
from app.errors import NotFound, ValidationFailed
from app.events.dispatch import RuleDeps
from app.schemas.knowledge import SkillRef, SkillWeight

pytestmark = pytest.mark.phase1

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, *, ex=None, nx=False):
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def incr(self, key):
        v = int(self.store.get(key, "0")) + 1
        self.store[key] = str(v)
        return v

    async def delete(self, key):
        self.store.pop(key, None)


def _deps(graph=None) -> RuleDeps:
    return RuleDeps(
        graph=graph,
        redis=_FakeRedis(),
        params=_params(),
        now=lambda: NOW,
    )


def _params():
    from app.config import KnowledgeParams

    return KnowledgeParams()


def _skill(skill_id: str) -> SkillRef:
    return SkillRef(
        id=skill_id,
        name=skill_id,
        description="...",
        exam_ids=["SAT_MATH"],  # type: ignore[list-item]
        effort_h=4.0,
        base_half_life_h=None,
    )


def _weight(skill_id: str) -> SkillWeight:
    return SkillWeight(
        skill=_skill(skill_id),
        area_id="area.sat.algebra",
        weight=3.0,
    )


# --- start ---


async def test_start_graph_none_raises():
    with pytest.raises(ValidationFailed):
        await start(None, _deps(graph=None), uuid4(), "SAT_MATH", None)


async def test_start_no_skills(monkeypatch):
    async def fake_skills(driver, exam_id):
        return []

    async def fake_areas(driver, exam_id):
        return []

    monkeypatch.setattr(
        "app.apply.diagnostic.canonical_q.list_exam_skills", fake_skills
    )
    monkeypatch.setattr("app.apply.diagnostic.canonical_q.list_areas", fake_areas)

    with pytest.raises(NotFound):
        await start(None, _deps(graph=object()), uuid4(), "SAT_MATH", None)


# --- finish ---


async def test_finish_run_not_found(monkeypatch):
    async def fake_get(session, student_id, run_id):
        raise NotFound("diagnostic run not found")

    monkeypatch.setattr("app.apply.diagnostic._get_run", fake_get)

    with pytest.raises(NotFound):
        await finish(None, _deps(graph=object()), uuid4(), uuid4())


# --- the task the server issued must be the task the server accepts ---
#
# Оба дефекта ниже пропускали тесты, потому что состояние `asked` в них
# подставлялось руками, а payload событий никогда не валидировался.


def _instance(instance_id, skill_id="sat.alg.linear"):
    from app.schemas.tasks import TaskInstanceOut

    return TaskInstanceOut(
        id=instance_id,
        template_id="tpl.1",
        exam_id="SAT_MATH",
        type="mcq4",
        skill_id=skill_id,
        stem_rendered="2x = 4",
        options=[],
        figure_url=None,
        time_reference_sec=60,
        difficulty=2,
        tags=[],
        mode="diagnostic",
        provenance="template",
    )


def _state(**over):
    from app.schemas.diagnostic import DiagnosticState

    base = dict(
        exam_id="SAT_MATH",
        budget_left=8,
        reserve_left=4,
        asked=[],
        answered=0,
        pending_descent=[],
        reask_queue=[],
        roots_found=[],
        trap_hits=[],
        firm=[],
        shaky=[],
        last_grade_correct=None,
        budget_order=["sat.alg.linear"],
        indirect=[],
    )
    base.update(over)
    return DiagnosticState(**base)  # type: ignore[arg-type]


def _patch_graph(monkeypatch):
    """Минимум графа, чтобы start дошёл до выдачи задачи."""

    async def fake_skills(driver, exam_id):
        return [_weight("sat.alg.linear")]

    async def fake_areas(driver, exam_id):
        from app.schemas.knowledge import AreaOut

        return [
            AreaOut(
                id="area.sat.algebra",
                name="Алгебра",
                exam_id="SAT_MATH",
                score_share=1.0,
            )
        ]

    async def fake_prereqs(driver, skill_id, depth=1):
        return []

    async def fake_roots(driver, student_id, window_days):
        return []

    monkeypatch.setattr(
        "app.apply.diagnostic.canonical_q.list_exam_skills", fake_skills
    )
    monkeypatch.setattr("app.apply.diagnostic.canonical_q.list_areas", fake_areas)
    monkeypatch.setattr(
        "app.apply.diagnostic.canonical_q.get_prerequisites", fake_prereqs
    )
    monkeypatch.setattr("app.apply.diagnostic.personal_q.list_root_causes", fake_roots)


async def test_start_records_the_issued_task_in_asked(monkeypatch):
    """`answer` опознаёт ответ по `state.asked[-1]`, а роутер отвергает
    instance_id, которого нет в `asked`. Значит выдача задачи обязана попасть
    в состояние — иначе замер непроходим с первого же вопроса."""
    from uuid import uuid4 as _uuid4

    from app.apply.diagnostic import start

    _patch_graph(monkeypatch)
    instance_id = _uuid4()
    run_id = _uuid4()
    saved: list = []

    async def fake_create_run(session, student_id, exam_id, state, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(id=run_id)

    async def fake_save_state(session, student_id, saved_run_id, state):
        saved.append((saved_run_id, list(state.asked)))

    async def fake_issue(session, deps, student_id, req):
        return _instance(instance_id)

    monkeypatch.setattr("app.apply.diagnostic.diag_repo.create_run", fake_create_run)
    monkeypatch.setattr("app.apply.diagnostic.diag_repo.save_state", fake_save_state)
    monkeypatch.setattr("app.apply.tasks.issue", fake_issue)

    out = await start(None, _deps(graph=object()), _uuid4(), "SAT_MATH", None)

    assert out.next_task is not None
    assert out.next_task.id == instance_id
    assert out.state.asked == [instance_id], "выданная задача не попала в asked"
    assert saved == [(run_id, [instance_id])], "состояние с asked не сохранено"


async def test_completed_event_payload_matches_its_schema(monkeypatch):
    """`events.store` валидирует payload по `DiagnosticCompletedPayload`.
    Неполный payload — это 500 на каждом завершении замера."""
    from types import SimpleNamespace
    from uuid import uuid4 as _uuid4

    from app.apply.diagnostic import finish
    from app.events.store import _validated_payload

    run_id = _uuid4()
    captured: list = []

    async def fake_get_run(session, student_id, wanted_run_id):
        return SimpleNamespace(
            id=run_id, state=_state(firm=["sat.alg.linear"]).model_dump(mode="json")
        )

    async def fake_append(session, redis, event, *args, **kwargs):
        captured.append(event)
        return SimpleNamespace(id=1)

    async def fake_complete(session, student_id, wanted_run_id, result):
        return None

    monkeypatch.setattr("app.apply.diagnostic._get_run", fake_get_run)
    monkeypatch.setattr("app.apply.diagnostic.events_store.append", fake_append)
    monkeypatch.setattr("app.apply.diagnostic.diag_repo.complete_run", fake_complete)

    await finish(None, _deps(graph=None), _uuid4(), run_id)

    assert len(captured) == 1
    # Тот же путь, которым payload проходит в app/events/store.py
    _validated_payload(captured[0])


async def test_progress_event_payload_matches_its_schema(monkeypatch):
    """То же самое для `DiagnosticProgressPayload`: он требует состояние."""
    from types import SimpleNamespace
    from uuid import uuid4 as _uuid4

    from app.apply.diagnostic import answer
    from app.events.store import _validated_payload
    from app.schemas.tasks import AnswerResult, Grade

    run_id = _uuid4()
    instance_id = _uuid4()
    captured: list = []

    async def fake_get_run(session, student_id, wanted_run_id):
        return SimpleNamespace(
            id=run_id, state=_state(asked=[instance_id]).model_dump(mode="json")
        )

    async def fake_get_instance(session, student_id, wanted_instance_id):
        return SimpleNamespace(
            id=instance_id, skill_id="sat.alg.linear", exam_id="SAT_MATH"
        )

    async def fake_append(session, redis, event, *args, **kwargs):
        captured.append(event)
        return SimpleNamespace(id=1)

    async def fake_save_state(session, student_id, wanted_run_id, state):
        return None

    async def fake_issue(session, deps, student_id, req):
        return None

    monkeypatch.setattr("app.apply.diagnostic._get_run", fake_get_run)
    monkeypatch.setattr(
        "app.apply.diagnostic.tasks_repo.get_instance", fake_get_instance
    )
    monkeypatch.setattr("app.apply.diagnostic.events_store.append", fake_append)
    monkeypatch.setattr("app.apply.diagnostic.diag_repo.save_state", fake_save_state)
    monkeypatch.setattr("app.apply.tasks.issue", fake_issue)

    await answer(
        None,
        _deps(graph=None),
        _uuid4(),
        run_id,
        AnswerResult(
            grade=Grade(correct=True),
            solution=[],
            state_words="",
            knowledge_version=1,
        ),
    )

    assert len(captured) == 1
    _validated_payload(captured[0])
