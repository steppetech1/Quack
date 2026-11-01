"""Postgres reads shared by notes, cheatsheets and search: A10 replay lookup and the
`ArtifactView` columns of core.artifacts (A14)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import orjson
from sqlalchemy import text

from quack.api.errors import ApiError
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


def check_replay(stored: dict[str, Any], request: dict[str, Any], endpoint: str) -> None:
    """T-E2, D-C-027: a used key with another body is 409 `IDEMPOTENCY_CONFLICT`. `request`
    holds the fields of the stored payload the request fixes."""
    if any(stored.get(k) != v for k, v in request.items()):
        raise ApiError(
            "IDEMPOTENCY_CONFLICT",
            f"Idempotency key was used for another {endpoint} request",
            status_code=409,
        )


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


# WP4.2: a topic belongs to the learner when their log created it (`structure.proposed` or
# `structure.edited`); the log is the source of truth and is never behind the projector.
_TOPIC_OF_LEARNER = (
    "SELECT EXISTS (SELECT 1 FROM core.events WHERE learner_id = :l AND ("
    "(type = 'structure.proposed' AND payload->'topics' @> CAST(:probe AS jsonb)) OR "
    "(type = 'structure.edited' AND payload->'added_topics' @> CAST(:probe AS jsonb))))"
)


async def require_topic_of_learner(db: SessionFactory, learner_id: UUID, topic_id: UUID) -> None:
    """`404 NOT_FOUND` when the topic is unknown or another learner's (WP4.2, `Т-§19.1`)."""
    probe = orjson.dumps([{"topic_id": str(topic_id)}]).decode()
    async with db() as session:
        found = (
            await session.execute(text(_TOPIC_OF_LEARNER), {"l": learner_id, "probe": probe})
        ).scalar_one()
    if not found:
        raise ApiError("NOT_FOUND", "topic not found", status_code=404)
