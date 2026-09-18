"""Phase 2 transport tests with B1/B2 rules replaced by fakes."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps, matching, overview
from app.config import KnowledgeParams
from app.events.dispatch import RuleDeps
from app.main import create_app
from app.schemas.auth import StudentCtx
from app.schemas.common import Source
from app.schemas.profile import Profile
from app.schemas.programs import Program, Requirement
from app.schemas.roadmap import ExamProgress, ExamRequirementOut, MilestoneOut

pytestmark = pytest.mark.phase2


def _program(index: int) -> Program:
    return Program(
        id=f"program-{index}",
        university=f"University {index}",
        country="KZ",
        city="Almaty",
        direction="math",
        language="en",
        currency="KZT",
        requirements=[],
        deadlines=[],
        source_url="https://example.com",
        checked_at=date(2026, 9, 1),
        is_demo=True,
        extracted_auto=False,
        flagged=False,
    )


@pytest.fixture
def transport(monkeypatch):
    student = StudentCtx(student_id=uuid4(), email="student@example.com")
    session = object()
    clock = datetime(2026, 9, 18, tzinfo=UTC)
    rule_deps = RuleDeps(
        graph=None, redis=object(), params=KnowledgeParams(), now=lambda: clock
    )
    app = create_app()

    async def session_override():
        yield session

    app.dependency_overrides[deps.get_session] = session_override
    app.dependency_overrides[deps.get_current_student] = lambda: student
    app.dependency_overrides[deps.get_rule_deps] = lambda: rule_deps

    async def get_profile(*args):
        return Profile(student_id=student.student_id, readiness=0.6)

    monkeypatch.setattr(matching.profile_repo, "get_profile", get_profile)
    monkeypatch.setattr(
        matching.program_repo,
        "list_all",
        lambda *args: _async([_program(i) for i in range(5)]),
    )
    monkeypatch.setattr(
        matching.program_repo, "list_saved_programs", lambda *args: _async([])
    )
    monkeypatch.setattr(
        matching.program_repo,
        "get_program",
        lambda _session, pid: _async(
            next((_program(i) for i in range(5) if pid == f"program-{i}"), None)
        ),
    )
    return TestClient(app), monkeypatch, student, session, rule_deps


async def _async(value):
    return value


def test_matching_pipeline_limit_and_minimum(transport):
    client, monkeypatch, *_ = transport
    calls = []

    def hard_filter(profile, programs, forecasts, test_dates, today, params):
        calls.append("hard")
        assert len(programs) == 5
        assert forecasts == test_dates == {}
        return [
            SimpleNamespace(program_id=p.id, factors=[], assumptions=[])
            for p in programs
        ]

    def realism_rule(item, params):
        calls.append("realism")
        return "possible"

    def rank_rule(profile, results, soft_scores, params):
        calls.append("rank")
        assert soft_scores == {}
        return [
            SimpleNamespace(program_id=item.program_id, score=1.0) for item in results
        ]

    monkeypatch.setattr(matching.hard, "hard_filter", hard_filter)
    monkeypatch.setattr(matching.realism, "realism", realism_rule)
    monkeypatch.setattr(matching.rank, "rank", rank_rule)
    response = client.get("/matching?limit=1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 3
    assert body["items"][0]["soft_pending"] is True
    assert body["items"][0]["fits_text"] is None
    assert body["profile_readiness"] == 0.6
    assert calls[0] == "hard" and calls[-1] == "rank"
    assert calls.count("realism") == 5
    assert len(client.get("/matching?limit=4").json()["items"]) == 4


def test_matching_empty_and_compare(transport):
    client, monkeypatch, *_ = transport
    monkeypatch.setattr(matching.hard, "hard_filter", lambda *args: [])
    monkeypatch.setattr(matching.hard, "explain_empty", lambda results: "no fit")
    monkeypatch.setattr(matching.rank, "rank", lambda *args, **kwargs: [])
    assert client.get("/matching").json()["empty_reason"] == "no fit"
    assert client.get("/matching/compare?ids=program-1").status_code == 400
    assert client.get("/matching/compare?ids=a,b,c,d,e").status_code == 400
    monkeypatch.setattr(
        matching.compare_rules,
        "compare",
        lambda *args: SimpleNamespace(
            model_dump=lambda: {
                "rows": [],
                "collapsed_same": [],
                "conclusion": "LLM text",
            }
        ),
    )
    response = client.get("/matching/compare?ids=program-1,program-2")
    assert response.status_code == 200, response.text
    assert response.json()["conclusion"] is None
    assert response.json()["program_ids"] == ["program-1", "program-2"]


def test_overview_and_mark_unmark(transport):
    client, monkeypatch, student, session, rule_deps = transport
    calls = []
    marks = {}
    events = []
    milestone = MilestoneOut(
        key="test:SAT_MATH:2026-10-01",
        kind="test",
        date=date(2026, 10, 1),
        title="SAT",
        exam_id="SAT_MATH",
        program_id=None,
        source=Source(label="calendar", url=None, checked_at=None, is_demo=True),
        done=False,
    )
    monkeypatch.setattr(
        overview.program_repo, "list_saved_programs", lambda *args: _async([])
    )
    monkeypatch.setattr(
        overview.profile_repo,
        "get_profile",
        lambda *args: _async(Profile(student_id=student.student_id)),
    )
    monkeypatch.setattr(
        overview.milestone_repo, "list_marks", lambda *args: _async(marks.copy())
    )

    async def set_mark(_session, owner, key, done):
        assert _session is session and owner == student.student_id
        if done:
            marks[key] = rule_deps.now()
        else:
            marks.pop(key, None)

    monkeypatch.setattr(overview.milestone_repo, "set_mark", set_mark)
    monkeypatch.setattr(overview, "_last_summary", lambda *args: _async(None))
    monkeypatch.setattr(
        overview.requirements,
        "build_requirements",
        lambda *args: calls.append("requirements") or [],
    )

    def build_milestones(saved, required, dates, calendars, current_marks, today):
        calls.append("milestones")
        return [
            milestone.model_copy(
                update={
                    "done": milestone.key in current_marks,
                    "done_at": current_marks.get(milestone.key),
                }
            )
        ]

    monkeypatch.setattr(overview.milestones, "build_milestones", build_milestones)
    monkeypatch.setattr(
        overview.conflicts,
        "find_conflicts",
        lambda *args: calls.append("conflicts") or [],
    )

    async def append(_session, redis, event, *_args):
        assert _session is session and redis is rule_deps.redis
        events.append(event)

    monkeypatch.setattr(overview.store, "append", append)
    response = client.get("/overview")
    assert response.status_code == 200, response.text
    assert calls == ["requirements", "milestones", "conflicts"]
    assert response.json()["milestones"][0]["done"] is False
    for done in (True, False):
        response = client.post(
            f"/overview/milestones/{milestone.key}", json={"done": done}
        )
        assert response.status_code == 200, response.text
        assert response.json()["done"] is done
    assert [event.type.value for event in events] == ["milestone.done"] * 2
    assert [event.payload["done"] for event in events] == [True, False]
    assert (
        client.post("/overview/milestones/unknown", json={"done": True}).status_code
        == 404
    )


def test_overview_uses_knowledge_states_for_progress(transport):
    client, monkeypatch, student, *_ = transport
    requirement = ExamRequirementOut(
        exam_id="SAT_MATH",
        target_score=700,
        target_source="programs",
        max_raw_score=58,
        program_ids=["program-0"],
        test_dates=[],
        has_knowledge_model=True,
        current_estimate=None,
        estimate_note="нет данных",
    )
    calls = []
    program = _program(0)
    program.requirements = [
        Requirement(
            type="exam_score",
            exam_id="SAT_MATH",
            threshold=700,
            comparator=">=",
            description="SAT",
        )
    ]
    monkeypatch.setattr(
        overview.program_repo, "list_saved_programs", lambda *args: _async([program])
    )
    monkeypatch.setattr(
        overview.profile_repo,
        "get_profile",
        lambda *args: _async(Profile(student_id=student.student_id)),
    )
    monkeypatch.setattr(overview.forecast_repo, "get", lambda *args: _async(None))
    monkeypatch.setattr(overview.milestone_repo, "list_marks", lambda *args: _async({}))
    monkeypatch.setattr(overview, "_last_summary", lambda *args: _async(None))
    monkeypatch.setattr(
        overview.requirements,
        "build_requirements",
        lambda *args: calls.append("requirements") or [requirement],
    )
    monkeypatch.setattr(
        overview.milestones,
        "build_milestones",
        lambda *args: calls.append("milestones") or [],
    )
    monkeypatch.setattr(
        overview.conflicts,
        "find_conflicts",
        lambda *args: calls.append("conflicts") or [],
    )

    async def states_view(*args):
        calls.append("states")
        return []

    monkeypatch.setattr(overview.apply_knowledge, "states_view", states_view)

    def exam_progress(received_requirement, states, forecast, milestone_items):
        calls.append("progress")
        assert received_requirement is requirement
        assert states == [] and forecast is None and milestone_items == []
        return ExamProgress(
            exam_id="SAT_MATH",
            readiness=0.25,
            forecast=None,
            milestones_done=0,
            milestones_total=0,
        )

    monkeypatch.setattr(overview.progress, "exam_progress", exam_progress)
    response = client.get("/overview")
    assert response.status_code == 200, response.text
    assert response.json()["progress"][0]["readiness"] == 0.25
    assert calls == ["requirements", "milestones", "conflicts", "states", "progress"]
