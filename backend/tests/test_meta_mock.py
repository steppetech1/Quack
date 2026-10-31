"""Stand-in Meta API server `meta-mock` on the wire (ТЗ Фазы 3 WP10.3, Д-§11, Т-§11.1, Т-§11.8).

The request bodies follow the Responses API contract of `T-L8`; `T-O5` goes through the
`ResponsesClient` adapter of WP5.7.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import httpx2
import pytest
from pydantic import BaseModel

from loadtest.meta_mock import MetaMockConfig, create_app, latency_s, minimal_instance
from quack.artifacts.schemas import ArtifactGenOutput
from quack.domains.schemas import CuratorOutput, DiagnosticOutput, StructurerOutput
from quack.llm.errors import ProviderIncompleteError, ProviderRateLimitedError
from quack.llm.pressure import parse_retry_after
from quack.llm.provider import ProviderRequest
from quack.llm.responses_client import ResponsesClient
from quack.llm.strict_schema import to_strict_json_schema
from quack.practice.schemas import EvaluatorOutput, TaskGenOutput
from quack.session.schemas import (
    ExtractorOutput,
    ExtractorOutputV2,
    TeacherOutput,
)

# The nine output models of T-O5; `CheckpointerOutput` → `ExtractorOutputV2` (WP13, D-C-029).
OUTPUT_MODELS: tuple[type[BaseModel], ...] = (
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
RATE_LIMIT_HEADERS = (
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
)
ZERO = MetaMockConfig(latency="zero")


def _body(output: type[BaseModel], **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "muse-spark-1.3",
        "input": [{"role": "user", "content": "Hello"}],
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": output.__name__,
                "strict": True,
                "schema": to_strict_json_schema(output),
            }
        },
        "reasoning": {"effort": "low"},
        "max_output_tokens": 4096,
        "prompt_cache_key": "teacher@1",
    }
    body.update(extra)
    return body


def _client(config: MetaMockConfig = ZERO) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(config)), base_url="http://meta-mock"
    )


def _keys_sorted(node: Any, path: str = "$") -> None:
    if isinstance(node, dict):
        keys = list(node)  # pyright: ignore[reportUnknownArgumentType]
        assert keys == sorted(keys), f"{path}: keys {keys} are not sorted"
        for key, value in node.items():  # pyright: ignore[reportUnknownVariableType]
            _keys_sorted(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):  # pyright: ignore[reportUnknownArgumentType,reportUnknownVariableType]
            _keys_sorted(value, f"{path}[{i}]")


def _answer_text(body: dict[str, Any]) -> str:
    message = [o for o in body["output"] if o["type"] == "message"][-1]
    return message["content"][0]["text"]


@pytest.mark.parametrize("model", OUTPUT_MODELS, ids=lambda m: m.__name__)
def test_minimal_instance_validates(model: type[BaseModel]) -> None:
    """The minimal valid instance of the strict schema passes `model_validate_json`; keys are
    sorted at the root and in nested objects (Т-§11.8)."""
    instance = minimal_instance(to_strict_json_schema(model))
    _keys_sorted(instance)
    model.model_validate_json(json.dumps(instance))


def test_minimal_instance_rules() -> None:
    """Required fields filled, nullable ones `null`, arrays empty, enum — the first value,
    numbers — the middle of the allowed interval, strings — short text."""
    schema = {
        "type": "object",
        "properties": {
            "note": {"type": ["string", "null"]},
            "items": {"type": "array", "items": {"type": "string"}},
            "move": {"type": "string", "enum": ["explain", "summarize"]},
            "score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "level": {"type": "integer", "minimum": 1, "maximum": 3},
            "text": {"type": "string"},
            "flag": {"type": "boolean"},
        },
        "required": ["note", "items", "move", "score", "level", "text", "flag"],
        "additionalProperties": False,
    }
    instance = minimal_instance(schema)
    assert instance["note"] is None
    assert instance["items"] == []
    assert instance["move"] == "explain"
    assert instance["score"] == 0.5
    assert instance["level"] == 2
    assert isinstance(instance["text"], str) and 0 < len(instance["text"]) <= 16
    assert isinstance(instance["flag"], bool)
    assert minimal_instance(schema, text_chars=50)["text"] != instance["text"]


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        ({"type": "integer", "exclusiveMinimum": 1, "exclusiveMaximum": 3}, 2),
        ({"type": "integer", "exclusiveMinimum": 1, "maximum": 2}, 2),
        ({"type": "integer", "minimum": 1, "exclusiveMaximum": 2}, 1),
        ({"type": "integer", "exclusiveMinimum": 0}, 1),
        ({"type": "number", "exclusiveMinimum": 0.0, "exclusiveMaximum": 1.0}, 0.5),
    ],
)
def test_minimal_number_respects_excluded_bounds(node: dict[str, Any], expected: float) -> None:
    """Review of B on #32: an integer between excluded bounds stays inside them."""
    assert minimal_instance(node) == expected


