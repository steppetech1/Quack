"""Provider of the gateway over the Responses API `POST /responses` (Т-§11.1, Т-§11.5, WP5.7).

One client per process, owned by the gateway (§4.2). No retries of its own (`max_retries =
0`): the gateway retries and writes every attempt to the ledger (WP5.2). The response is
read with its headers (`retry-after`, `x-ratelimit-*`, WP5.3).

Request body (Т-§11.1): `model`, `input` (stable part first), `store: false`, `text.format`
(`json_schema`, `strict: true`, `to_strict_json_schema`), `reasoning: {effort}`,
`max_output_tokens`, `prompt_cache_key`. Never sent: `temperature`, `response_format`,
`prompt_cache_retention`, `previous_response_id`, `tools`, `tool_choice`.

Stage I: signatures only. The SDK the contract names (`AsyncOpenAI`) is not a dependency of
the project yet — see D-B-004.
"""

from __future__ import annotations

import httpx

from quack.llm.provider import ProviderRequest, ProviderResponse


class ResponsesClient:
    """`LLMProvider` over `POST {base_url}/responses`; `http_client` lets tests pass
    `httpx.MockTransport` (`T-L8`)."""

    def __init__(
        self, base_url: str, api_key: str, *, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.http_client = http_client

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        raise NotImplementedError

    async def aclose(self) -> None:
        """Close the HTTP client (called by `LLMGateway.aclose`, §4.2)."""
        raise NotImplementedError
