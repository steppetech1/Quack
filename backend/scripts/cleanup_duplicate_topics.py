"""One-off cleanup of duplicate topics across learners/domains (writes `topic.merged` /
`topic.renamed` events via `cleanup_duplicate_topics`).

Usage (from `backend/`):
    uv run python scripts/cleanup_duplicate_topics.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quack.agents.hygiene import cleanup_duplicate_topics
from quack.config import Settings
from quack.db import open_db
from quack.db.neo4j import create_driver


async def main() -> None:
    settings = Settings()
    db = await open_db(settings)
    neo4j = create_driver(settings)
    try:
        rows = await db.fetch_all(
            "SELECT DISTINCT learner_id, domain_id FROM core.events "
            "WHERE type = 'domain.created' AND domain_id IS NOT NULL ORDER BY learner_id, domain_id"
        )
        total = 0
        for row in rows:
            learner_id = UUID(str(row["learner_id"]))
            domain_id = UUID(str(row["domain_id"]))
            merges = await cleanup_duplicate_topics(
                db, neo4j, learner_id=learner_id, domain_id=domain_id
            )
            for from_id, into_id in merges:
                print(f"merged learner={learner_id} domain={domain_id}: {from_id} -> {into_id}")
            total += len(merges)
        print(f"done: {total} topic merge(s) written")
    finally:
        await neo4j.close()
        await db.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(main())
