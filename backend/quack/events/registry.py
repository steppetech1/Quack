"""Registry of event payload schemas of Phase 4 (Т-§2.1, ТЗ Ф4 WP2.1, seam S3).

Every type is a Pydantic model registered by `(type, schema_version)`; a wrong payload is
rejected at write time. The catalog itself (§6.3) registers its models here; the Phase 1–3
catalog (`quack.events.catalog`) stays only for the old code until ТЗ Ф4 WP1.3.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = 1


class UnknownEventTypeError(LookupError):
    """No payload model for `(type, schema_version)` (Т-§2.4: an error, never a skip)."""

    def __init__(self, event_type: str, schema_version: int) -> None:
        super().__init__(f"unknown event type {event_type!r} v{schema_version}")
        self.event_type = event_type
        self.schema_version = schema_version


class EventPayload(BaseModel):
    """Base of payload models: unknown fields are rejected, payloads are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


REGISTRY: dict[tuple[str, int], type[EventPayload]] = {}


def register[P: type[EventPayload]](
    event_type: str, schema_version: int = SCHEMA_VERSION
) -> Callable[[P], P]:
    def decorator(model: P) -> P:
        key = (event_type, schema_version)
        if key in REGISTRY:
            raise ValueError(f"event type {event_type!r} v{schema_version} is already registered")
        REGISTRY[key] = model
        return model

    return decorator


def payload_model(event_type: str, schema_version: int = SCHEMA_VERSION) -> type[EventPayload]:
    try:
        return REGISTRY[(event_type, schema_version)]
    except KeyError:
        raise UnknownEventTypeError(event_type, schema_version) from None


def validate_payload(
    event_type: str, payload: dict[str, Any] | EventPayload, schema_version: int = SCHEMA_VERSION
) -> EventPayload:
    model = payload_model(event_type, schema_version)
    if isinstance(payload, model):
        return payload
    if isinstance(payload, BaseModel):
        raise TypeError(f"payload of {event_type!r} must be {model.__name__}")
    return model.model_validate(payload)
