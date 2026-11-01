"""LLM provider interface of the gateway (seam S12); the implementation is the Responses API
adapter `quack.llm.responses_client.ResponsesClient` (WP5.7, Т-§11.1, Т-§11.5)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

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
    """WP5.7 (Т-§11.5): the Phase 1 provider over `/chat/completions` is replaced by the
    Responses API adapter; this name stays for its call sites in `quack/runtime`, `quack/api`,
    `quack/domains`, `quack/artifacts` and delegates to `ResponsesClient`."""

    def __init__(self, base_url: str, api_key: str) -> None:
        # Imported here: the adapter module imports the types above (no module-level cycle).
        from quack.llm.responses_client import ResponsesClient

        self._impl = ResponsesClient(base_url, api_key)
        self.client = self._impl.client

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        return await self._impl.complete_structured(request)

    async def aclose(self) -> None:
        await self._impl.aclose()
