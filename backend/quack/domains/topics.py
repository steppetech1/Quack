"""Short topic routes helper: resolve a topic's `domain_id` for the current learner
from `core.events` (ТЗ-П1 §2.2.3, §2.4; Спец §9.2).
"""

from __future__ import annotations

from uuid import UUID

import orjson
from sqlalchemy import text

from quack.api.errors import ApiError
from quack.api.resources import AppResources

_RESOLVE_DOMAIN_SQL = text(
    """
    SELECT domain_id FROM core.events
    WHERE learner_id = :l
      AND domain_id IS NOT NULL
      AND (
        (type = 'structure.proposed' AND payload->'topics' @> CAST(:probe AS JSONB))
        OR (type = 'structure.edited' AND payload->'added_topics' @> CAST(:probe AS JSONB))
      )
    ORDER BY seq
    LIMIT 1
    """
)


async def resolve_topic_domain_id(
    resources: AppResources, learner_id: UUID, topic_id: UUID
) -> UUID:
    """Return the `domain_id` of the learner's topic or raise `NOT_FOUND` (404)."""
    probe = orjson.dumps([{"topic_id": str(topic_id)}]).decode()
    async with resources.session_factory() as session:
        row = (
            await session.execute(_RESOLVE_DOMAIN_SQL, {"l": learner_id, "probe": probe})
        ).first()
    if row is None:
        raise ApiError("NOT_FOUND", f"Topic {topic_id} not found", status_code=404)
    return row[0]
