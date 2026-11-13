"""ТЗ Ф4 WP10.2, WP10.3 inside the gateway (WP5 of Phase 3 stays, WP10 «adds what is missing»):
a streamed step of the teacher turn goes through the same admission, circuit breaker, pressure
flag and usage row as `generate_structured` (D-B-152). The turn graph takes the gateway stream as
its provider (seam S13 underneath)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.redis import llm_pressure_key
from quack.llm.errors import LLMRateLimitedError, LLMUnavailableError, ProviderUnavailableError
from quack.llm.gateway import LLMGateway
from quack.llm.streaming import StreamCompleted, StreamEvent, StreamingLLMProvider, TextDelta
from quack.llm.tool_loop import stream_step
from quack.prompts.build_manifest import write_manifest
from quack.prompts.registry import PromptRegistry
from tests.support.llm import ScriptedFailure, ScriptedLLMProvider

WONDER, SPARK = "m-wonder", "m-spark"
TOOL = {"type": "function", "name": "skill_history", "parameters": {}, "strict": True}


class StreamAnswer(BaseModel):
    label: str


@pytest.fixture
def prompts(tmp_path: Path) -> PromptRegistry:
    folder = tmp_path / "teacher"
    folder.mkdir()
    (folder / "v2.md").write_text("teacher: {{ text }}", encoding="utf-8")
    write_manifest(tmp_path)
    return PromptRegistry(tmp_path)


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "test",
        "llm_default_model": SPARK,
        "llm_role_models": {"teacher": WONDER, "teacher_fallback": SPARK},
        "llm_retry_base_ms": 1,
        "llm_retry_attempts": 1,
    }
    return Settings(**(base | over))


def _provider() -> ScriptedLLMProvider:
    provider = ScriptedLLMProvider()
    provider.respond("StreamAnswer", lambda _r: {"label": "ok"})
    return provider


def _gateway(
    pg_db: Db,
    provider: Any,
    prompts: PromptRegistry,
    redis: Redis | None = None,
    **over: Any,
) -> LLMGateway:
    return LLMGateway(provider, _settings(**over), prompts, db=pg_db, redis=redis)


def _stream(gw: LLMGateway, learner_id: UUID, turn_id: UUID) -> tuple[Any, StreamingLLMProvider]:
    request, prompt = gw.stream_request(
        "teacher",
        2,
        StreamAnswer,
        {"text": "context"},
        reasoning_effort="high",
        tools=(TOOL,),
    )
    return request, gw.streaming(
        "teacher", prompt, learner_id=learner_id, session_id=None, turn_id=turn_id
    )


async def _collect(events: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    return [event async for event in events]


async def _usage(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT role, model, prompt_version, status, input_tokens, output_tokens, effort "
        "FROM ops.llm_usage WHERE learner_id = %(l)s ORDER BY id",
        {"l": learner_id},
    )


async def test_stream_step_goes_through_the_gateway(pg_db: Db, prompts: PromptRegistry) -> None:
    provider = _provider()
    gw = _gateway(pg_db, provider, prompts)
    learner_id, turn_id = uuid4(), uuid4()
    request, stream = _stream(gw, learner_id, turn_id)

    events = await _collect(stream.stream_structured(request))

    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == '{"label":"ok"}'
    assert isinstance(events[-1], StreamCompleted)
    [sent] = provider.stream_calls
    assert sent.base.model == WONDER and sent.base.schema_name == "StreamAnswer"
    assert sent.base.prompt == "teacher: context"
    assert sent.base.reasoning_effort == "high" and sent.base.prompt_cache_key == "teacher@2"
    assert sent.base.json_schema["additionalProperties"] is False
    assert sent.tools == (TOOL,)
    [row] = await _usage(pg_db, learner_id)
    assert (row["role"], row["model"], row["prompt_version"]) == ("teacher", WONDER, "teacher@2")
    assert (row["status"], row["effort"]) == ("ok", "high")
    assert row["input_tokens"] > 0 and row["output_tokens"] > 0


async def test_unavailable_before_the_first_event_is_retried(
    pg_db: Db, prompts: PromptRegistry
) -> None:
    provider = _provider()
    provider.fail(ScriptedFailure("unavailable", schema_name="StreamAnswer", times=1))
    gw = _gateway(pg_db, provider, prompts)
    learner_id = uuid4()
    request, stream = _stream(gw, learner_id, uuid4())

    events = await _collect(stream.stream_structured(request))

    assert isinstance(events[-1], StreamCompleted)
    assert [r["status"] for r in await _usage(pg_db, learner_id)] == ["error", "ok"]


async def test_unavailable_after_retries_is_an_error(pg_db: Db, prompts: PromptRegistry) -> None:
    provider = _provider()
    provider.fail(ScriptedFailure("unavailable", schema_name="StreamAnswer", times=5))
    gw = _gateway(pg_db, provider, prompts)
    request, stream = _stream(gw, uuid4(), uuid4())

    with pytest.raises(LLMUnavailableError):
        await _collect(stream.stream_structured(request))
    assert len(provider.stream_calls) == 2  # one retry (`llm_retry_attempts`)


async def test_rate_limited_turn_is_not_retried_and_sets_pressure(
    pg_db: Db, prompts: PromptRegistry, redis: Redis
) -> None:
    provider = _provider()
    provider.fail(ScriptedFailure("rate_limited", schema_name="StreamAnswer", retry_after="7"))
    gw = _gateway(pg_db, provider, prompts, redis)
    learner_id = uuid4()
    request, stream = _stream(gw, learner_id, uuid4())

    with pytest.raises(LLMRateLimitedError):
        await _collect(stream.stream_structured(request))

    assert len(provider.stream_calls) == 1
    assert await redis.exists(llm_pressure_key(WONDER))
    assert [r["status"] for r in await _usage(pg_db, learner_id)] == ["error"]


async def test_bad_request_is_an_error(pg_db: Db, prompts: PromptRegistry) -> None:
    provider = _provider()
    provider.fail(ScriptedFailure("bad_request", schema_name="StreamAnswer"))
    gw = _gateway(pg_db, provider, prompts)
    request, stream = _stream(gw, uuid4(), uuid4())

    with pytest.raises(LLMUnavailableError) as raised:
        await _collect(stream.stream_structured(request))
    assert raised.value.reason == "bad_request" and len(provider.stream_calls) == 1


async def test_cut_step_is_passed_on_and_recorded(pg_db: Db, prompts: PromptRegistry) -> None:
    """The graph retries a cut step with the doubled limit (Т-§11.1); the gateway only
    passes it on and writes `incomplete`."""
    provider = _provider()
    provider.fail(ScriptedFailure("incomplete", schema_name="StreamAnswer"))
    gw = _gateway(pg_db, provider, prompts)
    learner_id = uuid4()
    request, stream = _stream(gw, learner_id, uuid4())

    events = await _collect(stream.stream_structured(request))

    last = events[-1]
    assert isinstance(last, StreamCompleted) and last.status == "incomplete"
    assert [r["status"] for r in await _usage(pg_db, learner_id)] == ["incomplete"]


async def test_open_circuit_short_circuits(pg_db: Db, prompts: PromptRegistry) -> None:
    provider = _provider()
    provider.fail(ScriptedFailure("unavailable", schema_name="StreamAnswer", times=10))
    gw = _gateway(pg_db, provider, prompts, circuit_failures=2, llm_retry_attempts=1)
    request, stream = _stream(gw, uuid4(), uuid4())
    with pytest.raises(LLMUnavailableError):
        await _collect(stream.stream_structured(request))  # two failures open the breaker
    calls = len(provider.stream_calls)

    with pytest.raises(LLMUnavailableError) as raised:
        await _collect(stream.stream_structured(request))

    assert raised.value.reason == "circuit_open" and len(provider.stream_calls) == calls


async def test_consumer_stop_releases_the_model_slot(pg_db: Db, prompts: PromptRegistry) -> None:
    """The graph stops a stream on a rejected head or a spoiler: the slot is free at once."""
    provider = _provider()
    gw = _gateway(pg_db, provider, prompts, llm_concurrency={"api": {WONDER: 1}})
    request, stream = _stream(gw, uuid4(), uuid4())

    events = stream.stream_structured(request)
    async for event in events:
        if isinstance(event, TextDelta):
            break
    await events.aclose()  # type: ignore[attr-defined]

    second = await asyncio.wait_for(_collect(stream.stream_structured(request)), 1)
    assert isinstance(second[-1], StreamCompleted)


async def test_teacher_waits_for_a_slot_no_longer_than_admission_wait(
    pg_db: Db, prompts: PromptRegistry
) -> None:
    provider = _provider()
    gate = provider.hold("StreamAnswer")
    gw = _gateway(
        pg_db, provider, prompts, llm_concurrency={"api": {WONDER: 1}}, teacher_admission_wait_ms=50
    )
    request, stream = _stream(gw, uuid4(), uuid4())
    first = asyncio.create_task(_collect(stream.stream_structured(request)))
    await gate.arrived.wait()

    with pytest.raises(LLMRateLimitedError):
        await _collect(stream.stream_structured(request))

    gate.release.set()
    assert isinstance((await first)[-1], StreamCompleted)


class _BreaksMidStream:
    """Gives one text delta, then the connection drops."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete_structured(self, request: Any) -> Any:  # pragma: no cover - unused
        raise AssertionError("not called")

    async def stream_structured(self, request: Any) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        yield TextDelta('{"label":')
        raise ProviderUnavailableError("connection dropped")