async def test_response_shape() -> None:
    """`output` = reasoning with `encrypted_content`, then the message; `usage` by kind; four
    `x-ratelimit-*` headers; keys of every object of the answer sorted."""
    async with _client() as client:
        response = await client.post("/responses", json=_body(TeacherOutput))
    assert response.status_code == 200, response.text
    body = response.json()
    _keys_sorted(body)
    assert body["status"] == "completed"
    assert [o["type"] for o in body["output"]] == ["reasoning", "message"]
    assert body["output"][0]["encrypted_content"]
    TeacherOutput.model_validate_json(_answer_text(body))
    usage = body["usage"]
    assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0
    assert usage["input_tokens_details"]["cached_tokens"] == 0
    assert usage["output_tokens_details"]["reasoning_tokens"] > 0
    for name in RATE_LIMIT_HEADERS:
        assert name in response.headers, name


async def test_teacher_reply_length_is_a_parameter() -> None:
    async with _client(MetaMockConfig(latency="zero", teacher_chars=1234)) as client:
        response = await client.post("/responses", json=_body(TeacherOutput))
    reply = TeacherOutput.model_validate_json(_answer_text(response.json())).reply_text
    assert len(reply) == 1234


async def test_reasoning_tokens_by_effort() -> None:
    async with _client() as client:
        low = (await client.post("/responses", json=_body(ExtractorOutput))).json()
        high = (
            await client.post(
                "/responses", json=_body(ExtractorOutput, reasoning={"effort": "high"})
            )
        ).json()
    low_tokens = low["usage"]["output_tokens_details"]["reasoning_tokens"]
    assert high["usage"]["output_tokens_details"]["reasoning_tokens"] > low_tokens


async def test_tools_give_commentary_and_function_calls() -> None:
    """With `tools`: a `message` with `phase: commentary`, then one or two `function_call`."""
    tool = {
        "type": "function",
        "name": "check_answer",
        "parameters": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
        "strict": True,
    }
    counts: set[int] = set()
    async with _client() as client:
        for _ in range(2):
            response = await client.post("/responses", json=_body(TeacherOutput, tools=[tool]))
            body = response.json()
            _keys_sorted(body)
            types = [o["type"] for o in body["output"]]
            assert types[0] == "reasoning"
            assert body["output"][1]["type"] == "message"
            assert body["output"][1]["phase"] == "commentary"
            calls = [o for o in body["output"] if o["type"] == "function_call"]
            assert types[2:] == ["function_call"] * len(calls)
            for call in calls:
                assert call["name"] == "check_answer"
                assert set(json.loads(call["arguments"])) == {"answer"}
            counts.add(len(calls))
    assert counts == {1, 2}


