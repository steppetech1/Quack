"""Postgres reads shared by notes, cheatsheets and search: A10 replay lookup and the
`ArtifactView` columns of core.artifacts (A14)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

from quack.db.postgres import SessionFactory
from quack.events import StoredEvent
from quack.events.reader import EVENT_COLUMNS, row_to_event

# A14: the fields of an `ArtifactView`, selected from `core.artifacts`.
ARTIFACT_VIEW_COLUMNS = (
    "id AS artifact_id, kind, domain_id, topic_id, session_id, title, content_md, version, "
    "created_at, updated_at"
)


def event_key(idempotency_key: str, event_type: str) -> str:
    """A10: the idempotency key of the event written by one request."""
    return f"{idempotency_key}:{event_type}"


async def fetch_rows(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def stored_event(db: SessionFactory, learner_id: UUID, key: str) -> StoredEvent | None:
    """A10: the event already written with this idempotency key, if any."""
    async with db() as session:
        row = (
            await session.execute(
                text(
                    f"SELECT {EVENT_COLUMNS} FROM core.events "
                    "WHERE learner_id = :l AND idempotency_key = :k"
                ),
                {"l": learner_id, "k": key},
            )
        ).first()
    return None if row is None else row_to_event(row)