async def test_failure_after_the_first_event_is_not_retried(
    pg_db: Db, prompts: PromptRegistry
) -> None:
    """The learner may have seen the first text: the turn fails, no second stream."""
    provider = _BreaksMidStream()
    gw = _gateway(pg_db, provider, prompts, llm_retry_attempts=3)
    request, stream = _stream(gw, uuid4(), uuid4())

    with pytest.raises(LLMUnavailableError):
        await _collect(stream.stream_structured(request))
    assert provider.calls == 1


async def test_graph_step_stopped_by_the_consumer_frees_the_slot(
    pg_db: Db, prompts: PromptRegistry
) -> None:
    """`stream_step` closes the stream when its text callback raises (a rejected head)."""
    provider = _provider()
    gw = _gateway(pg_db, provider, prompts, llm_concurrency={"api": {WONDER: 1}})
    request, stream = _stream(gw, uuid4(), uuid4())

    async def reject(_text: str) -> None:
        raise RuntimeError("head rejected")

    with pytest.raises(RuntimeError):
        await stream_step(stream, request, reject)
    gw.settings.teacher_admission_wait_ms = 50  # the slot must be free now, not after GC
    second = await _collect(stream.stream_structured(request))
    assert isinstance(second[-1], StreamCompleted)


# --- audit fixes of 2026-11-13: probe without a verdict, a stream without its end event -----


