"""Streaming structured output and the tool loop of the gateway (Т-§11.1, Т-§11.5, Т-§12.2;
ТЗ Ф4 WP10.2–10.3, seam S13).

`StreamingLLMProvider.stream_structured` calls `/responses` with `stream: true`, the strict
`text.format` and, if given, `tools` (`tool_choice: auto`, the only one Meta accepts,
Т-§1.3). It yields `StreamEvent`s; the last one is always `StreamCompleted`. Items of the
step (`reasoning` with `encrypted_content`, `function_call`, `message`) are returned raw:
the tool loop appends `reasoning` and `function_call` unchanged to the next step's input
together with `function_call_output` by `call_id`, without `previous_response_id`,
`store: false` (Т-§11.5). A `message` with `phase: commentary` is not the answer: it is not
shown, not parsed as JSON and not returned to the input (Т-§11.5).

`TurnStreamParser` parses the turn JSON as it comes (standard library): it reports the
close of `p1_head`, the growing `text` of each `p2_body.blocks` item and the close of
`p3_markup` (Т-§12.2). Body before head — the caller buffers fully (Т-§12.2).

`ScriptedLLMProvider` / `meta-mock` implement the provider side of this interface in
`tests/support/` (C, seam S13).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from quack.llm.provider import ProviderRequest


@dataclass(frozen=True)
class StreamRequest:
    """One step of a streamed call. `input_items` — items carried from the previous steps of
    the tool loop (`reasoning`, `function_call`, `function_call_output`), in order;
    `tools` — Responses API function definitions (`quack.session.tools.tool_definition`)."""

    base: ProviderRequest
    input_items: tuple[Mapping[str, Any], ...] = ()
    tools: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class TextDelta:
    """A piece of the answer text (`response.output_text.delta`) of a non-commentary message."""

    text: str


@dataclass(frozen=True)
class OutputItemDone:
    """A finished output item, raw (`response.output_item.done`): `reasoning`,
    `function_call`, `message` (with `phase` if any)."""

    item: Mapping[str, Any]


@dataclass(frozen=True)
class StreamUsage:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class StreamCompleted:
    """End of the stream: `status` `completed` | `incomplete`; usage by kind; the
    rate-limit headers of the response (Т-§11.3)."""

    status: Literal["completed", "incomplete"]
    usage: StreamUsage
    model: str
    headers: Mapping[str, str] = field(default_factory=dict)


type StreamEvent = TextDelta | OutputItemDone | StreamCompleted


class StreamingLLMProvider(Protocol):
    """Provider side of seam S13. Errors as `LLMProvider.complete_structured`: 429, 400,
    unavailable; an incomplete answer is `StreamCompleted(status="incomplete")` and is never
    parsed (Т-§11.1)."""

    def stream_structured(self, request: StreamRequest) -> AsyncIterator[StreamEvent]: ...


# --- Incremental parse of the turn output (Т-§12.2) ---


@dataclass(frozen=True)
class HeadClosed:
    """`p1_head` closed; `head` — its JSON text."""

    head: str


@dataclass(frozen=True)
class BlockText:
    """New text of `p2_body.blocks[index].text` (only the part not reported before)."""

    index: int
    text: str


@dataclass(frozen=True)
class BlockClosed:
    """`p2_body.blocks[index]` closed; `block` — its JSON text (for `block_id`, `element_id`)."""

    index: int
    block: str


@dataclass(frozen=True)
class MarkupClosed:
    """`p3_markup` closed; `markup` — its JSON text."""

    markup: str


type ParseEvent = HeadClosed | BlockText | BlockClosed | MarkupClosed


class TurnStreamParser:
    """Feed text deltas, get parse events in the order of the text. `body_before_head` is
    true when a body text came before the head closed (Т-§12.2: full buffering)."""

    body_before_head: bool

    def feed(self, delta: str) -> list[ParseEvent]:
        raise NotImplementedError

    def close(self) -> str:
        """The whole JSON text received; the caller validates it by the output model."""
        raise NotImplementedError


# --- Tool loop (Т-§11.5) ---


@dataclass(frozen=True)
class ToolCall:
    """A `function_call` of a step: `call_id` (`call_…`), not the item id (`fc_…`)."""

    call_id: str
    name: str
    arguments: str


def next_step_input(
    previous: tuple[Mapping[str, Any], ...],
    step_items: tuple[Mapping[str, Any], ...],
    outputs: Mapping[str, str],
) -> tuple[Mapping[str, Any], ...]:
    """Input items of the next step (Т-§11.5): `previous`, then the step's `reasoning` and
    `function_call` items unchanged, then a `function_call_output` per `call_id` in the order
    of the calls. Commentary messages are dropped."""
    raise NotImplementedError
