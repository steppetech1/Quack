"""T-DB6 (ТЗ Ф4 §8.2, WP1.1 + WP20.5): the full-text search uses the partial GIN index of the
baseline (`idx_events_fts_en`) — the query of `quack.materials.search` repeats its expression and
predicate."""

from __future__ import annotations

from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.materials.search import _SEARCH  # the query of C under test (WP20.5)
from tests.db.fixtures import create_learner


async def test_fts_index_used(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    words = ["limit", "matrix", "series", "vector", "integral"]
    events = [
        NewEvent(type="message.sent", payload={"text": f"{words[n % 5]} {n}"}, actor="learner",
                 idempotency_key=f"m{n}")
        for n in range(600)
    ]  # fmt: skip
    events.append(
        NewEvent(type="message.sent", payload={"text": "the derivative"}, actor="learner",
                 idempotency_key="rare")
    )  # fmt: skip
    await append_events(pg_db, learner, events)
    await pg_db.execute("ANALYZE core.events")
    params = {"learner_id": learner, "query": "derivative", "domain_id": None, "limit": 10}
    plan = await pg_db.fetch_all("EXPLAIN " + _SEARCH, params)
    text = "\n".join(str(next(iter(r.values()))) for r in plan)
    assert "idx_events_fts_en" in text, text
