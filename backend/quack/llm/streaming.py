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


type _PathItem = str | int

_HEAD: tuple[_PathItem, ...] = ("p1_head",)
_BODY: tuple[_PathItem, ...] = ("p2_body",)
_MARKUP: tuple[_PathItem, ...] = ("p3_markup",)
_BLOCKS: tuple[_PathItem, ...] = ("p2_body", "blocks")
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_WHITESPACE = frozenset(" \t\r\n")


@dataclass
class _Frame:
    """An open object or array: its path, where it started in the text, what comes next
    (`key`, `colon`, `value`, `comma`) and the current key or index."""

    is_object: bool
    path: tuple[_PathItem, ...]
    start: int
    expect: str = "key"
    key: str | None = None
    index: int = 0

    def child(self) -> _PathItem:
        return (self.key or "") if self.is_object else self.index


class TurnStreamParser:
    """Feed text deltas, get parse events in the order of the text. `body_before_head` is
    true when the body started before the head closed (Т-§12.2: full buffering).

    A small JSON scanner (standard library, WP10.2): it tracks the path of open objects and
    arrays, reports the close of `p1_head` and `p3_markup` and of each `p2_body.blocks[i]`,
    and decodes the string `p2_body.blocks[i].text` as it comes. It does not validate: the
    caller validates `close()` by the output model.
    """

    def __init__(self) -> None:
        self.body_before_head = False
        self._text: list[str] = []
        self._pos = 0
        self._stack: list[_Frame] = []
        self._head_closed = False
        # String state: inside, is it a key, its chars (keys only), escape state.
        self._in_string = False
        self._string_is_key = False
        self._key_chars: list[str] = []
        self._escape = False
        self._unicode: str | None = None
        self._high_surrogate: int | None = None
        self._stream_block: int | None = None

    def feed(self, delta: str) -> list[ParseEvent]:
        events: list[ParseEvent] = []
        for char in delta:
            self._text.append(char)
            self._step(char, events)
            self._pos += 1
        return self._flush(events)

    def close(self) -> str:
        """The whole JSON text received; the caller validates it by the output model."""
        return "".join(self._text)

    # --- scanner ---

    def _flush(self, events: list[ParseEvent]) -> list[ParseEvent]:
        """Pending block text goes out before the next structural event of the same feed."""
        out: list[ParseEvent] = []
        for event in events:
            last = out[-1] if out else None
            if (
                isinstance(event, BlockText)
                and isinstance(last, BlockText)
                and last.index == event.index
            ):
                out[-1] = BlockText(event.index, last.text + event.text)
                continue
            out.append(event)
        return out

    def _step(self, char: str, events: list[ParseEvent]) -> None:
        if self._in_string:
            self._string_char(char, events)
            return
        if char in _WHITESPACE:
            return
        frame = self._stack[-1] if self._stack else None
        if char == '"':
            self._start_string(frame)
        elif char in "{[":
            self._open(char == "{", frame)
        elif char in "}]":
            self._close(events)
        elif char == ":":
            if frame is not None:
                frame.expect = "value"
        elif char == ",":
            if frame is not None:
                if frame.is_object:
                    frame.expect = "key"
                else:
                    frame.index += 1
                    frame.expect = "value"
        elif frame is not None and frame.expect == "value":
            frame.expect = "comma"  # a number, true, false or null: its chars are skipped

    def _open(self, is_object: bool, parent: _Frame | None) -> None:
        path = () if parent is None else (*parent.path, parent.child())
        if parent is not None:
            parent.expect = "comma"
        if path == _BODY and not self._head_closed:
            self.body_before_head = True
        self._stack.append(_Frame(is_object, path, self._pos, "key" if is_object else "value"))

    def _close(self, events: list[ParseEvent]) -> None:
        frame = self._stack.pop()
        text = "".join(self._text[frame.start : self._pos + 1])
        if frame.path == _HEAD:
            self._head_closed = True
            events.append(HeadClosed(text))
        elif frame.path == _MARKUP:
            events.append(MarkupClosed(text))
        elif frame.is_object and len(frame.path) == 3 and frame.path[:2] == _BLOCKS:
            index = frame.path[2]
            assert isinstance(index, int)
            events.append(BlockClosed(index, text))

    def _start_string(self, frame: _Frame | None) -> None:
        self._in_string = True
        self._string_is_key = frame is not None and frame.is_object and frame.expect == "key"
        self._key_chars = []
        self._stream_block = None
        if frame is None or self._string_is_key:
            return
        path = (*frame.path, frame.child())
        if len(path) == 4 and path[:2] == _BLOCKS and path[3] == "text":
            index = path[2]
            assert isinstance(index, int)
            self._stream_block = index

    def _string_char(self, char: str, events: list[ParseEvent]) -> None:
        if self._unicode is not None:
            self._unicode += char
            if len(self._unicode) == 4:
                code, self._unicode = int(self._unicode, 16), None
                self._decoded_code(code, events)
            return
        if self._escape:
            self._escape = False
            if char == "u":
                self._unicode = ""
            else:
                self._decoded(_ESCAPES.get(char, char), events)
            return
        if char == "\\":
            self._escape = True
        elif char == '"':
            self._end_string()
        else:
            self._decoded(char, events)

    def _decoded_code(self, code: int, events: list[ParseEvent]) -> None:
        if 0xD800 <= code < 0xDC00:
            self._high_surrogate = code
            return
        if 0xDC00 <= code < 0xE000 and self._high_surrogate is not None:
            code = 0x10000 + ((self._high_surrogate - 0xD800) << 10) + (code - 0xDC00)
        self._high_surrogate = None
        self._decoded(chr(code), events)

    def _decoded(self, char: str, events: list[ParseEvent]) -> None:
        if self._string_is_key:
            self._key_chars.append(char)
        elif self._stream_block is not None:
            events.append(BlockText(self._stream_block, char))

    def _end_string(self) -> None:
        self._in_string = False
        frame = self._stack[-1] if self._stack else None
        if frame is None:
            return
        if self._string_is_key:
            frame.key = "".join(self._key_chars)
            frame.expect = "colon"
        else:
            frame.expect = "comma"
        self._stream_block = None


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
    carried = [item for item in step_items if item.get("type") in ("reasoning", "function_call")]
    results = [
        {
            "type": "function_call_output",
            "call_id": item["call_id"],
            "output": outputs[item["call_id"]],
        }
        for item in carried
        if item.get("type") == "function_call"
    ]
    return (*previous, *carried, *results)


def tool_calls(step_items: tuple[Mapping[str, Any], ...]) -> list[ToolCall]:
    """The `function_call` items of a step, in order."""
    return [
        ToolCall(str(item["call_id"]), str(item["name"]), str(item.get("arguments", "")))
        for item in step_items
        if item.get("type") == "function_call"
    ]
