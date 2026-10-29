"""Event envelopes: what callers append (`NewEvent`) and what the log returns (`StoredEvent`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quack.events.actor import validate_actor
from quack.events.catalog import SCHEMA_VERSION, validate_payload
from quack.events.payloads import Payload


class NewEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    payload: dict[str, Any] | Payload
    actor: str
    idempotency_key: str = Field(min_length=1)
    schema_version: int = SCHEMA_VERSION
    occurred_at: datetime | None = None
    session_id: UUID | None = None
    turn_id: UUID | None = None
    domain_id: UUID | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    event_id: UUID | None = None

    @field_validator("actor")
    @classmethod
    def _actor(cls, value: str) -> str:
        return validate_actor(value)


class StoredEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    learner_id: UUID
    seq: int
    event_id: UUID
    type: str
    schema_version: int
    actor: str
    session_id: UUID | None
    turn_id: UUID | None
    domain_id: UUID | None
    correlation_id: UUID | None
    causation_id: UUID | None
    idempotency_key: str
    payload_hash: str
    payload: dict[str, Any]
    occurred_at: datetime

    def parsed(self) -> Payload:
        return validate_payload(self.type, self.payload, self.schema_version)
