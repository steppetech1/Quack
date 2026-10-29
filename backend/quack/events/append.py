"""append_events: the only way to write events (spec §8.1, Т-§2.3)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import orjson
from redis.asyncio import Redis
from sqlalchemy import event as sa_event
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from quack.db.redis import PROJECTOR_WAKEUP_CHANNEL
from quack.events.catalog import validate_payload
from quack.events.errors import IdempotencyConflictError
from quack.events.hashing import payload_hash
from quack.events.models import NewEvent, StoredEvent
from quack.events.projections import apply_relational_projection
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.logging import get_logger

log = get_logger(__name__)

# Strong references to scheduled wakeup publications (asyncio keeps only weak ones).
_pending_publications: set[asyncio.Task[None]] = set()

_INSERT_EVENT = text(
    "INSERT INTO core.events (learner_id, seq, event_id, type, schema_version, actor, "
    "session_id, turn_id, domain_id, correlation_id, causation_id, idempotency_key, "
    "payload_hash, payload, occurred_at) VALUES (:learner_id, :seq, :event_id, :type, "
    ":schema_version, :actor, :session_id, :turn_id, :domain_id, :correlation_id, "
    ":causation_id, :idempotency_key, :payload_hash, CAST(:payload AS JSONB), :occurred_at) "
    f"RETURNING {EVENT_COLUMNS}"
)


def _uuid_field(data: dict[str, Any], key: str) -> UUID | None:
    value = data.get(key)
    return UUID(value) if isinstance(value, str) else None


async def send_wakeup(redis: Redis, learner_id: UUID, seq: int) -> None:
    """Best effort: the projector also polls Postgres (Т-§17.3)."""
    try:
        message = orjson.dumps({"learner_id": str(learner_id), "seq": seq}).decode()
        await redis.publish(PROJECTOR_WAKEUP_CHANNEL, message)
    except Exception:
        log.warning("projector_wakeup_publish_failed", learner_id=str(learner_id), seq=seq)


def _schedule_wakeup_after_commit(
    session: AsyncSession, redis: Redis, learner_id: UUID, seq: int
) -> None:
    loop = asyncio.get_running_loop()
    sync_session = session.sync_session
    state = {"cancelled": False}

    def after_commit(_: Session) -> None:
        if state["cancelled"]:
            return
        task = loop.create_task(send_wakeup(redis, learner_id, seq))
        _pending_publications.add(task)
        task.add_done_callback(_pending_publications.discard)

    def after_rollback(_: Session, previous_transaction: Any) -> None:
        if previous_transaction.parent is None:
            state["cancelled"] = True

    # Listeners are not removed while dispatching; the flag makes them inert instead.
    sa_event.listen(sync_session, "after_commit", after_commit, once=True)
    sa_event.listen(sync_session, "after_soft_rollback", after_rollback)


async def append_events(
    session: AsyncSession,
    learner_id: UUID,
    events: Sequence[NewEvent],
    publish_wakeup: bool = True,
    *,
    redis: Redis | None = None,
) -> list[StoredEvent]:
    """Validate, deduplicate by idempotency key, assign gapless seq and insert.

    Runs inside the caller's transaction (transactional outbox, spec §7.2); the wakeup to
    `projector:wakeup` is published only after that transaction commits.
    """
    if not events:
        return []
    # 1. Validate payloads and compute canonical hashes.
    prepared: list[tuple[NewEvent, dict[str, Any], str]] = []
    for new in events:
        model = validate_payload(new.type, new.payload, new.schema_version)
        data = model.model_dump(mode="json")
        prepared.append((new, data, payload_hash(data)))
    keys = [new.idempotency_key for new, _, _ in prepared]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate idempotency_key inside one append_events call")

    # 2. Ensure and lock the learner counter row: writes of one learner are serialized.
    await session.execute(
        text(
            "INSERT INTO core.learner_seq (learner_id, last_seq, updated_at) "
            "VALUES (:l, 0, now()) ON CONFLICT (learner_id) DO NOTHING"
        ),
        {"l": learner_id},
    )
    last_seq = int(
        (
            await session.execute(
                text("SELECT last_seq FROM core.learner_seq WHERE learner_id = :l FOR UPDATE"),
                {"l": learner_id},
            )
        ).scalar_one()
    )

    # 3. Idempotency: existing keys with the same hash are returned, others conflict.
    existing_rows = await session.execute(
        text(
            f"SELECT {EVENT_COLUMNS} FROM core.events "
            "WHERE learner_id = :l AND idempotency_key = ANY(:keys)"
        ),
        {"l": learner_id, "keys": keys},
    )
    existing = {e.idempotency_key: e for e in (row_to_event(r) for r in existing_rows)}

    # 4. Assign seq = last_seq + 1 ... and insert.
    result: list[StoredEvent] = []
    now = datetime.now(UTC)
    for new, data, digest in prepared:
        found = existing.get(new.idempotency_key)
        if found is not None:
            if found.payload_hash != digest:
                raise IdempotencyConflictError(new.idempotency_key, found.payload_hash, digest)
            result.append(found)
            continue
        last_seq += 1
        row = (
            await session.execute(
                _INSERT_EVENT,
                {
                    "learner_id": learner_id,
                    "seq": last_seq,
                    "event_id": new.event_id or uuid4(),
                    "type": new.type,
                    "schema_version": new.schema_version,
                    "actor": new.actor,
                    "session_id": new.session_id or _uuid_field(data, "session_id"),
                    "turn_id": new.turn_id or _uuid_field(data, "turn_id"),
                    "domain_id": new.domain_id or _uuid_field(data, "domain_id"),
                    "correlation_id": new.correlation_id,
                    "causation_id": new.causation_id,
                    "idempotency_key": new.idempotency_key,
                    "payload_hash": digest,
                    "payload": orjson.dumps(data).decode(),
                    "occurred_at": new.occurred_at or now,
                },
            )
        ).one()
        stored = row_to_event(row)
        await session.execute(
            text(
                "INSERT INTO ops.event_types_seen (type, schema_version) VALUES (:t, :v) "
                "ON CONFLICT DO NOTHING"
            ),
            {"t": stored.type, "v": stored.schema_version},
        )
        # 5. Relational projections in the same transaction.
        await apply_relational_projection(session, stored)
        result.append(stored)

    await session.execute(
        text("UPDATE core.learner_seq SET last_seq = :s, updated_at = now() WHERE learner_id = :l"),
        {"s": last_seq, "l": learner_id},
    )
    # 6. Wakeup after commit.
    if publish_wakeup and redis is not None and any(e.seq > 0 for e in result):
        _schedule_wakeup_after_commit(session, redis, learner_id, max(e.seq for e in result))
    return result
