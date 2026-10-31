"""LLM provider test doubles (ТЗ Фазы 3 §8.1).

- `FakeLLMProvider`: queue of responses in call order (moved from `tests/domains/conftest.py`).
- `ScriptedLLMProvider`: response chosen by `schema_name`, not by order, because background
  jobs in e2e tests run concurrently. Stage I skeleton: its failure kinds follow the provider
  interface of WP5.7 (seam S12, stream B) and are filled in when that interface lands.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import orjson
from pydantic import BaseModel

from quack.llm.provider import ProviderRequest, ProviderResponse

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


type Responder = Callable[[ProviderRequest], QueuedLLMItem]


@dataclass
class ScriptedFailure:
    """A failure injected for calls matching `model` and/or `schema_name` (`None` = any).

    `kind` values (§8.1): `rate_limited` (with `retry_after`: seconds, HTTP date or `None`),
    `unavailable`, `bad_request`, `incomplete`, `invalid_json`, `commentary_only`,
    `ratelimit_headers` (with `remaining_share`). `times` = how many matching calls fail.
    """

    kind: str
    model: str | None = None
    schema_name: str | None = None
    times: int = 1
    retry_after: str | None = None
    remaining_share: float | None = None


@dataclass
class ScriptedLLMProvider:
    """`LLMProvider` answering by `request.schema_name` with default responders for all
    output schemas, a call journal and failure injection by model or schema (§8.1)."""

    responders: dict[str, Responder] = field(default_factory=dict)
    failures: list[ScriptedFailure] = field(default_factory=list)
    calls: list[ProviderRequest] = field(default_factory=list)

    def respond(self, schema_name: str, responder: Responder) -> None:
        """Override the default responder of one schema."""
        raise NotImplementedError

    def fail(self, failure: ScriptedFailure) -> None:
        """Inject a failure for matching calls."""
        raise NotImplementedError

    def calls_for(
        self, *, model: str | None = None, schema_name: str | None = None
    ) -> list[ProviderRequest]:
        """Journal of calls filtered by model and/or schema."""
        raise NotImplementedError

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        raise NotImplementedError
