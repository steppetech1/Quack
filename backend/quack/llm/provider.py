"""LLM provider interface and the HTTP implementation.

The exact Meta API surface (Responses API, structured output on Spark/Wonder) is a
"Проверить" item of Т-§11.8; the HTTP provider targets an OpenAI-compatible
`/chat/completions` endpoint with `response_format: json_schema` and is replaceable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from quack.llm.errors import ProviderRateLimitedError, ProviderUnavailableError

type ReasoningEffort = Literal["low", "high"]


@dataclass(frozen=True)
class ProviderRequest:
    """One call of a role (seam S12). Fields after `repair_hint` are the Responses API
    parameters of WP5.7 (Т-§11.1); `temperature` is sent only by the legacy provider and
    goes away with it (WP5.7)."""

    model: str
    prompt: str
    json_schema: dict[str, Any]
    schema_name: str
    temperature: float
    timeout_sec: float
    repair_hint: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    max_output_tokens: int | None = None
    prompt_cache_key: str | None = None


@dataclass(frozen=True)
class ProviderResponse:
    """Answer of the provider (seam S12): the parsed answer text, the raw `output` items as
    they came (reasoning items are passed on unchanged in the next phase, Т-§11.5), usage
    by kind and the rate-limit headers of the response (WP5.3)."""

    content: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    model: str
    reasoning_tokens: int = 0
    output_items: tuple[dict[str, Any], ...] = ()
    incomplete: bool = False
    headers: Mapping[str, str] = field(default_factory=dict)


# Headers the gateway reads from every response, including 429 (Т-§11.3, WP5.3).
RATE_LIMIT_HEADERS: tuple[str, ...] = (
    "retry-after",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
)


class LLMProvider(Protocol):
    """Provider interface of the gateway (seam S12).

    Errors: `ProviderRateLimitedError` (429, `retry_after_sec` + headers),
    `ProviderBadRequestError` (400, body), `ProviderUnavailableError` (5xx, timeout,
    connection), `ProviderIncompleteError` (`status: incomplete`, partial text).
    """

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse: ...


class HttpLLMProvider:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = client or httpx.AsyncClient()

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        messages: list[dict[str, str]] = [{"role": "user", "content": request.prompt}]
        if request.repair_hint:
            messages.append(
                {
                    "role": "user",
                    "content": "The previous answer did not match the JSON schema: "
                    f"{request.repair_hint}. Answer again with valid JSON only.",
                }
            )
        body = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.json_schema,
                    "strict": True,
                },
            },
        }
        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=request.timeout_sec,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(str(exc)) from exc
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            try:
                seconds = float(retry_after) if retry_after is not None else None
            except ValueError:
                seconds = None
            raise ProviderRateLimitedError(seconds)
        if response.status_code >= 400:
            raise ProviderUnavailableError(f"provider returned HTTP {response.status_code}")
        data = response.json()
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        return ProviderResponse(
            content=data["choices"][0]["message"]["content"] or "",
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            cached_tokens=int(details.get("cached_tokens", 0)),
            model=str(data.get("model", request.model)),
        )
