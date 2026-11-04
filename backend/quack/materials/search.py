"""Full-text search over the learner's events (ТЗ Ф4 WP20.5; Т-§3.1, П-3.7).

«Where did I solve the integral»: the query uses the same expression as the index of WP1.1,
`to_tsvector('english', payload ->> 'text')`, over the same event types. Deleted events are
gone from the log (Т-§2.9), so they are never found.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from quack.db import Db

# The WHERE repeats the predicate and the expression of the partial index `idx_events_fts_en`
# (WP1.1), so it can be used; the type list equals `quack.events.registry.TEXT_TYPES` (tested).
_SEARCH = (
    "SELECT session_id, seq, type, "
    "ts_headline('english', payload ->> 'text', plainto_tsquery('english', %(query)s)) "
    "AS snippet FROM core.events "
    "WHERE learner_id = %(learner_id)s "
    "AND type IN ('message.sent', 'answer.submitted', 'element.responded', 'turn.committed', "
    "'canvas.line_committed', 'voice.transcribed', 'item.flagged_unclear', 'chunk.indexed', "
    "'artifact.updated') "
    "AND to_tsvector('english', payload ->> 'text') @@ plainto_tsquery('english', %(query)s) "
    "AND (%(domain_id)s::uuid IS NULL OR domain_id = %(domain_id)s::uuid) "
    "ORDER BY ts_rank(to_tsvector('english', payload ->> 'text'), "
    "plainto_tsquery('english', %(query)s)) DESC, seq DESC "
    "LIMIT %(limit)s"
)


@dataclass(frozen=True)
class SearchHit:
    session_id: UUID | None
    seq: int
    type: str
    snippet: str


async def search_events(
    db: Db, *, learner_id: UUID, query: str, domain_id: UUID | None = None, limit: int
) -> list[SearchHit]:
    """Events of the learner whose text matches `query`, best first."""
    rows = await db.fetch_all(
        _SEARCH,
        {"learner_id": learner_id, "query": query, "domain_id": domain_id, "limit": limit},
    )
    return [
        SearchHit(session_id=r["session_id"], seq=r["seq"], type=r["type"], snippet=r["snippet"])
        for r in rows
    ]
