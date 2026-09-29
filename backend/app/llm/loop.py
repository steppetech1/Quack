"""Tool-calling loop: model -> tool call -> result back to model.

Drives ``client.stream`` until the model stops requesting tools, yielding
``StreamEvent``s as they happen. The final yielded item is a ``LoopEnd`` —
deliberately not a ``StreamEvent`` (it carries the accumulated text, tool
results, the calls themselves and the step count for the calling agent to
build its own ``Done``), so the return type is annotated
``AsyncIterator[StreamEvent | LoopEnd]``.

Provider errors (`openai.APIError`, `httpx.HTTPError`) pass through
``LLMClient`` untouched by contract; here they end the stream as
``StreamError(llm_unavailable)`` instead of breaking it with an exception
(docs/tz/phase3-agents.md §5.2 F16).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import openai
import structlog

from app.errors import LLMUnavailable
from app.llm.client import LLMLike
from app.llm.tools import ToolCtx, ToolPayload, ToolRegistry
from app.schemas.chat import StreamError, StreamEvent, TextDelta, ToolCall, ToolResult
from app.schemas.llm import LLMMessage, ModelSlot, ToolCallOut

_logger = structlog.get_logger(__name__)


@dataclass
class LoopEnd:
    text_full: str
    tool_results: list[ToolResult]
    steps: int
    tool_calls: list[ToolCall] = field(default_factory=list)


def _safe_json(value: object) -> str:
    return json.dumps(value, default=str)


async def _call(registry: ToolRegistry, call: ToolCall, ctx: ToolCtx) -> ToolResult:
    started = time.perf_counter()
    if call.tool not in registry.names():
        _logger.info("tool_denied", tool=call.tool, reason="not_in_registry")
        return ToolResult(
            type="tool_result",
            tool=call.tool,
            call_id=call.call_id,
            data=None,
            error=f"unknown tool {call.tool!r}",
        )
    try:
        data = await registry.call(call.tool, call.args, ctx)
        model_data = None
        if isinstance(data, ToolPayload):
            data, model_data = data.data, data.model_data
        result = ToolResult(
            type="tool_result",
            tool=call.tool,
            call_id=call.call_id,
            data=data,
            model_data=model_data,
            error=None,
        )
        _logger.info(
            "tool_called",
            tool=call.tool,
            call_id=call.call_id,
            ok=True,
            ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return result
    except Exception as exc:  # noqa: BLE001 — a tool error is a result, not a crash
        _logger.info(
            "tool_called",
            tool=call.tool,
            call_id=call.call_id,
            ok=False,
            error_type=type(exc).__name__,
            ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return ToolResult(
            type="tool_result",
            tool=call.tool,
            call_id=call.call_id,
            data=None,
            error=str(exc),
        )


async def run_tool_loop(
    client: LLMLike,
    registry: ToolRegistry,
    messages: list[LLMMessage],
    slot: ModelSlot,
    ctx: ToolCtx,
    max_steps: int = 6,
) -> AsyncIterator[StreamEvent | LoopEnd]:
    local_messages = list(messages)
    text_full = ""
    tool_results: list[ToolResult] = []
    tool_calls: list[ToolCall] = []
    steps = 0

    while True:
        steps += 1
        if steps > max_steps:
            yield StreamError(
                type="error",
                code="tool_loop_limit",
                message=f"tool loop exceeded max_steps={max_steps}",
            )
            return

        tools = registry.schemas() or None
        step_calls: list[ToolCall] = []
        step_text = ""
        try:
            async for event in client.stream(local_messages, slot, tools=tools):
                if isinstance(event, TextDelta):
                    if not step_text and text_full and not text_full[-1].isspace():
                        # A new round after a tool call is a new paragraph; glued to the
                        # last one it reads «Записываю.Записал».
                        gap = chr(10) * 2
                        event = event.model_copy(update={"text": gap + event.text})
                    step_text += event.text
                    text_full += event.text
                    yield event
                elif isinstance(event, ToolCall):
                    step_calls.append(event)
                    yield event
                else:
                    yield event
        except (LLMUnavailable, openai.APIError, httpx.HTTPError) as exc:
            # The provider's words carry dashboard links and request ids: log them, and
            # give the student a sentence that is ours.
            _logger.warning(
                "llm_stream_failed", error=str(exc), kind=type(exc).__name__
            )
            yield StreamError(
                type="error",
                code="llm_unavailable",
                message="Ассистент сейчас недоступен",
            )
            return

        if not step_calls:
            yield LoopEnd(
                text_full=text_full,
                tool_results=tool_results,
                steps=steps,
                tool_calls=tool_calls,
            )
            return

        tool_calls.extend(step_calls)
        local_messages.append(
            LLMMessage(
                role="assistant",
                # What the model said before the call stays in its history, or the next
                # round does not know it already spoke and says the same again.
                content=step_text.strip() or None,
                tool_calls=[
                    ToolCallOut(call_id=call.call_id, name=call.tool, args=call.args)
                    for call in step_calls
                ],
            )
        )

        for call in step_calls:
            result = await _call(registry, call, ctx)
            tool_results.append(result)
            yield result
            local_messages.append(
                LLMMessage(
                    role="tool",
                    content=_safe_json(
                        {
                            "data": result.data
                            if result.model_data is None
                            else result.model_data,
                            "error": result.error,
                        }
                    ),
                    tool_call_id=call.call_id,
                )
            )