async def test_429_share_cycles_retry_after_formats() -> None:
    """`META_MOCK_429_SHARE = 1`: every answer is 429; `retry-after` in seconds, as an HTTP
    date and absent, in turn (`mixed`)."""
    config = MetaMockConfig(latency="zero", share_429=1.0)
    async with _client(config) as client:
        responses = [await client.post("/responses", json=_body(TeacherOutput)) for _ in range(3)]
    assert [r.status_code for r in responses] == [429, 429, 429]
    seconds, date, absent = (r.headers.get("retry-after") for r in responses)
    assert seconds is not None and float(seconds) >= 0
    assert date is not None and parsedate_to_datetime(date) is not None
    assert absent is None
    for response in responses:
        for name in RATE_LIMIT_HEADERS:
            assert name in response.headers, name


@pytest.mark.parametrize("fmt", ["seconds", "http-date", "none"])
async def test_429_single_retry_after_format(fmt: str) -> None:
    config = MetaMockConfig(latency="zero", share_429=1.0, retry_after=fmt)  # pyright: ignore[reportArgumentType]
    async with _client(config) as client:
        headers = (await client.post("/responses", json=_body(TeacherOutput))).headers
    assert ("retry-after" in headers) == (fmt != "none")


async def test_incomplete_share() -> None:
    """`META_MOCK_INCOMPLETE_SHARE = 1`: `status: incomplete` with partial JSON."""
    config = MetaMockConfig(latency="zero", incomplete_share=1.0)
    async with _client(config) as client:
        body = (await client.post("/responses", json=_body(TeacherOutput))).json()
    assert body["status"] == "incomplete"
    assert body["incomplete_details"]["reason"] == "max_output_tokens"
    with pytest.raises(ValueError):
        json.loads(_answer_text(body))


@pytest.mark.parametrize(
    "extra",
    [
        {"response_format": {"type": "json_object"}},
        {"temperature": 0.2},
        {"store": True},
    ],
    ids=["response_format", "temperature", "store"],
)
async def test_contract_violations_are_400(extra: dict[str, Any]) -> None:
    async with _client() as client:
        response = await client.post("/responses", json=_body(TeacherOutput, **extra))
    assert response.status_code == 400


async def test_request_without_store_is_400() -> None:
    body = _body(TeacherOutput)
    del body["store"]
    async with _client() as client:
        response = await client.post("/responses", json=body)
    assert response.status_code == 400


async def test_empty_bucket_gives_429_and_remaining_falls() -> None:
    """The request bucket refills at limit/60 per second; an empty bucket gives 429."""
    async with _client(MetaMockConfig(latency="zero", rpm=2)) as client:
        first = await client.post("/responses", json=_body(TeacherOutput))
        second = await client.post("/responses", json=_body(TeacherOutput))
        third = await client.post("/responses", json=_body(TeacherOutput))
    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 429]
    assert int(first.headers["x-ratelimit-remaining-requests"]) > int(
        second.headers["x-ratelimit-remaining-requests"]
    )
    assert first.headers["x-ratelimit-limit-requests"] == "2"


async def test_shared_limit_spans_both_models() -> None:
    """`META_MOCK_SHARED_LIMIT=1`: one bucket for both models; `0`: a bucket per model."""
    shared = MetaMockConfig(latency="zero", rpm=1)
    separate = MetaMockConfig(latency="zero", rpm=1, shared_limit=False)
    for config, expected in ((shared, 429), (separate, 200)):
        async with _client(config) as client:
            await client.post("/responses", json=_body(TeacherOutput))
            other = await client.post(
                "/responses", json=_body(TeacherOutput, model="muse-wonder-1.0")
            )
        assert other.status_code == expected


