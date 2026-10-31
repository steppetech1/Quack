"""Errors of the LLM gateway; callers switch to deterministic fallbacks on them (Т-§17.1).

Phase 3 (ТЗ §6.6): reasons `bad_request`, `circuit_open` on `LLMUnavailableError`,
`incomplete` on `LLMSchemaError`; `LLMRateLimitedError` (seam S3); provider errors of the
Responses API transport (seam S12, WP5.7).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

type UnavailableReason = Literal[
    "unavailable", "timeout", "rate_limited", "bad_request", "circuit_open"
]
type SchemaErrorReason = Literal["schema", "incomplete", "label"]


class LLMError(Exception):
    """Base error of the LLM gateway."""


class LLMBudgetExceededError(LLMError):
    def __init__(self, scope: str, used: int, limit: int) -> None:
        super().__init__(f"{scope} token budget exceeded: {used} >= {limit}")
        self.scope = scope
        self.used = used
        self.limit = limit


class LLMUnavailableError(LLMError):
    """Timeout, 5xx, rate limit, bad request or open circuit; `reason` tells which (§6.6)."""

    def __init__(self, message: str = "", *, reason: UnavailableReason = "unavailable") -> None:
        super().__init__(message)
        self.reason: UnavailableReason = reason


class LLMRateLimitedError(LLMUnavailableError):
    """429 that the gateway does not wait out (WP5.2): hot-path roles, last retry, or a
    `retry-after` above `llm_rate_limit_max_wait_s`. Seam S3."""

    def __init__(self, message: str = "", *, retry_after_sec: float | None = None) -> None:
        super().__init__(message, reason="rate_limited")
        self.retry_after_sec = retry_after_sec


class LLMSchemaError(LLMError):
    """The response did not match the schema after the single retry (Т-§11.1); `reason`
    `incomplete` — cut by the output limit twice (WP5.2), `label` — unknown label (WP14.1)."""

    def __init__(self, message: str = "", *, reason: SchemaErrorReason = "schema") -> None:
        super().__init__(message)
        self.reason: SchemaErrorReason = reason


class ProviderUnavailableError(Exception):
    """Raised by providers on timeout / 5xx / connection errors."""


class ProviderRateLimitedError(ProviderUnavailableError):
    """429 of the provider with the raw `retry-after` and `x-ratelimit-*` headers (WP5.3)."""

    def __init__(
        self,
        retry_after_sec: float | None,
        *,
        headers: Mapping[str, str] | None = None,
        body: str = "",
    ) -> None:
        super().__init__("rate limited")
        self.retry_after_sec = retry_after_sec
        self.headers: dict[str, str] = dict(headers or {})
        self.body = body


class ProviderBadRequestError(Exception):
    """400 of the provider: an error of our request, never retried (WP5.2)."""

    def __init__(self, body: str) -> None:
        super().__init__("bad request")
        self.body = body


class ProviderIncompleteError(Exception):
    """`status: incomplete` / `finish_reason: length`: never parsed (Т-§11.1, WP5.2)."""

    def __init__(self, partial_text: str, *, headers: Mapping[str, str] | None = None) -> None:
        super().__init__("incomplete response")
        self.partial_text = partial_text
        self.headers: dict[str, str] = dict(headers or {})
