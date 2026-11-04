"""ТЗ Ф4 §8.7, WP10.2–10.3: the streaming adapter over `/responses` and the tool loop
(`T-AI3`–`T-AI6` on the provider level; ledger rows of `T-AI6` come with WP10.4)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, Literal

import httpx2
import orjson
import pytest

from quack.llm.errors import ProviderIncompleteError, ProviderRateLimitedError
from quack.llm.provider import ProviderRequest
from quack.llm.responses_client import ResponsesClient
from quack.llm.streaming import (
    OutputItemDone,
    StreamCompleted,
    StreamEvent,
    StreamRequest,
    StreamUsage,
    TextDelta,
)
from quack.llm.tool_loop import ToolLoopLimitError, ToolLoopResult, run_tool_loop

REASONING = {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc==", "summary": []}
COMMENTARY = {
    "type": "message",
    "id": "msg_c",
    "phase": "commentary",
    "role": "assistant",
    "content": [{"type": "output_text", "text": "Looking."}],
}
CALL = {
    "type": "function_call",
    "id": "fc_1",
    "call_id": "call_a",
    "name": "skill_history",
    "arguments": '{"skill":"S1","level":null}',
}
ANSWER = {
    "type": "message",
    "id": "msg_a",
    "role": "assistant",
    "content": [{"type": "output_text", "text": '{"ok":true}'}],
}
HEADERS = {"x-ratelimit-remaining-tokens": "3999000", "x-ratelimit-limit-tokens": "4000000"}


def _sse(*events: dict[str, Any]) -> bytes:
    return b"".join(
        b"event: " + e["type"].encode() + b"\ndata: " + orjson.dumps(e) + b"\n\n" for e in events
    )


def _completed(status: str = "completed") -> dict[str, Any]:
    return {
        "type": f"response.{status}",
        "response": {
            "id": "resp_1",
            "model": "muse-wonder-1.0",
            "status": status,
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 20},
                "output_tokens": 40,
                "output_tokens_details": {"reasoning_tokens": 30},
            },
        },
    }


def _stream_body(status: str = "completed") -> bytes:
    return _sse(
        {"type": "response.output_item.added", "item": {"id": "rs_1", "type": "reasoning"}},
        {"type": "response.output_item.done", "item": REASONING},
        {"type": "response.output_item.added", "item": {**COMMENTARY, "content": []}},
        {"type": "response.output_text.delta", "item_id": "msg_c", "delta": "Looking."},
        {"type": "response.output_item.done", "item": COMMENTARY},
        {"type": "response.output_item.added", "item": {**ANSWER, "content": []}},
        {"type": "response.output_text.delta", "item_id": "msg_a", "delta": '{"ok":'},
        {"type": "response.output_text.delta", "item_id": "msg_a", "delta": "true}"},
        {"type": "response.output_item.done", "item": ANSWER},
        _completed(status),
    )


class _Handler:
    def __init__(self, *answers: httpx2.Response) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def _client(handler: _Handler) -> ResponsesClient:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return ResponsesClient("http://meta.test/v1", "key", http_client=http)


def _ok(body: bytes) -> httpx2.Response:
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream", **HEADERS}, content=body
    )


BASE = ProviderRequest(
    model="muse-wonder-1.0",
    prompt="context",
    json_schema={"type": "object", "properties": {}, "additionalProperties": False},
    schema_name="TeacherOutputV2",
    temperature=0.0,
    timeout_sec=30.0,
    reasoning_effort="low",
    max_output_tokens=8192,
    prompt_cache_key="teacher@2",
)
TOOL = {"type": "function", "name": "skill_history", "parameters": {}, "strict": True}


async def _collect(stream: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    return [event async for event in stream]


async def test_stream_events_and_request_body() -> None:
    """WP10.2: text deltas only of the answer message; raw items; usage and headers last.
    The body has `stream`, `store: false`, `text.format`, tools with `tool_choice: auto`,
    the carried input items after the prompt; no `previous_response_id`, no `temperature`."""
    handler = _Handler(_ok(_stream_body()))
    request = StreamRequest(BASE, input_items=(REASONING, CALL), tools=(TOOL,))
    events = await _collect(_client(handler).stream_structured(request))

    assert [e.text for e in events if isinstance(e, TextDelta)] == ['{"ok":', "true}"]
    done = [e.item for e in events if isinstance(e, OutputItemDone)]
    assert done == [REASONING, COMMENTARY, ANSWER]
    last = events[-1]
    assert isinstance(last, StreamCompleted)
    assert last.status == "completed"
    assert last.usage == StreamUsage(100, 20, 40, 30)
    assert last.headers["x-ratelimit-remaining-tokens"] == "3999000"

    body = orjson.loads(handler.requests[0].content)
    assert body["stream"] is True and body["store"] is False
    assert body["text"]["format"]["strict"] is True
    assert body["tools"] == [TOOL] and body["tool_choice"] == "auto"
    assert body["input"][0] == {"role": "user", "content": "context"}
    assert body["input"][1:] == [REASONING, CALL]
    assert body["reasoning"] == {"effort": "low"}
    for absent in ("previous_response_id", "temperature", "response_format"):
        assert absent not in body


async def test_stream_without_tools_has_no_tool_choice() -> None:
    handler = _Handler(_ok(_stream_body()))
    await _collect(_client(handler).stream_structured(StreamRequest(BASE)))
    body = orjson.loads(handler.requests[0].content)
    assert "tools" not in body and "tool_choice" not in body


async def test_stream_incomplete_status() -> None:
    handler = _Handler(_ok(_stream_body("incomplete")))
    events = await _collect(_client(handler).stream_structured(StreamRequest(BASE)))
    last = events[-1]
    assert isinstance(last, StreamCompleted) and last.status == "incomplete"


async def test_stream_429_raises_with_headers() -> None:
    handler = _Handler(httpx2.Response(429, headers={"retry-after": "2", **HEADERS}, json={}))
    with pytest.raises(ProviderRateLimitedError) as caught:
        await _collect(_client(handler).stream_structured(StreamRequest(BASE)))
    assert caught.value.retry_after_sec == 2.0
    assert caught.value.headers["x-ratelimit-limit-tokens"] == "4000000"


# --- tool loop (Т-§11.5) ------------------------------------------------------------------


class _ScriptedStream:
    """Provider side of S13 for the loop: one scripted list of events per step."""

    def __init__(self, *steps: list[StreamEvent]) -> None:
        self.steps = list(steps)
        self.requests: list[StreamRequest] = []

    async def stream_structured(self, request: StreamRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        for event in self.steps.pop(0):
            yield event


def _done(status: Literal["completed", "incomplete"] = "completed") -> StreamCompleted:
    return StreamCompleted(status, StreamUsage(10, 0, 5, 3), "muse-wonder-1.0")


CALL_B = {**CALL, "id": "fc_2", "call_id": "call_b", "name": "prerequisites"}
ANSWER_STEP: list[StreamEvent] = [TextDelta('{"ok":true}'), OutputItemDone(ANSWER), _done()]


async def test_tool_loop_parallel_with_timeout() -> None:
    """T-AI5: two calls of a step run at once, each within `tool_timeout_ms`; a failing and a
    slow one return an error text as `function_call_output`; the turn goes on."""
    provider = _ScriptedStream(
        [OutputItemDone(REASONING), OutputItemDone(CALL), OutputItemDone(CALL_B), _done()],
        ANSWER_STEP,
    )
    started: list[str] = []
    both_started = asyncio.Event()

    async def execute(name: str, arguments: str) -> str:
        started.append(name)
        if len(started) == 2:
            both_started.set()
        await both_started.wait()  # deadlocks unless the two calls run concurrently
        if name == "skill_history":
            raise RuntimeError("boom")
        await asyncio.sleep(10)
        return "never"

    result = await run_tool_loop(
        provider,
        StreamRequest(BASE, tools=(TOOL,)),
        execute,
        tool_timeout_ms=50,
        max_steps=4,
        max_output_tokens_cap=16384,
    )
    assert isinstance(result, ToolLoopResult)
    assert result.text == '{"ok":true}'
    second = provider.requests[1].input_items
    outputs = {i["call_id"]: i["output"] for i in second if i["type"] == "function_call_output"}
    assert set(outputs) == {"call_a", "call_b"}
    assert "boom" in outputs["call_a"]
    assert "timed out" in outputs["call_b"]
    assert [i for i in second if i["type"] != "function_call_output"] == [REASONING, CALL, CALL_B]


async def test_tool_loop_commentary_not_shown() -> None:
    """T-AI4: commentary text never reaches `on_text`, nor the next step's input."""
    provider = _ScriptedStream(
        [OutputItemDone(COMMENTARY), OutputItemDone(CALL), _done()], ANSWER_STEP
    )
    shown: list[str] = []

    async def execute(name: str, arguments: str) -> str:
        return "2 times"

    async def on_text(text: str) -> None:
        shown.append(text)

    await run_tool_loop(
        provider,
        StreamRequest(BASE, tools=(TOOL,)),
        execute,
        tool_timeout_ms=1000,
        on_text=on_text,
        max_steps=4,
        max_output_tokens_cap=16384,
    )
    assert "".join(shown) == '{"ok":true}'
    assert COMMENTARY not in provider.requests[1].input_items


