"""LLM provider test doubles (ТЗ Фазы 3 §8.1).

- `FakeLLMProvider`: queue of responses in call order (moved from `tests/domains/conftest.py`).
- `ScriptedLLMProvider`: response chosen by `schema_name`, not by order, because background
  jobs in e2e tests run concurrently; failure kinds follow the provider interface of WP5.7
  (seam S12). Phase 4 (ТЗ Ф4 §8.1, seam S13): `stream_structured` — the stream of
  `quack.llm.streaming` with tool steps; the format of items and failures is D-B-125.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import orjson
from pydantic import BaseModel

from quack.llm.errors import (
    ProviderBadRequestError,
    ProviderIncompleteError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from quack.llm.media import (
    IllustrationRequest,
    ImageResponse,
    SegmentRequest,
    TranscribeRequest,
    TranscribeResponse,
)
from quack.llm.provider import ProviderRequest, ProviderResponse
from quack.llm.streaming import (
    OutputItemDone,
    StreamCompleted,
    StreamEvent,
    StreamRequest,
    StreamUsage,
    TextDelta,
)

type QueuedLLMItem = dict[str, Any] | BaseModel | str | Exception


class FakeLLMProvider:
    """In-memory `LLMProvider` test double recording `ProviderRequest` calls and returning
    queued structured responses (`dict`, `BaseModel`, JSON `str`, or raising `Exception`).
    """

    def __init__(self, responses: Sequence[QueuedLLMItem] | None = None) -> None:
        self.responses: list[QueuedLLMItem] = list(responses or [])
        self.calls: list[ProviderRequest] = []

    def enqueue(self, *items: QueuedLLMItem) -> None:
        self.responses.extend(items)

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError(
                f"FakeLLMProvider queue is empty for schema {request.schema_name!r}"
            )
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, BaseModel):
            content = item.model_dump_json()
        elif isinstance(item, dict):
            content = orjson.dumps(item).decode()
        else:
            content = item
        return ProviderResponse(
            content=content,
            input_tokens=100,
            output_tokens=40,
            cached_tokens=0,
            model=request.model,
        )


class UnavailableLLMProvider:
    """`LLMProvider` that is always down (`ProviderUnavailableError`): the default of
    `build_app_resources`, like a deployment without a reachable model API."""

    def __init__(self) -> None:
        self.calls: list[ProviderRequest] = []

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        raise ProviderUnavailableError("test provider is unavailable")


type Responder = Callable[[ProviderRequest], QueuedLLMItem]


@dataclass
class ScriptedFailure:
    """A failure injected for calls matching `model` and/or `schema_name` (`None` = any).

    `kind` values (§8.1): `rate_limited` (with `retry_after`: seconds, HTTP date or `None`,
    sent as the raw `retry-after` header), `unavailable`, `bad_request`, `incomplete`,
    `invalid_json`, `commentary_only` (only a `phase: commentary` message, no answer),
    `ratelimit_headers` (a normal answer whose `x-ratelimit-remaining-*` headers are
    `remaining_share` of the limits). `times` = how many matching calls are affected.
    """

    kind: str
    model: str | None = None
    schema_name: str | None = None
    times: int = 1
    retry_after: str | None = None
    remaining_share: float | None = None


# Failure kinds of §8.1 over the provider interface of WP5.7 (seam S12).
SUPPORTED_FAILURES = frozenset(
    {
        "unavailable",
        "rate_limited",
        "bad_request",
        "incomplete",
        "invalid_json",
        "commentary_only",
        "ratelimit_headers",
    }
)
# Limits reported by `ratelimit_headers`; the remaining part is `remaining_share` of them.
SCRIPTED_REQUEST_LIMIT = 1000
SCRIPTED_TOKEN_LIMIT = 1_000_000


def _default_responses() -> dict[str, QueuedLLMItem]:
    """Valid, data-agnostic answers for the nine output schemas of Phase 2 (§8.1)."""
    return {
        "StructurerOutput": {
            "topics": [
                {"slug": "basics", "name": "Basics", "description": "Starting point"},
                {"slug": "next", "name": "Next steps", "description": "Builds on basics"},
            ],
            "skills": [
                {"slug": "basics-1", "topic_slug": "basics", "name": "Core idea"},
                {"slug": "next-1", "topic_slug": "next", "name": "Applying the idea"},
            ],
            "prereqs": [{"from_topic_slug": "basics", "to_topic_slug": "next"}],
            "skill_deps": [],
            "misconceptions": [],
        },
        "DiagnosticOutput": {
            "prompt_text": "Which option is right?",
            "options": ["A", "B", "C"],
            "correct_answer": "A",
            "is_correct": True,
            "score": 1.0,
            "tested_depth": 0.5,
        },
        "CuratorOutput": {
            "promotions": [],
            "skill_merges": [],
            "topic_merges": [],
            "misconception_merges": [],
        },
        "TeacherOutput": {
            "reply_text": "Let us practise this step by step.",
            "pedagogical_move": "assign_task",
        },
        "ExtractorOutput": {"observations": [], "misconceptions": []},
        "ExtractorOutputV2": {"entries": []},
        "TaskGenOutput": {
            "statement": "Pick the right option.",
            "options": [
                {"label": "A", "text": "Right", "correct": True, "catches": None},
                {"label": "B", "text": "Wrong", "correct": False, "catches": "slip"},
            ],
            "answer": {"type": "choice", "value": "A", "units": None},
            "solution": "A is right.",
            "hints": ["Read the options.", "Compare them.", "A is close."],
            "tests": [{"skill": "S1", "level": 1}],
            "method_named": True,
            "variant_of": None,
            "form": None,
            "pair_task": None,
        },
        "EvaluatorOutput": {
            "items": [],
            "verdict": "correct",
            "confidence": 1.0,
            "misconception": None,
            "note": "Correct.",
        },
        "ArtifactGenOutput": {"title": "Notes", "text": "# Notes\n\nScripted text."},
    }


def _retry_after_seconds(raw: str | None) -> float | None:
    """Seconds of a numeric `retry-after`; an HTTP date or no header gives `None`."""
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _failed_content(failure: ScriptedFailure) -> str:
    """Raise the provider error of `failure`, or return the broken answer text."""
    if failure.kind == "unavailable":
        raise ProviderUnavailableError("scripted: unavailable")
    if failure.kind == "rate_limited":
        headers = {} if failure.retry_after is None else {"retry-after": failure.retry_after}
        raise ProviderRateLimitedError(_retry_after_seconds(failure.retry_after), headers=headers)
    if failure.kind == "bad_request":
        raise ProviderBadRequestError('{"error": {"message": "scripted bad request"}}')
    if failure.kind == "incomplete":
        raise ProviderIncompleteError('{"reply_text": "cut')
    if failure.kind == "commentary_only":
        return ""
    return "{not json"


def _ratelimit_headers(remaining_share: float) -> dict[str, str]:
    return {
        "x-ratelimit-limit-requests": str(SCRIPTED_REQUEST_LIMIT),
        "x-ratelimit-remaining-requests": str(int(SCRIPTED_REQUEST_LIMIT * remaining_share)),
        "x-ratelimit-limit-tokens": str(SCRIPTED_TOKEN_LIMIT),
        "x-ratelimit-remaining-tokens": str(int(SCRIPTED_TOKEN_LIMIT * remaining_share)),
    }


def _content(item: QueuedLLMItem) -> str:
    if isinstance(item, Exception):
        raise item
    if isinstance(item, BaseModel):
        return item.model_dump_json()
    if isinstance(item, dict):
        return orjson.dumps(item).decode()
    return item


# Stream of the stub (D-C-108): the answer text goes out in deltas of this many characters.
STREAM_DELTA_CHARS = 16
SCRIPTED_INPUT_TOKENS = 100
SCRIPTED_OUTPUT_TOKENS = 40
COMMENTARY_TEXT = "Thinking it over."
PROVIDER_ERRORS = frozenset({"unavailable", "rate_limited", "bad_request"})
# Failures a tool step leaves for the answer step (D-B-125 п.5).
TOOL_STEP_SKIPS = frozenset({"invalid_json"})


@dataclass(frozen=True)
class ScriptedToolCall:
    """One `function_call` of a scripted step: tool name and its arguments."""

    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class ScriptedToolStep:
    """A step of the tool loop (Т-§11.5): an optional `phase: commentary` message, then the
    calls in order (two in one step are allowed, §8.1). A step with calls has no answer."""

    calls: tuple[ScriptedToolCall, ...]
    commentary: str | None = None


def _sorted_json(data: Any) -> str:
    return orjson.dumps(data, option=orjson.OPT_SORT_KEYS).decode()


def _stream_text(item: QueuedLLMItem, *, body_before_head: bool) -> str:
    """The answer text of a stream: keys in alphabetical order at every level (Т-§11.8); with
    `body_before_head` the key `p2_body` goes before `p1_head` (§8.1, Т-§12.2)."""
    if isinstance(item, Exception):
        raise item
    if isinstance(item, str):
        return item
    data: Any = item.model_dump(mode="json") if isinstance(item, BaseModel) else item
    if not (body_before_head and isinstance(data, dict) and "p2_body" in data):
        return _sorted_json(data)
    fields: dict[str, Any] = data
    rest = {key: value for key, value in fields.items() if key != "p2_body"}
    parts = [_sorted_json({"p2_body": fields["p2_body"]})[1:-1], _sorted_json(rest)[1:-1]]
    return "{" + ",".join(part for part in parts if part) + "}"


def _reasoning_item(schema_name: str, step: int) -> dict[str, Any]:
    return {
        "type": "reasoning",
        "id": f"rs_{schema_name}_{step}",
        "encrypted_content": f"scripted-{schema_name}-{step}",
        "summary": [],
    }


def _message_item(item_id: str, text: str, *, commentary: bool = False) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": "message",
        "id": item_id,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }
    if commentary:
        message["phase"] = "commentary"
    return message


def _call_id(schema_name: str, step: int, index: int) -> str:
    return f"call_{schema_name}_{step}_{index}"


def _answered_steps(
    schema_name: str,
    steps: tuple[ScriptedToolStep, ...],
    input_items: Sequence[Mapping[str, Any]],
) -> int:
    """How many scripted steps are done: every `call_id` of the step has a
    `function_call_output` in the input (D-B-125 п.2). A call carried without its output
    fails the test loudly."""
    called = {str(i.get("call_id")) for i in input_items if i.get("type") == "function_call"}
    answered = {
        str(i.get("call_id")) for i in input_items if i.get("type") == "function_call_output"
    }
    if missing := called - answered:
        raise AssertionError(f"ScriptedLLMProvider: no function_call_output for {sorted(missing)}")
    done = 0
    for step, scripted in enumerate(steps):
        ids = {_call_id(schema_name, step, index) for index in range(len(scripted.calls))}
        if not ids <= answered:
            break
        done += 1
    return done


@dataclass
class Hold:
    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class ScriptedLLMProvider:
    """`LLMProvider` answering by `request.schema_name` with default responders for all
    output schemas, a call journal and failure injection by model or schema (§8.1).

    Background jobs in e2e tests run concurrently, so the answer never depends on the call
    order. A schema without a responder fails the test loudly."""

    responders: dict[str, Responder] = field(default_factory=dict)
    failures: list[ScriptedFailure] = field(default_factory=list)
    calls: list[ProviderRequest] = field(default_factory=list)
    gates: dict[str, Hold] = field(default_factory=dict)
    tool_steps: dict[str, tuple[ScriptedToolStep, ...]] = field(default_factory=dict)
    stream_calls: list[StreamRequest] = field(default_factory=list)
    body_before_head: bool = False
    # Non-text roles (D-B-187; the gateway wants `segment`, `transcribe`, `illustrate`): the
    # journals, the special-token text `sam-3.1` answers with and the transcript of the audio,
    # each a constant or a function of the request; `media_error` is raised by every call.
    segment_calls: list[SegmentRequest] = field(default_factory=list)
    transcribe_calls: list[TranscribeRequest] = field(default_factory=list)
    segment_text: str | Callable[[SegmentRequest], str] = ""
    transcript: str | Callable[[TranscribeRequest], str] = "scripted transcript"
    media_error: Exception | None = None

    def __post_init__(self) -> None:
        for schema_name, item in _default_responses().items():
            self.responders.setdefault(schema_name, lambda _request, item=item: item)

    def respond(self, schema_name: str, responder: Responder) -> None:
        """Override the default responder of one schema."""
        self.responders[schema_name] = responder

    def script_tools(self, schema_name: str, *steps: ScriptedToolStep) -> None:
        """Streamed calls of `schema_name` go through `steps` before the answer (§8.1)."""
        self.tool_steps[schema_name] = steps

    def hold(self, schema_name: str) -> Hold:
        """Calls of `schema_name` wait until `release` is set; `arrived` is set when the first
        held call reaches the gate (race tests)."""
        gate = self.gates[schema_name] = Hold()
        return gate

    def fail(self, failure: ScriptedFailure) -> None:
        """Inject a failure for matching calls."""
        if failure.kind not in SUPPORTED_FAILURES:
            raise ValueError(f"unknown failure kind {failure.kind!r}")
        self.failures.append(failure)

    def calls_for(
        self, *, model: str | None = None, schema_name: str | None = None
    ) -> list[ProviderRequest]:
        """Journal of calls filtered by model and/or schema."""
        return [
            c
            for c in self.calls
            if (model is None or c.model == model)
            and (schema_name is None or c.schema_name == schema_name)
        ]

    def _take_failure(
        self, request: ProviderRequest, skip: frozenset[str] = frozenset()
    ) -> ScriptedFailure | None:
        """The first matching failure; kinds in `skip` are left for a later call."""
        for failure in self.failures:
            if failure.kind in skip:
                continue
            if failure.times <= 0:
                continue
            if failure.model not in (None, request.model):
                continue
            if failure.schema_name not in (None, request.schema_name):
                continue
            failure.times -= 1
            return failure
        return None

    async def segment(self, request: SegmentRequest) -> ProviderResponse:
        self.segment_calls.append(request)
        if self.media_error is not None:
            raise self.media_error
        text = self.segment_text(request) if callable(self.segment_text) else self.segment_text
        return ProviderResponse(text, 100, 10, 0, request.model)

    async def transcribe(self, request: TranscribeRequest) -> TranscribeResponse:
        self.transcribe_calls.append(request)
        if self.media_error is not None:
            raise self.media_error
        text = self.transcript(request) if callable(self.transcript) else self.transcript
        return TranscribeResponse(text, 0, 0, 0, request.model, audio_duration_ms=1000)

    async def illustrate(self, request: IllustrationRequest) -> ImageResponse:
        raise AssertionError("ScriptedLLMProvider: `illustrator` is not scripted")

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        gate = self.gates.get(request.schema_name)
        if gate is not None:
            gate.arrived.set()
            await gate.release.wait()
        failure = self._take_failure(request)
        headers: dict[str, str] = {}
        output_items: tuple[dict[str, Any], ...] = ()
        if failure is not None and failure.kind != "ratelimit_headers":
            content = _failed_content(failure)
            if failure.kind == "commentary_only":
                output_items = (
                    {
                        "type": "message",
                        "phase": "commentary",
                        "content": [{"type": "output_text", "text": "Thinking it over."}],
                    },
                )
        else:
            if failure is not None:
                headers = _ratelimit_headers(failure.remaining_share or 0.0)
            responder = self.responders.get(request.schema_name)
            if responder is None:
                raise AssertionError(
                    f"ScriptedLLMProvider: no responder for {request.schema_name!r}"
                )
            content = _content(responder(request))
        return ProviderResponse(
            content=content,
            input_tokens=100,
            output_tokens=40,
            cached_tokens=0,
            model=request.model,
            output_items=output_items,
            headers=headers,
        )

    async def stream_structured(self, request: StreamRequest) -> AsyncIterator[StreamEvent]:
        """Seam S13 (D-B-125): the next scripted tool step of `request.base.schema_name`, or
        the answer of its responder in text deltas. `StreamCompleted` is always last; provider
        errors are raised before the first event. The whole request goes to `stream_calls`,
        its `base` to `calls`."""
        base = request.base
        self.stream_calls.append(request)
        self.calls.append(base)
        gate = self.gates.get(base.schema_name)
        if gate is not None:
            gate.arrived.set()
            await gate.release.wait()
        steps = self.tool_steps.get(base.schema_name, ())
        step = _answered_steps(base.schema_name, steps, request.input_items)
        tool_step = step < len(steps)
        # D-B-125 п.5: `invalid_json` breaks the answer text, so on a tool step it waits for
        # the answer step.
        failure = self._take_failure(base, TOOL_STEP_SKIPS if tool_step else frozenset())
        kind = None if failure is None else failure.kind
        if failure is not None and kind in PROVIDER_ERRORS:
            _failed_content(failure)
        headers: dict[str, str] = {}
        if failure is not None and kind == "ratelimit_headers":
            headers = _ratelimit_headers(failure.remaining_share or 0.0)
        status: Literal["completed", "incomplete"] = "completed"
        # D-B-125 п.5: `commentary_only` gives only the commentary, `incomplete` only text
        # (on a tool step — nothing: no `function_call`, only `StreamCompleted`).
        if kind not in ("commentary_only", "incomplete"):
            yield OutputItemDone(_reasoning_item(base.schema_name, step))
        if kind == "commentary_only":
            item_id = f"msg_{base.schema_name}_{step}_c"
            yield OutputItemDone(_message_item(item_id, COMMENTARY_TEXT, commentary=True))
        elif tool_step and kind == "incomplete":
            status = "incomplete"
        elif tool_step:
            for event in _tool_step(base.schema_name, step, steps[step]):
                yield event
        else:
            text = "{not json" if kind == "invalid_json" else self._answer_text(base)
            if kind == "incomplete":
                text, status = text[: max(1, len(text) // 2)], "incomplete"
            for start in range(0, len(text), STREAM_DELTA_CHARS):
                yield TextDelta(text[start : start + STREAM_DELTA_CHARS])
            if status == "completed":
                yield OutputItemDone(_message_item(f"msg_{base.schema_name}_{step}", text))
        yield StreamCompleted(
            status=status,
            usage=StreamUsage(
                input_tokens=SCRIPTED_INPUT_TOKENS, output_tokens=SCRIPTED_OUTPUT_TOKENS
            ),
            model=base.model,
            headers=headers,
        )

    def _answer_text(self, request: ProviderRequest) -> str:
        responder = self.responders.get(request.schema_name)
        if responder is None:
            raise AssertionError(f"ScriptedLLMProvider: no responder for {request.schema_name!r}")
        return _stream_text(responder(request), body_before_head=self.body_before_head)


def _tool_step(schema_name: str, step: int, scripted: ScriptedToolStep) -> list[StreamEvent]:
    """Items of a scripted tool step: the commentary, then each `function_call` (D-B-125 п.2)."""
    events: list[StreamEvent] = []
    if scripted.commentary is not None:
        item_id = f"msg_{schema_name}_{step}_c"
        events.append(OutputItemDone(_message_item(item_id, scripted.commentary, commentary=True)))
    for index, call in enumerate(scripted.calls):
        item = {
            "type": "function_call",
            "id": f"fc_{schema_name}_{step}_{index}",
            "call_id": _call_id(schema_name, step, index),
            "name": call.name,
            "arguments": _sorted_json(dict(call.arguments)),
            "status": "completed",
        }
        events.append(OutputItemDone(item))
    return events
