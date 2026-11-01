"""The event log of Phase 4: envelopes and `append_events` on `Db` (Т-§2.1, Т-§2.3; ТЗ Ф4 WP1.2).

`append_events` is the only way to write an event. One transaction: lock the learner row,
return already written events by idempotency key (same payload hash) or fail on a conflict,
give new events seq + 1 … seq + n, record new `(type, schema_version)` pairs in
`ops.event_types_seen`, move the counter. After the outermost commit the `learner:{id}` signal
goes to Redis (best effort: consumers also poll Postgres, Т-§1.7). It runs inside the caller's
transaction when there is one (`Db.transaction()` nests).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.asyncio import Redis

from quack.db.db import Db
from quack.db.redis import learner_signal_channel
from quack.events.actor import validate_actor
from quack.events.errors import IdempotencyConflictError
from quack.events.hashing import payload_hash
from quack.events.registry import SCHEMA_VERSION, EventPayload, validate_payload
from quack.logging import get_logger

log = get_logger(__name__)

# Strong references to scheduled signals (asyncio keeps only weak ones).
_pending_signals: set[asyncio.Task[None]] = set()


class LearnerNotFoundError(LookupError):
    """append_events for a learner without a `core.learners` row (the row is created at login)."""

    def __init__(self, learner_id: UUID) -> None:
        super().__init__(f"learner {learner_id} has no core.learners row")
        self.learner_id = learner_id


class NewEvent(BaseModel):
    """What a writer appends (Т-§2.1). `seq`, `payload_hash` and the server time are set here."""

    model_config = ConfigDict(frozen=True)

    type: str
    payload: dict[str, Any] | EventPayload
    actor: str
    idempotency_key: str = Field(min_length=1)
    schema_version: int = SCHEMA_VERSION
    domain_id: UUID | None = None
    session_id: UUID | None = None
    causation_seq: int | None = None
    correlation_id: UUID | None = None
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    # Server time of the action; None = now(). Device time goes into the payload (Т-§2.1).
    occurred_at: datetime | None = None

    @field_validator("actor")
    @classmethod
    def _actor(cls, value: str) -> str:
        return validate_actor(value)


class StoredEvent(BaseModel):
    """A row of `core.events` (Т-§2.1)."""

    model_config = ConfigDict(frozen=True)

    learner_id: UUID
    seq: int
    id: int
    type: str
    schema_version: int
    actor: str
    domain_id: UUID | None
    session_id: UUID | None
    causation_seq: int | None
    correlation_id: UUID | None
    idempotency_key: str
    payload_hash: str
    occurred_at: datetime
    payload: dict[str, Any]
    source_refs: list[dict[str, Any]]

    def parsed(self) -> EventPayload:
        return validate_payload(self.type, self.payload, self.schema_version)


class _Seq(BaseModel):
    seq: int


_LOCK_LEARNER = "SELECT seq FROM core.learners WHERE learner_id = %(learner_id)s FOR UPDATE"

_SELECT_BY_KEYS = (
    "SELECT learner_id, seq, id, type, schema_version, actor, domain_id, session_id, "
    "causation_seq, correlation_id, idempotency_key, payload_hash, occurred_at, payload, "
    "source_refs FROM core.events "
    "WHERE learner_id = %(learner_id)s AND idempotency_key = ANY(%(keys)s)"
)

_INSERT_EVENT = (
    "INSERT INTO core.events (learner_id, seq, type, schema_version, actor, domain_id, "
    "session_id, causation_seq, correlation_id, idempotency_key, payload_hash, occurred_at, "
    "payload, source_refs) VALUES (%(learner_id)s, %(seq)s, %(type)s, %(schema_version)s, "
    "%(actor)s, %(domain_id)s, %(session_id)s, %(causation_seq)s, %(correlation_id)s, "
    "%(idempotency_key)s, %(payload_hash)s, %(occurred_at)s, %(payload)s, %(source_refs)s) "
    "RETURNING learner_id, seq, id, type, schema_version, actor, domain_id, session_id, "
    "causation_seq, correlation_id, idempotency_key, payload_hash, occurred_at, payload, "
    "source_refs"
)

_TYPE_SEEN = (
    "INSERT INTO ops.event_types_seen (type, schema_version) "
    "VALUES (%(type)s, %(schema_version)s) ON CONFLICT DO NOTHING"
)

_MOVE_COUNTER = "UPDATE core.learners SET seq = %(seq)s WHERE learner_id = %(learner_id)s"


async def _publish_signal(redis: Redis, learner_id: UUID, seq: int) -> None:
    try:
        await redis.publish(learner_signal_channel(learner_id), str(seq))
    except Exception:
        log.warning("learner_signal_publish_failed", learner_id=str(learner_id), seq=seq)


def _schedule_signal(redis: Redis, learner_id: UUID, seq: int) -> None:
    task = asyncio.get_running_loop().create_task(_publish_signal(redis, learner_id, seq))
    _pending_signals.add(task)
    task.add_done_callback(_pending_signals.discard)


async def append_events(
    db: Db,
    learner_id: UUID,
    events: Sequence[NewEvent],
    *,
    redis: Redis | None = None,
) -> list[StoredEvent]:
    """Write `events` of one learner (Т-§2.3); returns them in input order."""
    if not events:
        return []
    prepared: list[tuple[NewEvent, dict[str, Any], str]] = []
    for new in events:
        data = validate_payload(new.type, new.payload, new.schema_version).model_dump(mode="json")
        prepared.append((new, data, payload_hash(data)))
    keys = [new.idempotency_key for new in events]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate idempotency_key inside one append_events call")

    result: list[StoredEvent] = []
    async with db.transaction():
        locked = await db.fetch_one(_LOCK_LEARNER, {"learner_id": learner_id}, row=_Seq)
        if locked is None:
            raise LearnerNotFoundError(learner_id)
        seq = locked.seq
        existing = {
            e.idempotency_key: e
            for e in await db.fetch_all(
                _SELECT_BY_KEYS, {"learner_id": learner_id, "keys": keys}, row=StoredEvent
            )
        }
        now = datetime.now(UTC)
        seen: set[tuple[str, int]] = set()
        for new, data, digest in prepared:
            found = existing.get(new.idempotency_key)
            if found is not None:
                if found.payload_hash != digest:
                    raise IdempotencyConflictError(new.idempotency_key, found.payload_hash, digest)
                result.append(found)
                continue
            seq += 1
            stored = await db.fetch_one(
                _INSERT_EVENT,
                {
                    "learner_id": learner_id,
                    "seq": seq,
                    "type": new.type,
                    "schema_version": new.schema_version,
                    "actor": new.actor,
                    "domain_id": new.domain_id,
                    "session_id": new.session_id,
                    "causation_seq": new.causation_seq,
                    "correlation_id": new.correlation_id,
                    "idempotency_key": new.idempotency_key,
                    "payload_hash": digest,
                    "occurred_at": new.occurred_at or now,
                    "payload": Jsonb(data),
                    "source_refs": Jsonb(new.source_refs),
                },
                row=StoredEvent,
            )
            assert stored is not None  # INSERT … RETURNING always yields the row
            result.append(stored)
            seen.add((new.type, new.schema_version))
        if seq != locked.seq:
            for event_type, version in sorted(seen):
                await db.execute(_TYPE_SEEN, {"type": event_type, "schema_version": version})
            await db.execute(_MOVE_COUNTER, {"seq": seq, "learner_id": learner_id})
            if redis is not None:
                last = seq
                db.on_commit(lambda: _schedule_signal(redis, learner_id, last))
    return result
