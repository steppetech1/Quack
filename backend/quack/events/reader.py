"""Reading the event log."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from quack.events.models import StoredEvent

EVENT_COLUMNS = (
    "learner_id, seq, event_id, type, schema_version, actor, session_id, turn_id, domain_id, "
    "correlation_id, causation_id, idempotency_key, payload_hash, payload, occurred_at"
)


def row_to_event(row: Any) -> StoredEvent:
    return StoredEvent.model_validate(dict(row._mapping))


async def read_learner_events(
    session: AsyncSession, learner_id: UUID, *, after_seq: int = 0, limit: int | None = None
) -> list[StoredEvent]:
    sql = (
        f"SELECT {EVENT_COLUMNS} FROM core.events "
        "WHERE learner_id = :learner_id AND seq > :after_seq ORDER BY seq"
    )
    params: dict[str, Any] = {"learner_id": learner_id, "after_seq": after_seq}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit
    result = await session.execute(text(sql), params)
    return [row_to_event(row) for row in result]


async def read_session_tail(
    session: AsyncSession,
    learner_id: UUID,
    session_id: UUID,
    *,
    after_seq: int,
    types: frozenset[str] | None = None,
) -> list[StoredEvent]:
    sql = (
        f"SELECT {EVENT_COLUMNS} FROM core.events WHERE learner_id = :learner_id "
        "AND session_id = :session_id AND seq > :after_seq"
    )
    params: dict[str, Any] = {
        "learner_id": learner_id,
        "session_id": session_id,
        "after_seq": after_seq,
    }
    if types is not None:
        sql += " AND type = ANY(:types)"
        params["types"] = sorted(types)
    sql += " ORDER BY seq"
    result = await session.execute(text(sql), params)
    return [row_to_event(row) for row in result]


async def last_seq(session: AsyncSession, learner_id: UUID) -> int:
    result = await session.execute(
        text("SELECT last_seq FROM core.learner_seq WHERE learner_id = :learner_id"),
        {"learner_id": learner_id},
    )
    value = result.scalar_one_or_none()
    return int(value) if value is not None else 0
