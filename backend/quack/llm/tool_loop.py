"""The loop «model ⇄ tools» over a streaming provider (Т-§11.5; ТЗ Ф4 WP10.3).

Each step streams one `/responses` call. A step with `function_call` items runs all its
calls at once, each within `tool_timeout_ms`; a failing or late call returns an error text
as its `function_call_output` and the loop goes on. The next step's input carries the
step's `reasoning` and `function_call` items unchanged plus the outputs by `call_id`
(`next_step_input`); commentary is neither shown nor carried. A step without calls is the
answer. A cut step (`incomplete`) is never parsed: it is retried once with the doubled
output limit, not above `max_output_tokens_cap` (Т-§11.1); cut twice — an error.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from quack.llm.errors import LLMError, ProviderIncompleteError
from quack.llm.streaming import (
    OutputItemDone,
    StreamCompleted,
    StreamingLLMProvider,
    StreamRequest,
    StreamUsage,
    TextDelta,
    next_step_input,
    tool_calls,
)

type ExecuteTool = Callable[[str, str], Awaitable[str]]
type OnText = Callable[[str], Awaitable[None]]
type OnTool = Callable[[str], Awaitable[None]]
type OnRestart = Callable[[], Awaitable[None]]


class ToolLoopLimitError(LLMError):
    """The model kept calling tools for `max_steps` steps (D-B-117)."""


@dataclass(frozen=True)
class ToolLoopResult:
    """The answer text, the items of the answer step, usage summed over all steps, number of
    model steps, whether a cut answer was retried, the model and the last headers."""

    text: str
    output_items: tuple[Mapping[str, Any], ...]
    usage: StreamUsage
    steps: int
    retried_incomplete: bool
    model: str
    headers: Mapping[str, str]


@dataclass(frozen=True)
class _Step:
    text: str
    items: tuple[Mapping[str, Any], ...]
    completed: StreamCompleted


async def run_tool_loop(
    provider: StreamingLLMProvider,
    request: StreamRequest,
    execute: ExecuteTool,
    *,
    tool_timeout_ms: int,
    max_steps: int,
    max_output_tokens_cap: int,
    on_text: OnText | None = None,
    on_tool: OnTool | None = None,
    on_restart: OnRestart | None = None,
) -> ToolLoopResult:
    """Run the loop until a step without tool calls. `on_text` gets answer text as it
    streams; `on_tool` — each tool name before it runs (SSE `status: tool`, Т-§12.2);
    `on_restart` — before a cut step is retried, so that the caller drops what it got."""
    usage = StreamUsage()
    retried = False
    for step_no in range(1, max_steps + 1):
        step = await _stream_step(provider, request, on_text)
        usage = _add(usage, step.completed.usage)
        if step.completed.status == "incomplete":
            if retried:
                raise ProviderIncompleteError(step.text, headers=step.completed.headers)
            retried = True
            if on_restart is not None:
                await on_restart()
            request = replace(
                request,
                base=replace(
                    request.base,
                    max_output_tokens=_doubled(
                        request.base.max_output_tokens, max_output_tokens_cap
                    ),
                ),
            )
            step = await _stream_step(provider, request, on_text)
            usage = _add(usage, step.completed.usage)
            if step.completed.status == "incomplete":
                raise ProviderIncompleteError(step.text, headers=step.completed.headers)
        calls = tool_calls(step.items)
        if not calls:
            return ToolLoopResult(
                text=step.text,
                output_items=step.items,
                usage=usage,
                steps=step_no,
                retried_incomplete=retried,
                model=step.completed.model,
                headers=step.completed.headers,
            )
        if on_tool is not None:
            for call in calls:
                await on_tool(call.name)
        results = await asyncio.gather(
            *(_run_tool(execute, call.name, call.arguments, tool_timeout_ms) for call in calls)
        )
        outputs = {call.call_id: result for call, result in zip(calls, results, strict=True)}
        request = replace(
            request, input_items=next_step_input(request.input_items, step.items, outputs)
        )
    raise ToolLoopLimitError(f"no answer after {max_steps} model steps")


async def _stream_step(
    provider: StreamingLLMProvider, request: StreamRequest, on_text: OnText | None
) -> _Step:
    text: list[str] = []
    items: list[Mapping[str, Any]] = []
    completed: StreamCompleted | None = None
    async for event in provider.stream_structured(request):
        if isinstance(event, TextDelta):
            text.append(event.text)
            if on_text is not None:
                await on_text(event.text)
        elif isinstance(event, OutputItemDone):
            items.append(event.item)
        else:
            completed = event
    if completed is None:
        raise ProviderIncompleteError("".join(text))  # the stream ended without its end event
    return _Step("".join(text), tuple(items), completed)


async def _run_tool(execute: ExecuteTool, name: str, arguments: str, timeout_ms: int) -> str:
    """Т-§11.5: a failing or late tool returns an error text, the turn goes on."""
    try:
        return await asyncio.wait_for(execute(name, arguments), timeout_ms / 1000)
    except TimeoutError:
        return f"error: tool {name} timed out after {timeout_ms} ms"
    except Exception as exc:
        return f"error: tool {name} failed: {type(exc).__name__}: {exc}"


def _doubled(limit: int | None, cap: int) -> int:
    return cap if limit is None else min(limit * 2, cap)


def _add(a: StreamUsage, b: StreamUsage) -> StreamUsage:
    return StreamUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        cached_tokens=a.cached_tokens + b.cached_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        reasoning_tokens=a.reasoning_tokens + b.reasoning_tokens,
    )
