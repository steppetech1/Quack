"""The phase-4 job frame — §13.2 `test_pregenerate.py` and friends.

The jobs are driven on fakes: a scripted `FakeLLMClient`, a sessionmaker
that hands out a stub session, and monkeypatched repositories. What is
being checked is the *frame* of §2.1 — locks, statuses, idempotency, which
errors defer and which fail — not the wording, which `test_texts_phase4`
covers.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from arq import Retry

from app.agents import jobs_phase4 as jobs
from app.errors import LLMUnavailable
from app.llm.fake import FakeLLMClient
from app.schemas.sets import SetOut, SetProgress, TopicOut
from app.schemas.texts import ExplanationOut, GeneratedText, GuidelineOut

pytestmark = pytest.mark.phase4

CLOCK = datetime(2026, 9, 19, 12, tzinfo=UTC)
STUDENT = UUID("11111111-1111-4111-8111-111111111111")
# Фиксированный id: случайный UUID в фактах отчёта приносил бы в постпроверку
# случайные цифры и делал бы тест плавающим.
SET_ID = UUID("22222222-2222-4222-8222-222222222222")


class _Session:
    async def commit(self):
        pass

    async def flush(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _Redis:
    def __init__(self) -> None:
        self.values: dict = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, nx=False, ex=None, **_kwargs):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)


def _ctx(llm, redis=None, job_try=1):
    return {
        "sessionmaker": _Session,
        "redis": redis or _Redis(),
        "llm": llm,
        "job_id": "job-1",
        "job_try": job_try,
        "neo4j": object(),
    }


def _set(topics=(("a", "topic"), ("b", "topic"), ("c", "check"))) -> SetOut:
    return SetOut(
        id=SET_ID,
        exam_id="SAT_MATH",
        area_ids=["algebra"],
        status="current",
        kind="regular",
        position=0,
        deadline=date(2026, 10, 1),
        reason="по модели знаний",
        topics=[
            TopicOut(
                skill_id=skill,
                name=skill,
                kind=kind,
                position=index,
                status="open",
                level="shaky",
                is_root=False,
                misconception_labels=[],
                subtitle=None,
            )
            for index, (skill, kind) in enumerate(topics)
        ],
        progress=SetProgress(
            topics_closed=0, topics_total=len(topics), tasks_answered=0, tasks_correct=0
        ),
    )


def _inputs_for(kind: str, skill_id: str):
    from app.schemas.texts import ExplanationInputs, GuidelineInputs, SkillBrief

    brief = SkillBrief(
        id=skill_id, name=skill_id, description="описание", exam_id="SAT_MATH"
    )
    if kind == "explanation":
        return ExplanationInputs(skill=brief)
    return GuidelineInputs(
        skill=brief, state_words="shaky", p_target_words="около 90 процентов"
    )


def _guideline() -> GuidelineOut:
    return GuidelineOut(
        how_to_prepare="Начни с простого.",
        must_know=["раз", "два", "три"],
        traps=[],
        what_to_solve=["простые", "текстовые"],
        summary="Коротко.",
    )


def _explanation() -> ExplanationOut:
    return ExplanationOut(text="Объяснение.", key_points=["раз", "два"])


def _row(status: str, attempts: int = 0) -> GeneratedText:
    return GeneratedText(
        id=uuid4(),
        kind="guideline",
        input_hash="hash",
        text="текст" if status == "ready" else None,
        model="m",
        prompt_version="guideline_v1",
        created_at=CLOCK,
        status=status,
        attempts=attempts,
    )


@pytest.fixture
def pregen(monkeypatch):
    """Everything `pregenerate_set` touches, replaced by a recorder."""
    state = SimpleNamespace(rows={}, marks=[], set_out=_set())

    async def get_set(*_args):
        return state.set_out

    async def current_hash(_s, _d, _student, _set_id, skill_id, kind, version, model):
        return f"hash:{kind}:{skill_id}", _inputs_for(kind, skill_id)

    async def get_generated(_session, kind, digest):
        return state.rows.get(digest)

    async def mark(_session, kind, digest, status, **kwargs):
        state.marks.append((kind, digest, status))
        row = _row(status)
        row.kind, row.input_hash = kind, digest
        state.rows[digest] = row
        return row

    monkeypatch.setattr(jobs.sets_repo, "get_set", get_set)
    monkeypatch.setattr(jobs.texts_repo, "get_generated", get_generated)
    monkeypatch.setattr(jobs.texts_repo, "mark", mark)
    from app.apply import texts as apply_texts

    monkeypatch.setattr(apply_texts, "current_hash", current_hash)
    return state


# --- pregenerate_set (§3) ---


async def test_a_three_topic_set_produces_the_expected_texts(pregen):
    # topic + topic + check → 2 гайдлайна и 3 объяснения (§3.1).
    script = [
        _guideline(),
        _explanation(),
        _guideline(),
        _explanation(),
        _explanation(),
    ]
    llm = FakeLLMClient(script)
    await jobs.pregenerate_set(_ctx(llm), "req", SET_ID, STUDENT)
    ready = [mark for mark in pregen.marks if mark[2] == "ready"]
    kinds = [kind for kind, _digest, _status in ready]
    assert kinds.count("guideline") == 2
    assert kinds.count("explanation") == 3
    # Первым идёт гайдлайн первого топика — при обрыве готово нужное.
    assert pregen.marks[0][1] == "hash:guideline:a"


async def test_a_ready_text_is_not_regenerated(pregen):
    pregen.rows["hash:guideline:a"] = _row("ready")
    llm = FakeLLMClient([_explanation(), _guideline(), _explanation(), _explanation()])
    await jobs.pregenerate_set(_ctx(llm), "req", SET_ID, STUDENT)
    assert llm.remaining == 0
    assert "hash:guideline:a" not in [
        digest for _kind, digest, status in pregen.marks if status == "generating"
    ]


async def test_three_failed_attempts_stop_further_tries(pregen):
    pregen.rows["hash:guideline:a"] = _row("failed", attempts=3)
    llm = FakeLLMClient([_explanation(), _guideline(), _explanation(), _explanation()])
    await jobs.pregenerate_set(_ctx(llm), "req", SET_ID, STUDENT)
    assert llm.remaining == 0


async def test_llm_down_defers_the_whole_job(pregen):
    llm = FakeLLMClient([LLMUnavailable("llm unavailable")])
    with pytest.raises(Retry):
        await jobs.pregenerate_set(_ctx(llm), "req", SET_ID, STUDENT)
    # Строка осталась «генерируется» — читатель покажет stale (§3.7).
    assert ("guideline", "hash:guideline:a", "generating") in pregen.marks


async def test_a_rate_limit_defers_by_thirty_seconds(pregen):
    llm = FakeLLMClient([LLMUnavailable("rate limit")])
    with pytest.raises(Retry) as caught:
        await jobs.pregenerate_set(_ctx(llm), "req", SET_ID, STUDENT)
    assert caught.value.defer_score == 30_000


async def test_one_invalid_output_fails_only_its_own_text(pregen):
    """Одна плохая генерация не блокирует сет (§3.7)."""
    bad = LLMUnavailable("structured output failed")
    llm = FakeLLMClient(
        [bad, _explanation(), _guideline(), _explanation(), _explanation()]
    )
    with pytest.raises(RuntimeError, match="guideline:a"):
        await jobs.pregenerate_set(_ctx(llm), "req", SET_ID, STUDENT)
    statuses = {digest: status for _kind, digest, status in pregen.marks}
    assert statuses["hash:guideline:a"] == "failed"
    assert statuses["hash:explanation:b"] == "ready"


async def test_a_deleted_set_is_skipped(pregen, monkeypatch):
    async def get_set(*_args):
        return None

    monkeypatch.setattr(jobs.sets_repo, "get_set", get_set)
    llm = FakeLLMClient([])
    await jobs.pregenerate_set(_ctx(llm), "req", SET_ID, STUDENT)
    assert pregen.marks == []


async def test_a_locked_set_is_left_to_the_other_worker(pregen):
    from app import keys

    redis = _Redis()
    redis.values[keys.lock(f"pregen:{SET_ID}")] = "1"
    llm = FakeLLMClient([])
    await jobs.pregenerate_set(_ctx(llm, redis), "req", SET_ID, STUDENT)
    assert pregen.marks == []


async def test_the_lock_is_released_even_after_a_failure(pregen):
    from app import keys

    redis = _Redis()
    llm = FakeLLMClient([LLMUnavailable("llm unavailable")])
    with pytest.raises(Retry):
        await jobs.pregenerate_set(_ctx(llm, redis), "req", SET_ID, STUDENT)
    assert keys.lock(f"pregen:{SET_ID}") not in redis.values


# --- set_summary (§4) ---


@pytest.fixture
def summary(monkeypatch):
    from app.config import KnowledgeParams
    from app.sets.report import ReportInputs, set_stats

    stats = set_stats(
        ReportInputs(
            set_id=SET_ID,
            exam_id="SAT_MATH",
            deadline=date(2026, 9, 21),
            tasks_answered=5,
            tasks_correct=4,
        ),
        KnowledgeParams(),
        CLOCK,
    )
    state = SimpleNamespace(
        row=SimpleNamespace(status="generating", text=None, stats=stats), written=[]
    )

    async def get(*_args):
        return state.row

    async def set_text(_session, _set_id, text, version, status):
        state.written.append((text, version, status))
        return None

    monkeypatch.setattr(jobs.summaries_repo, "get", get)
    monkeypatch.setattr(jobs.summaries_repo, "set_text", set_text)
    return state


async def test_summary_writes_the_text_and_marks_it_ready(summary):
    from app.schemas.texts import SetSummaryTextOut

    llm = FakeLLMClient([SetSummaryTextOut(text="Решено 5 задач, 4 верно.")])
    await jobs.set_summary(_ctx(llm), "req", SET_ID, STUDENT)
    [(text, version, status)] = summary.written
    assert status == "ready" and version.startswith("set_summary_v")
    assert "5" in text


async def test_a_number_outside_the_stats_fails_the_summary(summary):
    from app.schemas.texts import SetSummaryTextOut

    bad = SetSummaryTextOut(text="Решено 7 задач!")
    llm = FakeLLMClient([bad, bad])
    await jobs.set_summary(_ctx(llm), "req", SET_ID, STUDENT)
    [(text, _version, status)] = summary.written
    # Текст не отдаётся никогда — статистика в Обзоре остаётся (§4.4).
    assert text is None and status == "failed"


async def test_a_ready_summary_is_not_regenerated(summary):
    summary.row.status = "ready"
    summary.row.text = "готовый текст"
    llm = FakeLLMClient([])
    await jobs.set_summary(_ctx(llm), "req", SET_ID, STUDENT)
    assert summary.written == []
    assert llm.calls == []


async def test_a_missing_summary_row_is_skipped(summary, monkeypatch):
    async def get(*_args):
        return None

    monkeypatch.setattr(jobs.summaries_repo, "get", get)
    llm = FakeLLMClient([])
    await jobs.set_summary(_ctx(llm), "req", SET_ID, STUDENT)
    assert summary.written == []


# --- soft_match (§5) ---


@pytest.fixture
def soft(monkeypatch):
    from app.schemas.profile import Profile
    from tests.quack.conftest import make_program

    profile = Profile(student_id=STUDENT)
    profile.traits.summary = "тёплый климат"
    state = SimpleNamespace(
        profile=profile,
        programs=[
            make_program(1, environment_text="Тёплый приморский город."),
            make_program(2, environment_text=None),
        ],
        existing={},
        written=[],
    )

    async def get_profile(*_args):
        return state.profile

    async def candidates(*_args):
        return state.programs

    async def get_many(*_args):
        return state.existing

    async def put(_session, digest, program_id, version, **kwargs):
        state.written.append((program_id, kwargs["score"], kwargs["model"]))

    monkeypatch.setattr(jobs.profiles_repo, "get_profile", get_profile)
    monkeypatch.setattr(jobs.soft_repo, "get_many", get_many)
    monkeypatch.setattr(jobs.soft_repo, "put", put)
    from app.apply import soft as apply_soft

    monkeypatch.setattr(apply_soft, "candidates", candidates)
    return state


async def test_an_empty_environment_costs_no_model_call(soft):
    from app.agents.texts import _SoftMatchModelOut

    llm = FakeLLMClient(
        [_SoftMatchModelOut(score=0.8, fit_text="подходит: тёплый город")]
    )
    await jobs.soft_match(_ctx(llm), "req", STUDENT, [])
    assert len(llm.calls) == 1
    assert ("program-2", 0.5, "no_environment") in soft.written


async def test_an_empty_summary_ends_the_job_immediately(soft):
    soft.profile.traits.summary = "   "
    llm = FakeLLMClient([])
    await jobs.soft_match(_ctx(llm), "req", STUDENT, [])
    assert soft.written == []


async def test_a_failing_pair_gets_a_neutral_row_not_an_endless_retry(soft):
    from app.agents.texts import _SoftMatchModelOut

    bad = _SoftMatchModelOut(score=0.5, fit_text="стоит 5000 евро")
    llm = FakeLLMClient([bad, bad])
    await jobs.soft_match(_ctx(llm), "req", STUDENT, [])
    assert ("program-1", 0.5, "failed") in soft.written


async def test_already_scored_pairs_are_skipped(soft):
    from app.schemas.matching import SoftMatchOut

    soft.existing = {
        "program-1": SoftMatchOut(
            program_id="program-1", score=0.7, fit_text="ок", stale=False
        )
    }
    llm = FakeLLMClient([])
    await jobs.soft_match(_ctx(llm), "req", STUDENT, [])
    assert [program_id for program_id, *_ in soft.written] == ["program-2"]


async def test_a_stale_version_row_is_recomputed(soft):
    from app.agents.texts import _SoftMatchModelOut
    from app.schemas.matching import SoftMatchOut

    soft.existing = {
        "program-1": SoftMatchOut(
            program_id="program-1", score=0.7, fit_text="ок", stale=True
        )
    }
    llm = FakeLLMClient(
        [_SoftMatchModelOut(score=0.9, fit_text="подходит: тёплый город")]
    )
    await jobs.soft_match(_ctx(llm), "req", STUDENT, [])
    assert [program_id for program_id, *_ in soft.written] == [
        "program-1",
        "program-2",
    ]


async def test_a_refused_key_fails_the_text_instead_of_leaving_it_generating(pregen):
    import httpx
    import openai

    request = httpx.Request("POST", "http://test")
    refused = openai.AuthenticationError(
        "bad key, see https://provider.example/keys",
        response=httpx.Response(401, request=request),
        body=None,
    )
    llm = FakeLLMClient([refused] * 6)
    with pytest.raises(RuntimeError):
        await jobs.pregenerate_set(_ctx(llm), "req", SET_ID, STUDENT)
    statuses = {digest: status for _kind, digest, status in pregen.marks}
    assert statuses["hash:guideline:a"] == "failed"
