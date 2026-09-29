"""LLMClient against a monkeypatched openai transport: circuit breaker,
rate-limit, structured/stream plumbing (docs/tz/30-B2.md §3.2-§3.4, §6,
tests/llm/test_client.py).

No live provider: ``AsyncOpenAI.chat.completions.create`` is replaced with a
scripted fake per test, and Redis is the ``fakeredis`` fixture from
``tests/conftest.py``. The breaker/rate-limit tests mock ``time.monotonic``
and ``asyncio.sleep`` so TTL/backoff waits never really elapse.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import SecretStr

from app import keys
from app.config import Settings
from app.errors import LLMUnavailable
from app.llm.client import LLMClient
from app.schemas.chat import TextDelta, ToolCall
from app.schemas.llm import LLMMessage

pytestmark = pytest.mark.phase1


def _settings(**overrides) -> Settings:
    return Settings(
        LLM_API_KEY=SecretStr("test-key"),
        LLM_BASE_URL="http://test",
        **overrides,
    )


class FakeTransport:
    """Replaces ``AsyncOpenAI.chat.completions.create``: pops one scripted
    item per call. An ``Exception`` is raised; a plain object is returned
    as-is (for ``complete``); a ``list`` is wrapped into an async iterable
    of chunks (for ``stream``)."""

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, list):

            async def _chunks():
                for chunk in item:
                    yield chunk

            return _chunks()
        return item


def _install(monkeypatch, client: LLMClient, script: list[object]) -> FakeTransport:
    transport = FakeTransport(script)
    monkeypatch.setattr(client._client.chat.completions, "create", transport.create)
    return transport


def _timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx.Request("POST", "http://test"))


def _status_error(status_code: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "http://test")
    response = httpx.Response(status_code, request=request)
    return openai.APIStatusError("boom", response=response, body=None)


def _completion_response(text: str = "ok") -> SimpleNamespace:
    message = SimpleNamespace(content=text, tool_calls=[])
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None)


def _mock_fast_clock(monkeypatch) -> None:
    """Advances a fake monotonic clock by 1000s on every read, so any
    deadline computed as ``now + wait_s`` (wait_s <= 30) is already passed
    by the very next read — the rate-limit wait loop's real 5s/30s waits
    never happen. ``asyncio.sleep`` is also a no-op for the same reason."""
    value = [0.0]

    def fake_monotonic() -> float:
        value[0] += 1000.0
        return value[0]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


async def test_three_timeouts_trip_breaker_to_down_with_bounded_ttl(redis, monkeypatch):
    client = LLMClient(_settings(), redis)
    _install(
        monkeypatch, client, [_timeout_error(), _timeout_error(), _timeout_error()]
    )

    for _ in range(3):
        with pytest.raises(openai.APITimeoutError):
            await client.complete([LLMMessage(role="user", content="hi")], "chat")

    assert await client.status() == "down"
    ttl = await redis.ttl(keys.llm_status())
    assert 0 < ttl <= 120


async def test_down_status_blocks_calls_without_hitting_transport(redis, monkeypatch):
    client = LLMClient(_settings(), redis)
    transport = _install(
        monkeypatch, client, [_timeout_error(), _timeout_error(), _timeout_error()]
    )

    for _ in range(3):
        with pytest.raises(openai.APITimeoutError):
            await client.complete([LLMMessage(role="user", content="hi")], "chat")
    assert len(transport.calls) == 3

    with pytest.raises(LLMUnavailable):
        await client.complete([LLMMessage(role="user", content="hi")], "chat")

    assert len(transport.calls) == 3


async def test_status_degrades_after_ttl_expiry_then_recovers_on_success(
    redis, monkeypatch
):
    client = LLMClient(_settings(), redis)
    transport = _install(
        monkeypatch,
        client,
        [_timeout_error(), _timeout_error(), _timeout_error(), _completion_response()],
    )

    for _ in range(3):
        with pytest.raises(openai.APITimeoutError):
            await client.complete([LLMMessage(role="user", content="hi")], "chat")
    assert await client.status() == "down"

    await redis.delete(keys.llm_status())
    assert await client.status() == "degraded"

    result = await client.complete([LLMMessage(role="user", content="hi")], "chat")
    assert result.text == "ok"
    assert await client.status() == "ok"
    assert len(transport.calls) == 4


async def test_force_down_short_circuits_without_calling_transport(redis, monkeypatch):
    client = LLMClient(_settings(LLM_FORCE_DOWN=True), redis)
    transport = _install(monkeypatch, client, [])

    assert await client.status() == "down"
    with pytest.raises(LLMUnavailable):
        await client.complete([LLMMessage(role="user", content="hi")], "chat")

    assert transport.calls == []


async def test_single_error_degrades_but_calls_still_pass(redis, monkeypatch):
    client = LLMClient(_settings(), redis)
    _install(monkeypatch, client, [_timeout_error(), _completion_response()])

    with pytest.raises(openai.APITimeoutError):
        await client.complete([LLMMessage(role="user", content="hi")], "chat")
    assert await client.status() == "degraded"

    result = await client.complete([LLMMessage(role="user", content="hi")], "chat")
    assert result.text == "ok"


async def test_400_errors_do_not_increment_breaker_counter(redis, monkeypatch):
    client = LLMClient(_settings(), redis)
    _install(
        monkeypatch,
        client,
        [_status_error(400), _status_error(400), _status_error(400)],
    )

    for _ in range(3):
        with pytest.raises(openai.APIStatusError):
            await client.complete([LLMMessage(role="user", content="hi")], "chat")

    assert await client.status() == "ok"


async def test_bulk_rate_limit_raises_llm_unavailable_on_third_call_in_window(
    redis, monkeypatch
):
    client = LLMClient(_settings(LLM_RPM_BULK=2), redis)
    _install(
        monkeypatch,
        client,
        [_completion_response(), _completion_response(), _completion_response()],
    )
    _mock_fast_clock(monkeypatch)

    await client.complete([LLMMessage(role="user", content="hi")], "bulk")
    await client.complete([LLMMessage(role="user", content="hi")], "bulk")

    with pytest.raises(LLMUnavailable, match="rate limit"):
        await client.complete([LLMMessage(role="user", content="hi")], "bulk")


async def test_stream_emits_text_deltas_and_one_tool_call_from_chunked_args(
    redis, monkeypatch
):
    client = LLMClient(_settings(), redis)

    def _chunk(*, content=None, tool_call_deltas=None, finish_reason=None):
        delta = SimpleNamespace(
            content=content,
            tool_calls=tool_call_deltas,
            reasoning_content=None,
            reasoning=None,
        )
        choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
        return SimpleNamespace(choices=[choice], usage=None)

    def _tool_call_delta(index, *, call_id=None, name=None, arguments=None):
        function = SimpleNamespace(name=name, arguments=arguments)
        return SimpleNamespace(index=index, id=call_id, function=function)

    chunks = [
        _chunk(content="Hello"),
        _chunk(content=" world"),
        _chunk(
            tool_call_deltas=[
                _tool_call_delta(0, call_id="c1", name="get_time", arguments='{"a":')
            ]
        ),
        _chunk(
            tool_call_deltas=[_tool_call_delta(0, arguments="1}")],
            finish_reason="tool_calls",
        ),
    ]
    _install(monkeypatch, client, [chunks])

    events = [
        event
        async for event in client.stream(
            [LLMMessage(role="user", content="hi")], "chat"
        )
    ]

    text_events = [e for e in events if isinstance(e, TextDelta)]
    tool_events = [e for e in events if isinstance(e, ToolCall)]
    assert [e.text for e in text_events] == ["Hello", " world"]
    assert len(tool_events) == 1
    assert tool_events[0].tool == "get_time"
    assert tool_events[0].call_id == "c1"
    assert tool_events[0].args == {"a": 1}


async def test_complete_passes_tools_through_and_uses_model_for_slot(
    redis, monkeypatch
):
    settings = _settings(MODEL_CHAT="chat-model", MODEL_BULK="bulk-model")
    client = LLMClient(settings, redis)
    transport = _install(monkeypatch, client, [_completion_response()])
    tools = [{"type": "function", "function": {"name": "get_time", "parameters": {}}}]

    await client.complete([LLMMessage(role="user", content="hi")], "bulk", tools=tools)

    assert transport.calls[0]["tools"] == tools
    assert transport.calls[0]["model"] == "bulk-model"


async def test_a_refused_key_trips_the_breaker(redis, monkeypatch):
    """401 is the provider unusable for everyone; /health must not keep saying ok."""
    client = LLMClient(_settings(), redis)
    _install(
        monkeypatch,
        client,
        [_status_error(401), _status_error(401), _status_error(401)],
    )

    for _ in range(3):
        with pytest.raises(openai.APIStatusError):
            await client.complete([LLMMessage(role="user", content="hi")], "chat")

    assert await client.status() == "down"


async def test_without_a_key_the_client_starts_and_reports_down(redis):
    client = LLMClient(
        Settings(LLM_API_KEY=SecretStr(""), LLM_BASE_URL="http://test"), redis
    )
    assert await client.status() == "down"
    with pytest.raises(LLMUnavailable):
        await client.complete([LLMMessage(role="user", content="hi")], "chat")


async def test_thinking_switch_goes_in_extra_body_per_slot(redis, monkeypatch):
    client = LLMClient(
        _settings(LLM_THINKING_CHAT="disabled", LLM_THINKING_BULK="enabled"), redis
    )
    transport = _install(
        monkeypatch, client, [_completion_response(), _completion_response()]
    )

    await client.complete([LLMMessage(role="user", content="hi")], "chat")
    await client.complete([LLMMessage(role="user", content="hi")], "bulk")

    assert transport.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert transport.calls[1]["extra_body"] == {"thinking": {"type": "enabled"}}


async def test_thinking_is_not_sent_when_unset(redis, monkeypatch):
    client = LLMClient(_settings(LLM_THINKING_CHAT=None, LLM_THINKING_BULK=None), redis)
    transport = _install(monkeypatch, client, [_completion_response()])
    await client.complete([LLMMessage(role="user", content="hi")], "chat")
    assert "extra_body" not in transport.calls[0]


async def test_json_object_mode_puts_the_schema_in_the_prompt(redis, monkeypatch):
    from pydantic import BaseModel

    class Answer(BaseModel):
        word: str

    client = LLMClient(_settings(LLM_STRUCTURED_MODE="json_object"), redis)
    transport = _install(
        monkeypatch, client, [_completion_response('{"word": "quack"}')]
    )

    out = await client.structured(
        [LLMMessage(role="user", content="say a word")], Answer, "bulk"
    )

    assert out.word == "quack"
    call = transport.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert "json" in call["messages"][-1]["content"].lower()
    assert '"word"' in call["messages"][-1]["content"]
