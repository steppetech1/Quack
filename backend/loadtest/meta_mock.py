"""Stand-in Meta API server `meta-mock` (ТЗ Фазы 3 WP10.3, Д-§11, Т-§11.1, Т-§11.8).

A separate ASGI process that answers `POST /responses` as the Responses API does on the wire:

- the answer is the **minimal valid** instance of the JSON Schema of `text.format`; keys of
  every object are sorted, at the root and nested (Т-§11.8); the teacher's answer length in
  characters is a parameter;
- `output` = a `reasoning` item with opaque `encrypted_content`, then the `message`. With
  `tools`: a `message` with `phase: commentary`, then one or two `function_call` (in turn);
- latency by the measured profile (`META_MOCK_LATENCY=measured`) or none (`zero`);
- `x-ratelimit-*` headers from a bucket refilled at limit/60 per second; an empty bucket is
  429; one bucket for both models when `META_MOCK_SHARED_LIMIT=1`;
- a given share of 429 with `retry-after` as seconds, an HTTP date, none or all three in turn;
  a given share of cut answers (`status: incomplete`, partial JSON);
- `response_format`, `temperature` or no `store: false` → 400, as Meta does (Т-§11.1).

Run: `uvicorn --factory loadtest.meta_mock:app_from_env` (service `meta-mock`, profile
`loadtest`). Values the documents do not give are recorded in PHASE3_NOTES D-C-023.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any, Literal, cast, get_args
from uuid import uuid4

import orjson
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

type Latency = Literal["measured", "zero"]
type RetryAfterFormat = Literal["seconds", "http-date", "none", "mixed"]

# Д-§11, Т-§11.8: Wonder — first token after 8 s (low) / 15 s (high), the answer after it about
# 6 s more; Spark — 3 s (low) / 4.4 s (high) per call.
WONDER_FIRST_TOKEN_S = {"low": 8.0, "high": 15.0}
WONDER_ANSWER_S = 6.0
SPARK_CALL_S = {"low": 3.0, "high": 4.4}
# D-C-023: values of the stand that the documents do not give.
REASONING_TOKENS = {"low": 256, "high": 1024}
CHARS_PER_TOKEN = 4
SHORT_TEXT = "text"
RETRY_AFTER_S = 1
RETRY_CYCLE: tuple[RetryAfterFormat, ...] = ("seconds", "http-date", "none")
FIXED_UUID = "00000000-0000-4000-8000-000000000000"
FIXED_DATETIME = "2026-01-01T00:00:00Z"
FIXED_DATE = "2026-01-01"
TEACHER_SCHEMA = "TeacherOutput"


@dataclass(frozen=True)
class MetaMockConfig:
    """`META_MOCK_*` settings of the stand (WP10.3)."""

    latency: Latency = "measured"
    rpm: int = 3000
    tpm: int = 4_000_000
    shared_limit: bool = True
    share_429: float = 0.0
    retry_after: RetryAfterFormat = "mixed"
    incomplete_share: float = 0.0
    teacher_chars: int = 600

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> MetaMockConfig:
        source = dict(os.environ) if env is None else env
        default = cls()
        latency = source.get("META_MOCK_LATENCY", default.latency)
        retry_after = source.get("META_MOCK_RETRY_AFTER", default.retry_after)
        if latency not in get_args(Latency.__value__):
            raise ValueError(f"META_MOCK_LATENCY: {latency!r}")
        if retry_after not in get_args(RetryAfterFormat.__value__):
            raise ValueError(f"META_MOCK_RETRY_AFTER: {retry_after!r}")
        return cls(
            latency=cast(Latency, latency),
            rpm=int(source.get("META_MOCK_RPM", default.rpm)),
            tpm=int(source.get("META_MOCK_TPM", default.tpm)),
            shared_limit=source.get("META_MOCK_SHARED_LIMIT", "1") == "1",
            share_429=float(source.get("META_MOCK_429_SHARE", default.share_429)),
            retry_after=cast(RetryAfterFormat, retry_after),
            incomplete_share=float(
                source.get("META_MOCK_INCOMPLETE_SHARE", default.incomplete_share)
            ),
            teacher_chars=int(source.get("META_MOCK_TEACHER_CHARS", default.teacher_chars)),
        )


# --- minimal valid instance -----------------------------------------------------------------


def minimal_instance(schema: dict[str, Any], *, text_chars: int | None = None) -> Any:
    """The minimal valid instance of a strict JSON Schema: required fields filled, nullable
    ones `null`, arrays empty (or `minItems` long), enum — the first value, numbers — the
    middle of the allowed interval, strings — short text (`text_chars` long when given).
    Keys of every object are sorted. `pattern` of a string is not followed: the nine output
    schemas have none; a schema with one needs a rule here first."""
    if "anyOf" in schema:
        return minimal_instance(schema["anyOf"][0], text_chars=text_chars)
    kind = schema.get("type")
    if isinstance(kind, list):
        if "null" in kind:
            return None
        kind = kind[0]
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object":
        properties: dict[str, Any] = schema.get("properties", {})
        return {
            name: minimal_instance(properties[name], text_chars=text_chars)
            for name in sorted(properties)
        }
    if kind == "array":
        count = int(schema.get("minItems", 0))
        return [minimal_instance(schema["items"], text_chars=text_chars) for _ in range(count)]
    if kind in ("number", "integer"):
        return _middle(schema, integer=kind == "integer")
    if kind == "boolean":
        return False
    if kind == "string":
        return _string(schema, text_chars)
    if kind == "null":
        return None
    raise ValueError(f"unsupported schema node {schema!r}")


def _middle(schema: dict[str, Any], *, integer: bool) -> int | float:
    """The middle of the allowed interval; for an integer the excluded bounds are first moved
    to the nearest allowed values, so `exclusiveMinimum: 1, exclusiveMaximum: 2.5` gives 2."""
    low = schema.get("minimum")
    high = schema.get("maximum")
    if "exclusiveMinimum" in schema:
        bound = schema["exclusiveMinimum"]
        low = math.floor(bound) + 1 if integer else bound
    if "exclusiveMaximum" in schema:
        bound = schema["exclusiveMaximum"]
        high = math.ceil(bound) - 1 if integer else bound
    if low is not None and high is not None:
        value = (low + high) / 2
    elif low is not None:
        value = low + (1 if "exclusiveMinimum" in schema and not integer else 0)
    elif high is not None:
        value = high - (1 if "exclusiveMaximum" in schema and not integer else 0)
    else:
        value = 0
    return math.floor(value) if integer else float(value)


def _string(schema: dict[str, Any], text_chars: int | None) -> str:
    fmt = schema.get("format")
    if fmt == "uuid":
        return FIXED_UUID
    if fmt == "date-time":
        return FIXED_DATETIME
    if fmt == "date":
        return FIXED_DATE
    length = len(SHORT_TEXT) if text_chars is None else text_chars
    length = max(length, int(schema.get("minLength", 0)))
    if "maxLength" in schema:
        length = min(length, int(schema["maxLength"]))
    return (SHORT_TEXT * (length // len(SHORT_TEXT) + 1))[:length]


# --- latency and limits ---------------------------------------------------------------------


def latency_s(model: str, effort: str, latency: Latency) -> float:
    """Seconds before the answer by the measured profile (Д-§11); `zero` — none."""
    if latency == "zero":
        return 0.0
    level = "high" if effort == "high" else "low"
    if "wonder" in model:
        return WONDER_FIRST_TOKEN_S[level] + WONDER_ANSWER_S
    return SPARK_CALL_S[level]


@dataclass
class _Bucket:
    limit: int
    level: float = field(init=False)
    at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.level = float(self.limit)

    def refill(self, now: float) -> None:
        self.level = min(float(self.limit), self.level + (now - self.at) * self.limit / 60)
        self.at = now

    def seconds_until(self, amount: float) -> float:
        missing = amount - self.level
        return 0.0 if missing <= 0 else missing * 60 / self.limit


@dataclass
class _Limits:
    requests: _Bucket
    tokens: _Bucket

    def headers(self) -> dict[str, str]:
        return {
            "x-ratelimit-limit-requests": str(self.requests.limit),
            "x-ratelimit-limit-tokens": str(self.tokens.limit),
            "x-ratelimit-remaining-requests": str(max(0, math.floor(self.requests.level))),
            "x-ratelimit-remaining-tokens": str(max(0, math.floor(self.tokens.level))),
        }


class _Share:
    """Deterministic share of hits: `share = 1` hits every call, `0.5` every second one."""

    def __init__(self, share: float) -> None:
        self.share = share
        self.acc = 0.0

    def hit(self) -> bool:
        self.acc += self.share
        if self.acc >= 1.0 - 1e-9:
            self.acc -= 1.0
            return True
        return False


@dataclass
class _State:
    config: MetaMockConfig
    limits: dict[str, _Limits] = field(default_factory=dict)
    retry_turn: int = 0
    tool_turn: int = 0
    share_429: _Share = field(init=False)
    share_incomplete: _Share = field(init=False)

    def __post_init__(self) -> None:
        self.share_429 = _Share(self.config.share_429)
        self.share_incomplete = _Share(self.config.incomplete_share)

    def limits_for(self, model: str) -> _Limits:
        key = "shared" if self.config.shared_limit else model
        if key not in self.limits:
            self.limits[key] = _Limits(_Bucket(self.config.rpm), _Bucket(self.config.tpm))
        return self.limits[key]

    def retry_format(self) -> RetryAfterFormat:
        if self.config.retry_after != "mixed":
            return self.config.retry_after
        fmt = RETRY_CYCLE[self.retry_turn % len(RETRY_CYCLE)]
        self.retry_turn += 1
        return fmt


# --- responses ------------------------------------------------------------------------------


def _json(body: Any, status: int, headers: dict[str, str] | None = None) -> Response:
    return Response(
        orjson.dumps(body, option=orjson.OPT_SORT_KEYS),
        status_code=status,
        media_type="application/json",
        headers=headers,
    )


def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> Response:
    kind = "rate_limit_error" if status == 429 else "invalid_request_error"
    return _json({"error": {"code": code, "message": message, "type": kind}}, status, headers)


def _contract_violation(body: dict[str, Any]) -> str | None:
    """Т-§11.1: what Meta rejects with 400 and what the adapter must never send."""
    if "response_format" in body:
        return "response_format is not supported on /responses; use text.format"
    if "temperature" in body:
        return "temperature is not supported"
    if body.get("store") is not False:
        return "store must be false"
    fmt = body.get("text", {}).get("format", {})
    if fmt.get("type") != "json_schema" or not isinstance(fmt.get("schema"), dict):
        return "text.format must be a json_schema"
    return None


def _retry_after(fmt: RetryAfterFormat, seconds: float) -> dict[str, str]:
    wait = max(RETRY_AFTER_S, math.ceil(seconds))
    if fmt == "seconds":
        return {"retry-after": str(wait)}
    if fmt == "http-date":
        return {"retry-after": format_datetime(datetime.now(UTC) + timedelta(seconds=wait), True)}
    return {}


def _tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


def _item_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _reasoning_item() -> dict[str, Any]:
    opaque = base64.b64encode(hashlib.sha256(uuid4().bytes).digest() * 4).decode()
    return {
        "encrypted_content": opaque,
        "id": _item_id("rs"),
        "summary": [],
        "type": "reasoning",
    }


def _message(text: str, *, status: str = "completed", phase: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "content": [{"annotations": [], "text": text, "type": "output_text"}],
        "id": _item_id("msg"),
        "role": "assistant",
        "status": status,
        "type": "message",
    }
    if phase is not None:
        message["phase"] = phase
    return message


def _function_calls(tools: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    tool = tools[0]
    arguments = minimal_instance(tool.get("parameters", {"type": "object", "properties": {}}))
    return [
        {
            "arguments": orjson.dumps(arguments, option=orjson.OPT_SORT_KEYS).decode(),
            "call_id": _item_id("call"),
            "id": _item_id("fc"),
            "name": tool["name"],
            "status": "completed",
            "type": "function_call",
        }
        for _ in range(count)
    ]


async def _responses(request: Request) -> Response:
    state: _State = request.app.state.meta_mock
    config = state.config
    try:
        body = orjson.loads(await request.body())
    except orjson.JSONDecodeError:
        return _error(400, "invalid_json", "request body is not JSON")
    if not isinstance(body, dict):
        return _error(400, "invalid_request", "request body is not an object")
    body = cast(dict[str, Any], body)
    violation = _contract_violation(body)
    if violation is not None:
        return _error(400, "invalid_request", violation)

    model = str(body.get("model", ""))
    effort = str(body.get("reasoning", {}).get("effort", "low"))
    fmt: dict[str, Any] = body["text"]["format"]
    tools: list[dict[str, Any]] = body.get("tools") or []
    limits = state.limits_for(model)
    now = time.monotonic()
    limits.requests.refill(now)
    limits.tokens.refill(now)

    if state.share_429.hit():
        headers = {**limits.headers(), **_retry_after(state.retry_format(), RETRY_AFTER_S)}
        return _error(429, "rate_limit_exceeded", "scripted 429 share", headers)

    text_chars = config.teacher_chars if fmt.get("name") == TEACHER_SCHEMA else None
    answer = orjson.dumps(
        minimal_instance(fmt["schema"], text_chars=text_chars), option=orjson.OPT_SORT_KEYS
    ).decode()
    input_tokens = _tokens(orjson.dumps(body.get("input", "")).decode())
    reasoning_tokens = REASONING_TOKENS["high" if effort == "high" else "low"]
    output_tokens = _tokens(answer) + reasoning_tokens
    if limits.requests.level < 1 or limits.tokens.level < input_tokens + output_tokens:
        wait = max(
            limits.requests.seconds_until(1),
            limits.tokens.seconds_until(input_tokens + output_tokens),
        )
        headers = {**limits.headers(), **_retry_after(state.retry_format(), wait)}
        return _error(429, "rate_limit_exceeded", "rate limit reached", headers)
    limits.requests.level -= 1
    limits.tokens.level -= input_tokens + output_tokens

    await asyncio.sleep(latency_s(model, effort, config.latency))

    status = "completed"
    output: list[dict[str, Any]] = [_reasoning_item()]
    if tools:
        output.append(_message("Checking the step.", phase="commentary"))
        output.extend(_function_calls(tools, 1 + state.tool_turn % 2))
        state.tool_turn += 1
    elif state.share_incomplete.hit():
        status = "incomplete"
        output.append(_message(answer[: max(1, len(answer) // 2)], status=status))
    else:
        output.append(_message(answer))
    response = {
        "created_at": int(time.time()),
        "id": _item_id("resp"),
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "model": model,
        "object": "response",
        "output": output,
        "status": status,
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": output_tokens,
            "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
            "total_tokens": input_tokens + output_tokens,
        },
    }
    return _json(response, 200, limits.headers())


def create_app(config: MetaMockConfig | None = None) -> Starlette:
    app = Starlette(routes=[Route("/responses", _responses, methods=["POST"])])
    app.state.meta_mock = _State(config or MetaMockConfig())
    return app


def app_from_env() -> Starlette:
    """Factory for `uvicorn --factory loadtest.meta_mock:app_from_env`."""
    return create_app(MetaMockConfig.from_env())
