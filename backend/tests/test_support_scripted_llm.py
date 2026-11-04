"""`ScriptedLLMProvider` of `tests/support` (ТЗ Фазы 3 §8.1, seam S9 over S12; ТЗ Ф4 §8.1,
seam S13: streaming and tools by D-B-125)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import orjson
import pytest
from pydantic import BaseModel

from quack.artifacts.schemas import ArtifactGenOutput
from quack.domains.schemas import CuratorOutput, DiagnosticOutput, StructurerOutput
from quack.llm.errors import (
    ProviderBadRequestError,
    ProviderIncompleteError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from quack.llm.provider import ProviderRequest
from quack.llm.streaming import (
    OutputItemDone,
    StreamCompleted,
    StreamEvent,
    StreamRequest,
    TextDelta,
    TurnStreamParser,
    next_step_input,
    tool_calls,
)
from quack.practice.schemas import EvaluatorOutput, TaskGenOutput
from quack.session.schemas import ExtractorOutput, ExtractorOutputV2, TeacherOutput
from tests.support.llm import (
    ScriptedFailure,
    ScriptedLLMProvider,
    ScriptedToolCall,
    ScriptedToolStep,
)

OUTPUT_SCHEMAS: tuple[type[BaseModel], ...] = (
    StructurerOutput,
    DiagnosticOutput,
    CuratorOutput,
    TeacherOutput,
    ExtractorOutput,
    ExtractorOutputV2,
    TaskGenOutput,
    EvaluatorOutput,
    ArtifactGenOutput,
)


def _request(schema_name: str, model: str = "muse-spark-1.3") -> ProviderRequest:
    return ProviderRequest(model, "prompt", {}, schema_name, 0.0, 1.0)


async def test_default_responders_are_valid_for_every_output_schema() -> None:
    provider = ScriptedLLMProvider()
    for model in OUTPUT_SCHEMAS:
        response = await provider.complete_structured(_request(model.__name__))
        model.model_validate_json(response.content)
    assert len(provider.calls_for(schema_name="TeacherOutput")) == 1


async def test_answer_is_chosen_by_schema_not_by_order() -> None:
    provider = ScriptedLLMProvider()
    provider.respond("ArtifactGenOutput", lambda _r: {"title": "T", "text": "x"})
    other = await provider.complete_structured(_request("ExtractorOutputV2"))
    mine = await provider.complete_structured(_request("ArtifactGenOutput"))
    assert ArtifactGenOutput.model_validate_json(mine.content).title == "T"
    ExtractorOutputV2.model_validate_json(other.content)


@pytest.mark.parametrize(
    ("kind", "error"),
    [
        ("unavailable", ProviderUnavailableError),
        ("bad_request", ProviderBadRequestError),
        ("incomplete", ProviderIncompleteError),
    ],
)
async def test_failures_raise_provider_errors(kind: str, error: type[Exception]) -> None:
    provider = ScriptedLLMProvider()
    provider.fail(ScriptedFailure(kind, schema_name="TeacherOutput"))
    with pytest.raises(error):
        await provider.complete_structured(_request("TeacherOutput"))
    # `times=1`: the next call answers normally; other schemas were never affected.
    TeacherOutput.model_validate_json(
        (await provider.complete_structured(_request("TeacherOutput"))).content
    )


@pytest.mark.parametrize(
    ("retry_after", "seconds"), [("2", 2.0), ("Wed, 21 Oct 2026 07:28:00 GMT", None), (None, None)]
)
async def test_rate_limited_carries_raw_retry_after(
    retry_after: str | None, seconds: float | None
) -> None:
    provider = ScriptedLLMProvider()
    provider.fail(ScriptedFailure("rate_limited", model="muse-wonder-1.0", retry_after=retry_after))
    TeacherOutput.model_validate_json(
        (await provider.complete_structured(_request("TeacherOutput"))).content
    )
    with pytest.raises(ProviderRateLimitedError) as caught:
        await provider.complete_structured(_request("TeacherOutput", "muse-wonder-1.0"))
    assert caught.value.retry_after_sec == seconds
    assert caught.value.headers.get("retry-after") == retry_after


async def test_broken_answers_and_ratelimit_headers() -> None:
    provider = ScriptedLLMProvider()
    provider.fail(ScriptedFailure("invalid_json", schema_name="ExtractorOutput"))
    provider.fail(ScriptedFailure("commentary_only", schema_name="TeacherOutput"))
    provider.fail(
        ScriptedFailure("ratelimit_headers", schema_name="EvaluatorOutput", remaining_share=0.1)
    )
    assert (await provider.complete_structured(_request("ExtractorOutput"))).content == "{not json"
    commentary = await provider.complete_structured(_request("TeacherOutput"))
    assert commentary.content == ""
    assert [item["phase"] for item in commentary.output_items] == ["commentary"]
    limited = await provider.complete_structured(_request("EvaluatorOutput"))
    EvaluatorOutput.model_validate_json(limited.content)
    remaining = int(limited.headers["x-ratelimit-remaining-requests"])
    assert remaining == int(limited.headers["x-ratelimit-limit-requests"]) // 10


async def test_hold_blocks_until_released() -> None:
    provider = ScriptedLLMProvider()
    gate = provider.hold("TeacherOutput")
    call = asyncio.create_task(provider.complete_structured(_request("TeacherOutput")))
    await asyncio.wait_for(gate.arrived.wait(), timeout=5)
    assert not call.done()
    gate.release.set()
    TeacherOutput.model_validate_json((await asyncio.wait_for(call, timeout=5)).content)


def test_unknown_failure_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown failure kind"):
        ScriptedLLMProvider().fail(ScriptedFailure("teapot"))


# --- Phase 4: streaming and tools (ТЗ Ф4 §8.1, seam S13, D-B-125) ---------------------------


def _stream_request(
    schema_name: str,
    input_items: tuple[Mapping[str, Any], ...] = (),
    *,
    tools: bool = False,
) -> StreamRequest:
    definitions = ({"type": "function", "name": "graph_read", "parameters": {}},) if tools else ()
    return StreamRequest(_request(schema_name), input_items, definitions)


async def _events(provider: ScriptedLLMProvider, request: StreamRequest) -> list[StreamEvent]:
    return [event async for event in provider.stream_structured(request)]


def _text(events: list[StreamEvent]) -> str:
    return "".join(event.text for event in events if isinstance(event, TextDelta))


def _items(events: list[StreamEvent]) -> tuple[dict[str, Any], ...]:
    return tuple(dict(event.item) for event in events if isinstance(event, OutputItemDone))


async def test_stream_default_answer_is_one_step_with_sorted_keys() -> None:
    """D-B-125 п.1–2: reasoning, text deltas of the answer, the answer message, `StreamCompleted`
    last; the text is the JSON of `complete_structured` with keys in alphabetical order."""
    provider = ScriptedLLMProvider()
    request = _stream_request("EvaluatorOutput")
    events = await _events(provider, request)
    assert isinstance(events[-1], StreamCompleted)
    assert events[-1].status == "completed"
    assert events[-1].model == "muse-spark-1.3"
    assert events[-1].usage.input_tokens > 0
    assert sum(isinstance(event, TextDelta) for event in events) > 1
    text = _text(events)
    EvaluatorOutput.model_validate_json(text)
    assert text == orjson.dumps(orjson.loads(text), option=orjson.OPT_SORT_KEYS).decode()
    reasoning, message = _items(events)
    assert reasoning["type"] == "reasoning" and reasoning["id"].startswith("rs_")
    assert reasoning["encrypted_content"] and reasoning["summary"] == []
    assert message["type"] == "message" and "phase" not in message
    assert message["content"][0]["text"] == text
    assert provider.stream_calls == [request]
    assert len(provider.calls_for(schema_name="EvaluatorOutput")) == 1


async def test_stream_tool_steps_wait_for_every_call_output() -> None:
    """D-B-125 п.2: a step with `function_call` (two in one step here) has no answer; the
    next step comes when the input has a `function_call_output` for each `call_id`."""
    provider = ScriptedLLMProvider()
    provider.script_tools(
        "EvaluatorOutput",
        ScriptedToolStep(
            (
                ScriptedToolCall("graph_read", {"skill": "S1"}),
                ScriptedToolCall("graph_read", {"skill": "S2"}),
            ),
            commentary="Looking at the skills.",
        ),
        ScriptedToolStep((ScriptedToolCall("graph_read", {"skill": "S3"}),)),
    )
    first = await _events(provider, _stream_request("EvaluatorOutput", tools=True))
    assert _text(first) == ""
    items = _items(first)
    assert [item["type"] for item in items] == [
        "reasoning",
        "message",
        "function_call",
        "function_call",
    ]
    assert items[1]["phase"] == "commentary"
    assert items[1]["content"][0]["text"] == "Looking at the skills."
    calls = tool_calls(items)
    assert [json.loads(call.arguments) for call in calls] == [{"skill": "S1"}, {"skill": "S2"}]
    assert all(call.call_id.startswith("call_") for call in calls)
    assert all(item["id"].startswith("fc_") for item in items[2:])
    assert all(item["status"] == "completed" for item in items[2:])
    assert len({call.call_id for call in calls}) == 2

    second_input = next_step_input((), items, {call.call_id: "{}" for call in calls})
    second = await _events(provider, _stream_request("EvaluatorOutput", second_input, tools=True))
    assert _text(second) == ""
    [call] = tool_calls(_items(second))
    assert json.loads(call.arguments) == {"skill": "S3"}
    assert not any(item["type"] == "message" for item in _items(second))

    third_input = next_step_input(second_input, _items(second), {call.call_id: "{}"})
    third = await _events(provider, _stream_request("EvaluatorOutput", third_input, tools=True))
    EvaluatorOutput.model_validate_json(_text(third))
    assert [request.input_items for request in provider.stream_calls] == [
        (),
        second_input,
        third_input,
    ]


async def test_stream_call_without_output_fails_loudly() -> None:
    provider = ScriptedLLMProvider()
    provider.script_tools(
        "EvaluatorOutput", ScriptedToolStep((ScriptedToolCall("graph_read", {}),))
    )
    items = _items(await _events(provider, _stream_request("EvaluatorOutput", tools=True)))
    carried = tuple(item for item in items if item["type"] != "message")
    with pytest.raises(AssertionError, match="function_call_output"):
        await _events(provider, _stream_request("EvaluatorOutput", carried, tools=True))


async def test_stream_incomplete_on_a_tool_step_has_no_calls() -> None:
    """D-B-125 п.5: `incomplete` on a tool step — no `function_call`, only
    `StreamCompleted(status="incomplete")`; the retry of the same step gives the calls."""
    provider = ScriptedLLMProvider()
    provider.script_tools(
        "EvaluatorOutput", ScriptedToolStep((ScriptedToolCall("graph_read", {"skill": "S1"}),))
    )
    provider.fail(ScriptedFailure("incomplete", schema_name="EvaluatorOutput"))
    request = _stream_request("EvaluatorOutput", tools=True)
    cut = await _events(provider, request)
    assert len(cut) == 1
    assert isinstance(cut[0], StreamCompleted) and cut[0].status == "incomplete"
    retry = await _events(provider, request)
    [call] = tool_calls(_items(retry))
    assert json.loads(call.arguments) == {"skill": "S1"}
    assert isinstance(retry[-1], StreamCompleted) and retry[-1].status == "completed"


async def test_stream_invalid_json_waits_for_the_answer_step() -> None:
    """D-B-125 п.5: `invalid_json` is not taken on a tool step; it breaks the answer."""
    provider = ScriptedLLMProvider()
    provider.script_tools(
        "EvaluatorOutput", ScriptedToolStep((ScriptedToolCall("graph_read", {}),))
    )
    provider.fail(ScriptedFailure("invalid_json", schema_name="EvaluatorOutput"))
    items = _items(await _events(provider, _stream_request("EvaluatorOutput", tools=True)))
    [call] = tool_calls(items)
    follow = next_step_input((), items, {call.call_id: "{}"})
    answer = await _events(provider, _stream_request("EvaluatorOutput", follow, tools=True))
    assert _text(answer) == "{not json"


TURN = {
    "p3_markup": {"intent": "explain"},
    "p1_head": {"status": "thinking"},
    "p2_body": {"blocks": [{"block_id": "b1", "text": "Hello there"}]},
}


@pytest.mark.parametrize("body_first", [False, True])
async def test_stream_body_before_head_by_flag(body_first: bool) -> None:
    """§8.1: keys in alphabetical order (`p1_head` first); with `body_before_head` the body
    comes before the head, and the parser of B notices it (Т-§12.2)."""
    provider = ScriptedLLMProvider(body_before_head=body_first)
    provider.respond("TeacherOutputV2", lambda _r: TURN)
    text = _text(await _events(provider, _stream_request("TeacherOutputV2")))
    assert json.loads(text) == TURN
    parser = TurnStreamParser()
    parser.feed(text)
    assert parser.body_before_head is body_first
    assert text.startswith('{"p2_body"' if body_first else '{"p1_head"')


@pytest.mark.parametrize(
    ("kind", "error"),
    [
        ("unavailable", ProviderUnavailableError),
        ("rate_limited", ProviderRateLimitedError),
        ("bad_request", ProviderBadRequestError),
    ],
)
async def test_stream_failures_raise_before_the_first_event(
    kind: str, error: type[Exception]
) -> None:
    provider = ScriptedLLMProvider()
    provider.fail(ScriptedFailure(kind, schema_name="EvaluatorOutput", retry_after="3"))
    stream = provider.stream_structured(_stream_request("EvaluatorOutput"))
    with pytest.raises(error):
        await anext(stream)
    EvaluatorOutput.model_validate_json(
        _text(await _events(provider, _stream_request("EvaluatorOutput")))
    )


async def test_stream_broken_answers() -> None:
    """D-B-125 п.5: `incomplete` — part of the text, then `status="incomplete"`;
    `invalid_json` — completed non-JSON text; `commentary_only` — only the commentary item;
    `ratelimit_headers` — a normal answer with the headers in `StreamCompleted`."""
    provider = ScriptedLLMProvider()
    for kind, schema in (
        ("incomplete", "EvaluatorOutput"),
        ("invalid_json", "ExtractorOutput"),
        ("commentary_only", "TeacherOutput"),
    ):
        provider.fail(ScriptedFailure(kind, schema_name=schema))
    provider.fail(
        ScriptedFailure("ratelimit_headers", schema_name="TaskGenOutput", remaining_share=0.5)
    )

    cut = await _events(provider, _stream_request("EvaluatorOutput"))
    assert isinstance(cut[-1], StreamCompleted) and cut[-1].status == "incomplete"
    whole = _text(await _events(provider, _stream_request("EvaluatorOutput")))
    assert _text(cut) and whole.startswith(_text(cut)) and _text(cut) != whole
    assert not any(item["type"] == "message" for item in _items(cut))

    broken = await _events(provider, _stream_request("ExtractorOutput"))
    assert _text(broken) == "{not json"
    assert isinstance(broken[-1], StreamCompleted) and broken[-1].status == "completed"

    commentary = await _events(provider, _stream_request("TeacherOutput"))
    assert _text(commentary) == ""
    assert [item.get("phase") for item in _items(commentary)] == ["commentary"]

    limited = await _events(provider, _stream_request("TaskGenOutput"))
    TaskGenOutput.model_validate_json(_text(limited))
    assert isinstance(limited[-1], StreamCompleted)
    remaining = int(limited[-1].headers["x-ratelimit-remaining-requests"])
    assert remaining == int(limited[-1].headers["x-ratelimit-limit-requests"]) // 2


async def test_stream_hold_blocks_until_released() -> None:
    provider = ScriptedLLMProvider()
    gate = provider.hold("EvaluatorOutput")
    call = asyncio.create_task(_events(provider, _stream_request("EvaluatorOutput")))
    await asyncio.wait_for(gate.arrived.wait(), timeout=5)
    assert not call.done()
    gate.release.set()
    EvaluatorOutput.model_validate_json(_text(await asyncio.wait_for(call, timeout=5)))
