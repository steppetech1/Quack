"""LLM provider interface and the HTTP implementation.

The exact Meta API surface (Responses API, structured output on Spark/Wonder) is a
"Проверить" item of Т-§11.8; the HTTP provider targets an OpenAI-compatible
`/chat/completions` endpoint with `response_format: json_schema` and is replaceable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from quack.llm.errors import ProviderRateLimitedError, ProviderUnavailableError


@dataclass(frozen=True)
class ProviderRequest:
    model: str
    prompt: str
    json_schema: dict[str, Any]
    schema_name: str
    temperature: float
    timeout_sec: float
    repair_hint: str | None = None


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    model: str


class LLMProvider(Protocol):
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
