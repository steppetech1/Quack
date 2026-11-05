"""ТЗ Ф4 WP10.4 (Т-§11.4, D-B-197..D-B-199): a row of `ops.llm_usage` with the cost of every
call (text and non-text roles) and the budgets in dollars, over the Phase 4 database (`pg_db`)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from quack.config import ModelPrice, Settings
from quack.db import Db
from quack.llm.errors import LLMBudgetExceededError
from quack.llm.gateway import LLMGateway
from quack.llm.media import TranscribeResponse
from quack.llm.provider import ProviderRequest, ProviderResponse
from quack.llm.usage import call_cost_usd
from quack.prompts.registry import PromptRegistry, PromptTemplate

PRICES = {
    "m": ModelPrice(input=2.0, cached_input=0.5, output=8.0),
    "voice-m": ModelPrice(input=0, cached_input=0, output=0, per_minute=0.01),
}


class Answer(BaseModel):
    label: str


class _Registry(PromptRegistry):
    def load(self, role: str, version: int) -> PromptTemplate:
        return PromptTemplate(role, version, "Answer.", "0" * 64)


class _Provider:
    """100 tokens in (40 cached), 50 out (10 of them reasoning); 2 minutes of audio."""

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse('{"label": "ok"}', 100, 50, 40, request.model, reasoning_tokens=10)

    async def transcribe(self, request: Any) -> TranscribeResponse:
        return TranscribeResponse("текст", 0, 0, 0, request.model, audio_duration_ms=120_000)

    async def segment(self, request: Any) -> ProviderResponse:
        raise AssertionError

    async def illustrate(self, request: Any) -> ProviderResponse:
        raise AssertionError


def _gateway(pg_db: Db, **over: Any) -> LLMGateway:
    settings = Settings(
        env="test",
        llm_role_models={"task_generator": "m", "teacher": "m", "voice": "voice-m"},
        llm_prices=PRICES,
        **over,
    )
    return LLMGateway(_Provider(), settings, _Registry(), db=pg_db)


async def _ask(gw: LLMGateway, role: str, learner: UUID, session: UUID | None) -> None:
    await gw.generate_structured(role, 1, Answer, {}, learner, session, None, f"k:{uuid4()}")


async def test_text_and_voice_calls_write_a_usage_row_with_cost(pg_db: Db) -> None:
    gw, learner, session = _gateway(pg_db), uuid4(), uuid4()

    await _ask(gw, "task_generator", learner, session)
    await gw.transcribe(b"RIFF", learner_id=learner, session_id=session)

    rows = await pg_db.fetch_all(
        "SELECT role, model, input_tokens, cached_input_tokens, output_tokens, "
        "reasoning_tokens, cost_usd::float8 AS cost, status, retried "
        "FROM ops.llm_usage WHERE learner_id = %(l)s ORDER BY id",
        {"l": learner},
    )
    text_row, voice_row = rows
    assert (text_row["role"], text_row["model"], text_row["status"]) == (
        "task_generator",
        "m",
        "ok",
    )
    assert (text_row["input_tokens"], text_row["cached_input_tokens"]) == (100, 40)
    assert (text_row["output_tokens"], text_row["reasoning_tokens"]) == (50, 10)
    assert text_row["cost"] == pytest.approx((60 * 2.0 + 40 * 0.5 + 50 * 8.0) / 1e6)
    assert voice_row["role"] == "voice" and voice_row["cost"] == pytest.approx(0.02)
    assert not text_row["retried"]


async def test_budgets_in_dollars(pg_db: Db) -> None:
    gw = _gateway(pg_db, session_agent_budget_usd=0.5, learner_daily_budget_usd=3.0)
    learner, session = uuid4(), uuid4()
    sql = (
        "INSERT INTO ops.llm_usage (learner_id, session_id, role, model, status, cost_usd) "
        "VALUES (%(l)s, %(s)s, %(r)s, 'm', 'ok', %(c)s)"
    )
    await pg_db.execute(sql, {"l": learner, "s": session, "r": "task_generator", "c": 0.6})
    await pg_db.execute(sql, {"l": learner, "s": session, "r": "teacher", "c": 100})

    with pytest.raises(LLMBudgetExceededError) as session_over:
        await _ask(gw, "task_generator", learner, session)
    assert (session_over.value.scope, session_over.value.unit) == ("session", "usd")
    await _ask(gw, "task_generator", learner, uuid4())  # another session of a learner under 3 $
    await _ask(gw, "teacher", learner, session)  # the teacher is counted, not limited

    other = uuid4()
    await pg_db.execute(sql, {"l": other, "s": uuid4(), "r": "task_generator", "c": 3.0})
    with pytest.raises(LLMBudgetExceededError) as daily_over:
        await _ask(gw, "task_generator", other, uuid4())
    assert daily_over.value.scope == "daily"


def test_cost_without_prices_is_zero_and_budgets_stay_quiet() -> None:
    settings = Settings(env="test")
    cost = call_cost_usd(settings, "unpriced", input_tokens=10, cached_tokens=0, output_tokens=5)
    assert cost == 0.0


async def test_pool_tightens_then_keeps_the_models_for_judges(pg_db: Db) -> None:
    """D-B-211: a pool of 1000 $ with 920 $ spent is `tight` (the budgets of Т-§11.4); with
    960 $ spent it is `judges_only` — a learner without `meta_judge` is refused every role, one
    click (`preference.set`) lets them in."""
    from quack.events.log import NewEvent, append_events
    from quack.llm.access import reset_pool_cache

    before = await pg_db.fetch_one(
        "SELECT COALESCE(sum(cost_usd), 0)::float8 AS s FROM ops.llm_usage", {}
    )
    pool = 1000.0 + (float(before["s"]) if before else 0.0)
    over = {"llm_pool_budget_usd": pool, "llm_pool_cache_s": 0.0}
    gw, learner, session = _gateway(pg_db, **over), uuid4(), uuid4()
    await pg_db.execute(
        "INSERT INTO core.learners (learner_id, email) VALUES (%(l)s, %(e)s)",
        {"l": learner, "e": f"{learner}@test.invalid"},
    )
    spend = (
        "INSERT INTO ops.llm_usage (learner_id, session_id, role, model, status, cost_usd) "
        "VALUES (%(l)s, %(s)s, 'teacher', 'm', 'ok', %(c)s)"
    )
    await pg_db.execute(spend, {"l": uuid4(), "s": uuid4(), "c": 919.0})
    await pg_db.execute(spend, {"l": learner, "s": session, "c": 1.0})
    reset_pool_cache()
    await pg_db.execute(
        "INSERT INTO ops.llm_usage (learner_id, session_id, role, model, status, cost_usd) "
        "VALUES (%(l)s, %(s)s, 'task_generator', 'm', 'ok', 0.6)",
        {"l": learner, "s": session},
    )
    with pytest.raises(LLMBudgetExceededError) as tight:
        await _ask(gw, "task_generator", learner, session)  # 0.6 $ > the tight 0.5 $
    assert tight.value.scope == "session"

    await pg_db.execute(spend, {"l": uuid4(), "s": uuid4(), "c": 40.0})
    with pytest.raises(LLMBudgetExceededError) as judges:
        await _ask(gw, "teacher", learner, session)
    assert judges.value.scope == "pool"

    judge = NewEvent(
        type="preference.set",
        payload={"key": "meta_judge", "value": True},
        actor="learner",
        idempotency_key=f"preference:{uuid4()}",
    )
    await append_events(pg_db, learner, [judge])
    await _ask(gw, "teacher", learner, session)
