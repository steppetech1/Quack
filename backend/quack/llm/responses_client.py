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

import base64
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import contextmanager
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
from quack.llm.media import (
    IllustrationRequest,
    ImageResponse,
    SegmentRequest,
    TranscribeRequest,
    TranscribeResponse,
    TranscriptTurn,
    box_input_tokens,
)
from quack.llm.provider import RATE_LIMIT_HEADERS, ImageInput, ProviderRequest, ProviderResponse
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


def _data_event(line: str) -> dict[str, Any] | None:
    """The JSON of one `data:` line of the stream; `None` for any other line and for the
    `data: [DONE]` terminator the live API sends after `response.completed` (found by the live
    run of 2026-11-04, WP10.7)."""
    if not line.startswith("data:"):
        return None
    payload = line.removeprefix("data:").strip()
    if not payload or payload == "[DONE]":
        return None
    return orjson.loads(payload)


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


@contextmanager
def _provider_errors() -> Iterator[None]:
    """SDK and transport errors → the provider errors of the gateway (as in the text calls)."""
    try:
        yield
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


def _check_status(response: httpx2.Response) -> None:
    """Plain-HTTP calls (voice, image): the status codes of the Meta API → provider errors.
    400, 406, 413 are the caller's fault (no retry), 429 is a rate limit, the rest is a failure
    of the provider."""
    status = response.status_code
    if status < 400:
        return
    headers = _rate_limit_headers(response.headers)
    if status == 429:
        raise ProviderRateLimitedError(
            _seconds(headers.get("retry-after")), headers=headers, body=response.text
        )
    if status in (400, 406, 413):
        raise ProviderBadRequestError(response.text)
    raise ProviderUnavailableError(f"provider returned HTTP {status}")


def image_data_url(image: ImageInput) -> str:
    """`data:{mime};base64,…` — the `image_url` of an `input_image` part (D-B-170)."""
    return f"data:{image.mime};base64,{base64.b64encode(image.data).decode('ascii')}"


class ResponsesClient:
    """`LLMProvider` over `POST {base_url}/responses`; `http_client` lets tests pass a
    `MockTransport` (`T-L8`). The SDK (openai 3.x) runs on `httpx2`, so the client is an
    `httpx2.AsyncClient`."""

    def __init__(
        self, base_url: str, api_key: str, *, http_client: httpx2.AsyncClient | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key or MISSING_API_KEY
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
        content: str | list[dict[str, str]] = request.prompt
        if request.images:
            # The Responses API content parts of Meta (B notes, WP0 п.3д): `input_text`, then
            # `input_image` with a data URL; D-B-170.
            content = [
                {"type": "input_text", "text": request.prompt},
                *(
                    {"type": "input_image", "image_url": image_data_url(image)}
                    for image in request.images
                ),
            ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
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
        usage = data.get("usage") or {}
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        if data.get("status") == "incomplete":
            raise ProviderIncompleteError(
                answer,
                headers=headers,
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                cached_tokens=int(input_details.get("cached_tokens", 0)),
                reasoning_tokens=int(output_details.get("reasoning_tokens", 0)),
            )
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

    async def segment(self, request: SegmentRequest) -> ProviderResponse:
        """`sam-3.1` over `/responses` (WP0 п.3д, WP10.1): one user message with `input_text`
        (the name of the object or the box tokens) and `input_image`; no `text.format`, the
        answer is the special-token text of `output_text`, parsed by the gateway."""
        if (request.concept is None) == (request.box is None):
            raise ValueError("segment: exactly one of concept and box")
        prompt = box_input_tokens(request.box) if request.box else str(request.concept)
        body: dict[str, Any] = {
            "model": request.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": image_data_url(request.image)},
                    ],
                }
            ],
            "store": False,
            "metadata": {
                "mask_encoding": request.mask_encoding,
                "include_confidence": "true" if request.include_confidence else "false",
            },
        }
        with _provider_errors():
            raw = await self.sdk.responses.with_raw_response.create(
                **body, timeout=request.timeout_sec
            )
        headers = _rate_limit_headers(raw.headers)
        data: dict[str, Any] = raw.http_response.json()
        answer = ""
        for item in data.get("output") or []:
            if item.get("type") == "message" and item.get("phase") != "commentary":
                answer = _message_text(item)
        usage = data.get("usage") or {}
        cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
        if data.get("status") == "incomplete":
            raise ProviderIncompleteError(
                answer,
                headers=headers,
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                cached_tokens=cached,
            )
        return ProviderResponse(
            content=answer,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cached_tokens=cached,
            model=str(data.get("model", request.model)),
            headers=headers,
        )

    async def transcribe(self, request: TranscribeRequest) -> TranscribeResponse:
        """`muse-voice-transcribe-1.0`: `POST {base}/asr/transcribe`, multipart `request` (JSON)
        and `audio` (WAV); not over `/responses` (WP0 п.3д, WP10.1)."""
        meta: dict[str, Any] = {
            "model": request.model,
            "audioEncoding": "WAV",
            "mode": request.mode,
        }
        if request.language_bias:
            meta["languageBias"] = request.language_bias
        if request.keywords:
            meta["keywords"] = list(request.keywords)
        try:
            response = await self.client.post(
                f"{self.base_url}/asr/transcribe",
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={
                    "request": (None, orjson.dumps(meta), "application/json"),
                    "audio": ("audio.wav", request.wav, "audio/wav"),
                },
                timeout=request.timeout_sec,
            )
        except httpx2.HTTPError as exc:
            raise ProviderUnavailableError(str(exc)) from exc
        _check_status(response)
        data: dict[str, Any] = response.json()
        return TranscribeResponse(
            content=str(data.get("transcript", "")),
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            model=request.model,
            headers=_rate_limit_headers(response.headers),
            audio_duration_ms=int(data.get("audioDurationMs", 0)),
            turns=tuple(
                TranscriptTurn(
                    turn_id=str(turn.get("turnId", "")),
                    start_ms=int(turn.get("startMs", 0)),
                    end_ms=int(turn.get("endMs", 0)),
                    text=str(turn.get("transcript", "")),
                    speaker=None if turn.get("speaker") is None else str(turn["speaker"]),
                )
                for turn in data.get("turns") or []
            ),
        )

    async def illustrate(self, request: IllustrationRequest) -> ImageResponse:
        """`muse-image-1.0`: `POST {base}/images/generations` with `b64_json` [ПРОВЕРИТЬ на
        стенде] — the documents give no form of this call (D-B-187)."""
        body: dict[str, Any] = {
            "model": request.model,
            "prompt": request.prompt,
            "n": 1,
            "response_format": "b64_json",
        }
        if request.size:
            body["size"] = request.size
        try:
            response = await self.client.post(
                f"{self.base_url}/images/generations",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
                timeout=request.timeout_sec,
            )
        except httpx2.HTTPError as exc:
            raise ProviderUnavailableError(str(exc)) from exc
        _check_status(response)
        data: dict[str, Any] = response.json()
        first = (data.get("data") or [{}])[0]
        encoded, url = first.get("b64_json"), first.get("url")
        if not encoded and not url:
            raise ProviderUnavailableError("image response without b64_json or url")
        usage = data.get("usage") or {}
        return ImageResponse(
            content="",
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cached_tokens=0,
            model=str(data.get("model", request.model)),
            headers=_rate_limit_headers(response.headers),
            image=base64.b64decode(encoded) if encoded else None,
            url=str(url) if url else None,
            mime="image/png",
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
                    event = _data_event(line)
                    if event is None:
                        continue
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