def test_latency_profile() -> None:
    """Д-§11: Wonder — 8 s (low) / 15 s (high) to the first token, then about 6 s; Spark —
    3 s (low) / 4.4 s (high) per call; `zero` — none."""
    assert latency_s("muse-wonder-1.0", "low", "measured") == pytest.approx(14.0)
    assert latency_s("muse-wonder-1.0", "high", "measured") == pytest.approx(21.0)
    assert latency_s("muse-spark-1.3", "low", "measured") == pytest.approx(3.0)
    assert latency_s("muse-spark-1.3", "high", "measured") == pytest.approx(4.4)
    assert latency_s("muse-wonder-1.0", "high", "zero") == 0.0


def test_config_from_env() -> None:
    config = MetaMockConfig.from_env(
        {
            "META_MOCK_LATENCY": "zero",
            "META_MOCK_RPM": "10",
            "META_MOCK_TPM": "1000",
            "META_MOCK_SHARED_LIMIT": "0",
            "META_MOCK_429_SHARE": "0.5",
            "META_MOCK_RETRY_AFTER": "seconds",
            "META_MOCK_INCOMPLETE_SHARE": "0.25",
            "META_MOCK_TEACHER_CHARS": "80",
        }
    )
    assert config == MetaMockConfig("zero", 10, 1000, False, 0.5, "seconds", 0.25, 80)
    assert MetaMockConfig.from_env({}) == MetaMockConfig()


# --- T-O5: through the Responses API adapter (WP5.7) -------------------------------------


def _adapter(config: MetaMockConfig = ZERO) -> ResponsesClient:
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=create_app(config)))
    return ResponsesClient("http://meta-mock", "any-key", http_client=http)


def _request(output: type[BaseModel]) -> ProviderRequest:
    return ProviderRequest(
        model="muse-spark-1.3",
        prompt="Hello",
        json_schema=to_strict_json_schema(output),
        schema_name=output.__name__,
        temperature=0.0,
        timeout_sec=5.0,
        reasoning_effort="low",
        max_output_tokens=4096,
        prompt_cache_key=f"{output.__name__}@1",
    )


@pytest.mark.parametrize("model", OUTPUT_MODELS, ids=lambda m: m.__name__)
async def test_meta_mock_outputs_validate(model: type[BaseModel]) -> None:
    """T-O5: the adapter parses the answer of meta-mock for each of the nine output models;
    it passes `model_validate_json`; reasoning item, usage and `x-ratelimit-*` are there."""
    adapter = _adapter()
    try:
        response = await adapter.complete_structured(_request(model))
    finally:
        await adapter.aclose()
    model.model_validate_json(response.content)
    _keys_sorted(json.loads(response.content))
    assert response.output_items[0]["type"] == "reasoning"
    assert response.input_tokens > 0 and response.output_tokens > 0
    assert response.reasoning_tokens > 0
    for name in RATE_LIMIT_HEADERS:
        assert name in response.headers, name


async def test_meta_mock_429_formats_are_parsed() -> None:
    """T-O5: with `META_MOCK_429_SHARE = 1` every call is 429; `retry-after` comes in all three
    formats in turn, and each is parsed by the gateway's parser (WP5.3)."""
    adapter = _adapter(MetaMockConfig(latency="zero", share_429=1.0))
    parsed: list[float | None] = []
    try:
        for _ in range(3):
            with pytest.raises(ProviderRateLimitedError) as caught:
                await adapter.complete_structured(_request(TeacherOutput))
            raw = caught.value.headers.get("retry-after")
            parsed.append(parse_retry_after(raw, datetime.now(UTC)))
    finally:
        await adapter.aclose()
    seconds, date, absent = parsed
    assert seconds is not None and seconds >= 1
    assert date is not None and date >= 0
    assert absent is None


async def test_meta_mock_incomplete_is_cut() -> None:
    """T-O5: with `META_MOCK_INCOMPLETE_SHARE = 1` the adapter reports a cut answer."""
    adapter = _adapter(MetaMockConfig(latency="zero", incomplete_share=1.0))
    try:
        with pytest.raises(ProviderIncompleteError):
            await adapter.complete_structured(_request(TeacherOutput))
    finally:
        await adapter.aclose()
