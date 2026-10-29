"""Errors of the LLM gateway; callers switch to deterministic fallbacks on them (Т-§17.1)."""

from __future__ import annotations


class LLMError(Exception):
    """Base error of the LLM gateway."""


class LLMBudgetExceededError(LLMError):
    def __init__(self, scope: str, used: int, limit: int) -> None:
        super().__init__(f"{scope} token budget exceeded: {used} >= {limit}")
        self.scope = scope
        self.used = used
        self.limit = limit


class LLMUnavailableError(LLMError):
    """Timeout, 5xx or rate limit of the provider."""


class LLMSchemaError(LLMError):
    """The response did not match the schema after the single retry (Т-§11.1)."""


class ProviderUnavailableError(Exception):
    """Raised by providers on timeout / 5xx / connection errors."""


class ProviderRateLimitedError(ProviderUnavailableError):
    def __init__(self, retry_after_sec: float | None) -> None:
        super().__init__("rate limited")
        self.retry_after_sec = retry_after_sec