async def test_stream_probe_without_a_slot_does_not_lock_the_breaker(
    pg_db: Db, prompts: PromptRegistry
) -> None:
    """The breaker lets one probe through; if the probe finds no free slot of the model
    (`LLMRateLimitedError`), it ends without a verdict and the next call probes again."""
    provider = _provider()
    provider.fail(ScriptedFailure("unavailable", schema_name="StreamAnswer", times=1))
    gw = _gateway(
        pg_db,
        provider,
        prompts,
        llm_concurrency={"api": {WONDER: 1}},
        teacher_admission_wait_ms=50,
        circuit_failures=1,
        circuit_open_s=0.2,
        llm_retry_attempts=0,
    )
    request, stream = _stream(gw, uuid4(), uuid4())
    with pytest.raises(LLMUnavailableError):
        await _collect(stream.stream_structured(request))  # opens the breaker
    await asyncio.sleep(0.3)

    # Another role holds the only slot of the model while the probe arrives.
    holder = asyncio.create_task(gw._semaphore(WONDER).acquire())  # pyright: ignore[reportPrivateUsage]
    await holder
    with pytest.raises(LLMRateLimitedError):
        await _collect(stream.stream_structured(request))  # the probe: no slot
    gw._semaphore(WONDER).release()  # pyright: ignore[reportPrivateUsage]

    second = await _collect(stream.stream_structured(request))  # must reach the provider
    assert isinstance(second[-1], StreamCompleted)


class _NoEndEvent:
    """Gives the text and stops without `response.completed`."""

    async def complete_structured(self, request: Any) -> Any:  # pragma: no cover - unused
        raise AssertionError("not called")

    async def stream_structured(self, request: Any) -> AsyncIterator[StreamEvent]:
        yield TextDelta('{"label":')


async def test_stream_without_the_end_event_has_a_usage_row(
    pg_db: Db, prompts: PromptRegistry
) -> None:
    gw = _gateway(pg_db, _NoEndEvent(), prompts)
    learner_id = uuid4()
    request, stream = _stream(gw, learner_id, uuid4())

    events = await _collect(stream.stream_structured(request))

    assert [type(e) for e in events] == [TextDelta]
    assert [r["status"] for r in await _usage(pg_db, learner_id)] == ["incomplete"]
