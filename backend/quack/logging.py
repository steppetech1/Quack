"""structlog JSON logging with ContextVars (request_id, trace_id, learner_id, session_id, seq)."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from typing import Any

import structlog

CONTEXT_KEYS = ("request_id", "trace_id", "learner_id", "session_id", "seq")
_MASK = "***"
_secret_values: list[str] = []


def _mask_secrets(
    _: Any, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Mask values of known secret variables (Д-§7)."""
    if not _secret_values:
        return event_dict
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            for secret in _secret_values:
                if secret in value:
                    value = value.replace(secret, _MASK)
            event_dict[key] = value
    return event_dict


def configure_logging(level: str = "INFO", secret_values: list[str] | None = None) -> None:
    _secret_values[:] = list(secret_values or [])
    log_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _mask_secrets,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    """Bind context variables for the duration of the block."""
    unknown = set(values) - set(CONTEXT_KEYS)
    if unknown:
        raise ValueError(f"unknown log context keys: {sorted(unknown)}")
    bound = {k: str(v) for k, v in values.items() if v is not None}
    tokens = structlog.contextvars.bind_contextvars(**bound)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)
