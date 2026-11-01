"""Registry of event payload schemas of Phase 4 (Т-§2.1, ТЗ Ф4 WP2.1, seam S3).

Every type is a Pydantic model registered by `(type, schema_version)`; a wrong payload is
rejected at write time. Each type also declares whether the projector has a handler for it and
whether the dispatcher has a rule (§6.3 columns «Проектор», «Диспетчер»; `Д-§5.2` п.6, T-EV5),
and the types whose `text` is indexed for full-text search (WP2.3). The catalog itself is
`quack.events.event_types`; the Phase 1–3 catalog (`quack.events.catalog`) stays only for the
old code until ТЗ Ф4 WP1.3.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = 1

# WP2.3 (Т-§3.1, Т-§22.2): types whose `text` field is full-text indexed. The partial GIN index
# of the baseline migration lists the same types.
TEXT_TYPES: frozenset[str] = frozenset(
    {
        "message.sent",
        "answer.submitted",
        "element.responded",
        "turn.committed",
        "canvas.line_committed",
        "voice.transcribed",
        "item.flagged_unclear",
        "chunk.indexed",
        "artifact.updated",
    }
)


class UnknownEventTypeError(LookupError):
    """No payload model for `(type, schema_version)` (Т-§2.4: an error, never a skip)."""

    def __init__(self, event_type: str, schema_version: int) -> None:
        super().__init__(f"unknown event type {event_type!r} v{schema_version}")
        self.event_type = event_type
        self.schema_version = schema_version


class EventPayload(BaseModel):
    """Base of payload models: unknown fields are rejected, payloads are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class EventSpec:
    """§6.3 row: does the projector handle the type, does the dispatcher have a rule for it.

    `False` is the explicit mark «без обработчика» / «без правила» of `Д-§5.2` п.6.
    """

    event_type: str
    schema_version: int
    projector: bool
    dispatcher: bool


REGISTRY: dict[tuple[str, int], type[EventPayload]] = {}
SPECS: dict[tuple[str, int], EventSpec] = {}


def register[P: type[EventPayload]](
    event_type: str,
    *,
    projector: bool,
    dispatcher: bool,
    schema_version: int = SCHEMA_VERSION,
) -> Callable[[P], P]:
    def decorator(model: P) -> P:
        key = (event_type, schema_version)
        if key in REGISTRY:
            raise ValueError(f"event type {event_type!r} v{schema_version} is already registered")
        REGISTRY[key] = model
        SPECS[key] = EventSpec(event_type, schema_version, projector, dispatcher)
        return model

    return decorator


def payload_model(event_type: str, schema_version: int = SCHEMA_VERSION) -> type[EventPayload]:
    _load_catalog()
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


def _load_catalog() -> None:
    """The catalog registers itself on import; import it lazily to avoid an import cycle."""
    import quack.events.event_types  # noqa: F401  (registers the §6.3 payload models)
