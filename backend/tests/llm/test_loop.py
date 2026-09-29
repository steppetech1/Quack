"""Tool-calling loop (docs/tz/30-B2.md §4.3, §6, tests/llm/test_loop.py)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from app.errors import LLMUnavailable
from app.llm.fake import FakeLLMClient
from app.llm.loop import LoopEnd, run_tool_loop
from app.llm.tools import ToolCtx, ToolPayload, ToolRegistry, tool
from app.schemas.chat import StreamError, TextDelta, ToolCall, ToolResult
from app.schemas.llm import LLMMessage

pytestmark = pytest.mark.phase1


class _EchoArgs(BaseModel):
    text: str = Field(description="text to echo back")


@tool(name="echo", description="echoes text back")
async def _echo(args: _EchoArgs, ctx: ToolCtx) -> dict:
    return {"echo": args.text}


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_echo)
    return registry


def _ctx() -> ToolCtx:
    return ToolCtx(student_id="s1", deps=None, request_id="r1")


async def test_loop_yields_tool_call_result_text_then_loop_end():
    client = FakeLLMClient(
        [
            [ToolCall(tool="echo", args={"text": "hi"}, call_id="c1")],
            [TextDelta(text="done")],
        ]
    )

    events = [
        event
        async for event in run_tool_loop(
            client, _registry(), [LLMMessage(role="user", content="go")], "chat", _ctx()
        )
    ]

    assert isinstance(events[0], ToolCall)
    assert isinstance(events[1], ToolResult)
    assert events[1].data == {"echo": "hi"}
    assert isinstance(events[2], TextDelta)
    assert isinstance(events[3], LoopEnd)

    second_call_messages = client.calls[1].messages
    assert any(
        m.role == "assistant" and m.tool_calls and m.tool_calls[0].call_id == "c1"
        for m in second_call_messages
    )
    assert any(
        m.role == "tool" and m.tool_call_id == "c1" for m in second_call_messages
    )


async def test_unknown_tool_call_yields_error_result_and_loop_continues():
    client = FakeLLMClient(
        [
            [ToolCall(tool="unknown", args={}, call_id="c1")],
            [TextDelta(text="ok")],
        ]
    )

    events = [
        event
        async for event in run_tool_loop(
            client, _registry(), [LLMMessage(role="user", content="go")], "chat", _ctx()
        )
    ]

    tool_result = next(e for e in events if isinstance(e, ToolResult))
    assert tool_result.error is not None
    assert any(isinstance(e, TextDelta) for e in events)
    assert isinstance(events[-1], LoopEnd)


async def test_exceeding_max_steps_yields_tool_loop_limit_error():
    client = FakeLLMClient(
        [
            [ToolCall(tool="echo", args={"text": f"s{i}"}, call_id=f"c{i}")]
            for i in range(7)
        ]
    )

    events = [
        event
        async for event in run_tool_loop(
            client,
            _registry(),
            [LLMMessage(role="user", content="go")],
            "chat",
            _ctx(),
            max_steps=6,
        )
    ]

    last = events[-1]
    assert isinstance(last, StreamError)
    assert last.code == "tool_loop_limit"


async def test_llm_unavailable_yields_llm_unavailable_error():
    client = FakeLLMClient([LLMUnavailable("down")])

    events = [
        event
        async for event in run_tool_loop(
            client, _registry(), [LLMMessage(role="user", content="go")], "chat", _ctx()
        )
    ]

    last = events[-1]
    assert isinstance(last, StreamError)
    assert last.code == "llm_unavailable"


async def test_empty_registry_passes_no_tools_to_client():
    client = FakeLLMClient([[TextDelta(text="hi")]])

    [
        event
        async for event in run_tool_loop(
            client,
            ToolRegistry(),
            [LLMMessage(role="user", content="go")],
            "chat",
            _ctx(),
        )
    ]

    assert client.calls[0].tools is None


async def test_invalid_tool_args_yield_error_result_not_exception():
    client = FakeLLMClient(
        [
            [ToolCall(tool="echo", args={}, call_id="c1")],
            [TextDelta(text="done")],
        ]
    )

    events = [
        event
        async for event in run_tool_loop(
            client, _registry(), [LLMMessage(role="user", content="go")], "chat", _ctx()
        )
    ]

    tool_result = next(e for e in events if isinstance(e, ToolResult))
    assert tool_result.error is not None


class _BigArgs(BaseModel):
    limit: int = Field(default=5, description="how many rows")


@tool(name="big", description="returns a large result with a compact projection")
async def _big(args: _BigArgs, ctx: ToolCtx) -> ToolPayload:
    return ToolPayload(
        data={"rows": [{"id": i, "blob": "x" * 200} for i in range(args.limit)]},
        model_data={"ids": list(range(args.limit))},
    )


async def test_tool_payload_sends_the_compact_projection_to_the_model():
    """Карточки рендерит фронт, поэтому `data` полный; в контекст модели
    идёт сжатая проекция (`run_matching` иначе — 3–4 КБ на каждый ход)."""
    registry = ToolRegistry()
    registry.register(_big)
    client = FakeLLMClient(
        [
            [ToolCall(tool="big", args={"limit": 3}, call_id="c1")],
            [TextDelta(text="done")],
        ]
    )

    results = [
        event
        async for event in run_tool_loop(
            client, registry, [LLMMessage(role="user", content="go")], "chat", _ctx()
        )
        if isinstance(event, ToolResult)
    ]
    assert results[0].data["rows"][0]["id"] == 0
    assert results[0].model_data == {"ids": [0, 1, 2]}
    # на фронт model_data не уходит
    assert "model_data" not in results[0].model_dump_json()

    tool_message = next(
        message for message in client.tool_messages() if message.role == "tool"
    )
    assert '"ids"' in (tool_message.content or "")
    assert "xxxx" not in (tool_message.content or "")


async def test_text_before_a_tool_call_is_kept_and_the_next_round_is_a_new_paragraph():
    """«Записываю.Записал»: two rounds glued into one line, the second repeating."""
    client = FakeLLMClient(
        [
            [
                TextDelta(text="Записываю."),
                ToolCall(tool="echo", args={"text": "hi"}, call_id="c1"),
            ],
            [TextDelta(text="Записал.")],
        ]
    )

    events = [
        event
        async for event in run_tool_loop(
            client, _registry(), [LLMMessage(role="user", content="go")], "chat", _ctx()
        )
    ]

    end = events[-1]
    assert isinstance(end, LoopEnd)
    assert end.text_full == "Записываю." + chr(10) * 2 + "Записал."
    # The model sees what it already said before the tool call
    said = [
        m for m in client.calls[1].messages if m.role == "assistant" and m.tool_calls
    ]
    assert said[0].content == "Записываю."
