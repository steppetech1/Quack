"""Provider of the gateway over the Responses API `POST /responses` (Т-§11.1, Т-§11.5, WP5.7).

One client per process, owned by the gateway (§4.2): `AsyncOpenAI` of the official SDK with
`base_url` of the Meta API. No retries of its own (`max_retries = 0`): the gateway retries and
writes every attempt to the ledger (WP5.2). The response is read raw, with its headers
(`retry-after`, `x-ratelimit-*`, WP5.3).

Request body (Т-§11.1): `model`, `input` (stable part first), `store: false`, `text.format`
(`json_schema`, `strict: true`, the strict schema of the gateway), `reasoning: {effort}`,
`max_output_tokens`, `prompt_cache_key`. Never sent: `temperature`, `response_format`,
`prompt_cache_retention`, `previous_response_id`, `tools`, `tool_choice`.

Output items (Т-§11.5): `reasoning` is not parsed and is returned as is; a `message` with
`phase: commentary` is not the answer — it goes to the log; the answer is the text of the
last `message` without that phase. The order of JSON keys is never relied on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx2
import openai
import orjson
from openai import AsyncOpenAI

from quack.llm.errors import (
    ProviderBadRequestError,
    ProviderIncompleteError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from quack.llm.provider import RATE_LIMIT_HEADERS, ProviderRequest, ProviderResponse
from quack.llm.streaming import (
    OutputItemDone,
    StreamCompleted,
    StreamEvent,
    StreamRequest,
    StreamUsage,
    TextDelta,
)
from quack.logging import get_logger

log = get_logger(__name__)

MISSING_API_KEY = "missing"

# The repair message of the one schema retry (WP5.2); the wording is the Phase 1 provider's.
REPAIR_TEMPLATE = (
    "The previous answer did not match the JSON schema: {error}. Answer again with valid JSON only."
)


def _rate_limit_headers(headers: Mapping[str, str]) -> dict[str, str]:
    lowered = {k.lower(): v for k, v in headers.items()}
    return {name: lowered[name] for name in RATE_LIMIT_HEADERS if name in lowered}


def _seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # an HTTP date: the gateway parses the raw header (WP5.3)


def _message_text(item: Mapping[str, Any]) -> str:
    parts = item.get("content") or []
    return "".join(
        str(part.get("text", ""))
        for part in parts
        if isinstance(part, Mapping) and part.get("type") == "output_text"
    )


class ResponsesClient:
    """`LLMProvider` over `POST {base_url}/responses`; `http_client` lets tests pass a
    `MockTransport` (`T-L8`). The SDK (openai 3.x) runs on `httpx2`, so the client is an
    `httpx2.AsyncClient`."""

    def __init__(
        self, base_url: str, api_key: str, *, http_client: httpx2.AsyncClient | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # The HTTP client is held here so that its owner can close it (`client.aclose()`).
        self.client = http_client or openai.DefaultAsyncHttpxClient()
        # The SDK refuses an empty key; without one (dev, tests) a call gets 401 from the
        # provider, which the gateway sees as unavailable — as with the Phase 1 provider.
        self.sdk = AsyncOpenAI(
            base_url=self.base_url,
            api_key=api_key or MISSING_API_KEY,
            max_retries=0,
            http_client=self.client,
        )

    def _body(self, request: ProviderRequest) -> dict[str, Any]:
        messages: list[dict[str, str]] = [{"role": "user", "content": request.prompt}]
        if request.repair_hint:
            messages.append(
                {"role": "user", "content": REPAIR_TEMPLATE.format(error=request.repair_hint)}
            )
        body: dict[str, Any] = {
            "model": request.model,
            "input": messages,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "strict": True,
                    "schema": request.json_schema,
                }
            },
        }
        if request.reasoning_effort is not None:
            body["reasoning"] = {"effort": request.reasoning_effort}
        if request.max_output_tokens is not None:
            body["max_output_tokens"] = request.max_output_tokens
        if request.prompt_cache_key is not None:
            body["prompt_cache_key"] = request.prompt_cache_key
        return body

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        try:
            raw = await self.sdk.responses.with_raw_response.create(
                **self._body(request), timeout=request.timeout_sec
            )
        except openai.RateLimitError as exc:
            headers = _rate_limit_headers(exc.response.headers)
            raise ProviderRateLimitedError(
                _seconds(headers.get("retry-after")), headers=headers, body=exc.response.text
            ) from exc
        except openai.BadRequestError as exc:
            raise ProviderBadRequestError(exc.response.text) from exc
        except openai.APIStatusError as exc:
            raise ProviderUnavailableError(f"provider returned HTTP {exc.status_code}") from exc
        except (openai.APIConnectionError, openai.APITimeoutError, httpx2.HTTPError) as exc:
            raise ProviderUnavailableError(str(exc)) from exc

        headers = _rate_limit_headers(raw.headers)
        data: dict[str, Any] = raw.http_response.json()
        output = [item for item in data.get("output") or [] if isinstance(item, Mapping)]
        answer = ""
        for item in output:
            if item.get("type") != "message":
                continue
            if item.get("phase") == "commentary":
                log.info("llm_commentary", model=request.model, text=_message_text(item))
                continue
            answer = _message_text(item)
        if data.get("status") == "incomplete":
            raise ProviderIncompleteError(answer, headers=headers)
        usage = data.get("usage") or {}
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        return ProviderResponse(
            content=answer,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cached_tokens=int(input_details.get("cached_tokens", 0)),
            model=str(data.get("model", request.model)),
            reasoning_tokens=int(output_details.get("reasoning_tokens", 0)),
            output_items=tuple(dict(item) for item in output),
            headers=headers,
        )

    async def stream_structured(self, request: StreamRequest) -> AsyncIterator[StreamEvent]:
        """WP10.2 (Т-§11.1, Т-§11.5): `/responses` with `stream: true`. Text deltas only of
        the answer message (not `phase: commentary`); every finished output item raw; last —
        `StreamCompleted` with usage and the rate-limit headers. An error event of the
        stream is `ProviderUnavailableError`; `incomplete` is reported, never parsed."""
        body = self._body(request.base)
        body["stream"] = True
        body["input"] = [*body["input"], *(dict(item) for item in request.input_items)]
        if request.tools:
            body["tools"] = [dict(tool) for tool in request.tools]
            body["tool_choice"] = "auto"  # the only choice Meta accepts (Т-§1.3)
        try:
            async with self.sdk.responses.with_streaming_response.create(
                **body, timeout=request.base.timeout_sec
            ) as raw:
                headers = _rate_limit_headers(raw.headers)
                commentary: set[str] = set()
                async for line in raw.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = orjson.loads(line.removeprefix("data:").strip())
                    for out in _stream_event(event, commentary, headers, request.base.model):
                        yield out
        except openai.RateLimitError as exc:
            headers = _rate_limit_headers(exc.response.headers)
            raise ProviderRateLimitedError(
                _seconds(headers.get("retry-after")), headers=headers, body=exc.response.text
            ) from exc
        except openai.BadRequestError as exc:
            raise ProviderBadRequestError(exc.response.text) from exc
        except openai.APIStatusError as exc:
            raise ProviderUnavailableError(f"provider returned HTTP {exc.status_code}") from exc
        except (openai.APIConnectionError, openai.APITimeoutError, httpx2.HTTPError) as exc:
            raise ProviderUnavailableError(str(exc)) from exc

    async def aclose(self) -> None:
        """Close the HTTP client (called by `LLMGateway.aclose`, §4.2)."""
        await self.sdk.close()


def _stream_event(
    event: Mapping[str, Any], commentary: set[str], headers: Mapping[str, str], model: str
) -> list[StreamEvent]:
    """One server-sent event of `/responses` → stream events of the gateway (WP10.2)."""
    kind = event.get("type")
    if kind == "response.output_item.added":
        item = event.get("item") or {}
        if item.get("type") == "message" and item.get("phase") == "commentary":
            commentary.add(str(item.get("id")))
        return []
    if kind == "response.output_text.delta":
        if str(event.get("item_id")) in commentary:
            return []
        return [TextDelta(str(event.get("delta", "")))]
    if kind == "response.output_item.done":
        item = dict(event.get("item") or {})
        if item.get("type") == "message" and item.get("phase") == "commentary":
            log.info("llm_commentary", model=model, text=_message_text(item))
        return [OutputItemDone(item)]
    if kind in ("response.completed", "response.incomplete"):
        response = event.get("response") or {}
        usage = response.get("usage") or {}
        status = "incomplete" if kind == "response.incomplete" else "completed"
        if response.get("status") == "incomplete":
            status = "incomplete"
        return [
            StreamCompleted(
                status=status,
                usage=StreamUsage(
                    input_tokens=int(usage.get("input_tokens", 0)),
                    cached_tokens=int(
                        (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
                    ),
                    output_tokens=int(usage.get("output_tokens", 0)),
                    reasoning_tokens=int(
                        (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
                    ),
                ),
                model=str(response.get("model", model)),
                headers=dict(headers),
            )
        ]
    if kind in ("response.failed", "error"):
        raise ProviderUnavailableError(f"stream error event {kind}: {orjson.dumps(event).decode()}")
    return []