async def test_tool_loop_reports_tool_calls() -> None:
    """Т-§12.2 этап 1: the loop reports each tool name (for the SSE `status: tool`)."""
    provider = _ScriptedStream([OutputItemDone(CALL), _done()], ANSWER_STEP)
    names: list[str] = []

    async def execute(name: str, arguments: str) -> str:
        return "ok"

    async def on_tool(name: str) -> None:
        names.append(name)

    await run_tool_loop(
        provider,
        StreamRequest(BASE, tools=(TOOL,)),
        execute,
        tool_timeout_ms=1000,
        on_tool=on_tool,
        max_steps=4,
        max_output_tokens_cap=16384,
    )
    assert names == ["skill_history"]


async def test_incomplete_never_parsed_retry_doubled() -> None:
    """T-AI6: a cut answer is not returned; one retry with the doubled limit, at most the
    cap; cut twice → `ProviderIncompleteError`."""
    cut: list[StreamEvent] = [TextDelta('{"ok":'), _done("incomplete")]
    provider = _ScriptedStream(cut, ANSWER_STEP)
    result = await run_tool_loop(
        provider,
        StreamRequest(replace(BASE, max_output_tokens=8192)),
        _never,
        tool_timeout_ms=10,
        max_output_tokens_cap=12000,
        max_steps=4,
    )
    assert result.text == '{"ok":true}'
    assert result.retried_incomplete is True
    assert [r.base.max_output_tokens for r in provider.requests] == [8192, 12000]

    provider = _ScriptedStream(cut, cut)
    with pytest.raises(ProviderIncompleteError):
        await run_tool_loop(
            provider,
            StreamRequest(BASE),
            _never,
            tool_timeout_ms=10,
            max_output_tokens_cap=16384,
            max_steps=4,
        )


async def test_tool_loop_step_limit() -> None:
    """A model that keeps calling tools is stopped after `max_steps` (no endless loop)."""
    step: list[StreamEvent] = [OutputItemDone(CALL), _done()]
    provider = _ScriptedStream(*[list(step) for _ in range(5)])

    async def execute(name: str, arguments: str) -> str:
        return "ok"

    with pytest.raises(ToolLoopLimitError):
        await run_tool_loop(
            provider,
            StreamRequest(BASE, tools=(TOOL,)),
            execute,
            tool_timeout_ms=10,
            max_steps=3,
            max_output_tokens_cap=16384,
        )


async def _never(name: str, arguments: str) -> str:
    raise AssertionError("no tool call expected")
