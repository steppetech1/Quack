"""Reconcile and replay of deletions (ТЗ Ф4 WP25 п.5; Т-§19.5, Д-§8.5, Д-§8.7).

The journal in R2 is written before a deletion (`delete_domain`), the row of `ops.deletion_log`
after it. A record without its row is a deletion that did not finish: `reconcile_deletions`
(the periodic job `deletions.reconcile`, WP7.5) runs it again. After a restore of Postgres the
database may hold data deleted after the dump: `replay_deletions(since)` (`ops replay-deletions`,
WP9.6) runs every record since then again. Both are repeatable.
"""

from __future__ import annotations

from datetime import datetime

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.deletion.account import apply_account_record
from quack.deletion.domain import apply_domain_record
from quack.deletion.topic import apply_topic_record
from quack.logging import get_logger
from quack.storage.deletions import DeletionJournal, DeletionRecord
from quack.storage.objects import ObjectStore

log = get_logger(__name__)

_LOGGED = (
    "SELECT 1 AS found FROM ops.deletion_log "
    "WHERE learner_id = %(learner_id)s AND deletion_id = %(deletion_id)s"
)


async def _apply(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    store: ObjectStore,
    record: DeletionRecord,
    redis: Redis | None,
) -> bool:
    if record.kind == "domain":
        await apply_domain_record(
            db, neo4j, settings, store, record.learner_id, record, redis=redis
        )
        return True
    if record.kind == "topic":
        return await apply_topic_record(
            db, neo4j, settings, store, record.learner_id, record, redis=redis
        )
    await apply_account_record(db, neo4j, store, record.learner_id, record, redis=redis)
    return True


async def _logged(db: Db, learner_id: object, deletion_id: object) -> bool:
    row = await db.fetch_one(_LOGGED, {"learner_id": learner_id, "deletion_id": deletion_id})
    return row is not None


async def reconcile_deletions(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    journal: DeletionJournal,
    store: ObjectStore,
    since: datetime,
    *,
    redis: Redis | None = None,
) -> int:
    """Records since `since` without a row of `ops.deletion_log` run again; returns how many."""
    done = 0
    for record in await journal.list_since(since):
        if await _logged(db, record.learner_id, record.deletion_id):
            continue
        done += await _apply(db, neo4j, settings, store, record, redis)
    return done


async def replay_deletions(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    journal: DeletionJournal,
    store: ObjectStore,
    since: datetime,
    *,
    redis: Redis | None = None,
) -> int:
    """Every record since `since` again (after a restore); returns how many ran."""
    done = 0
    for record in await journal.list_since(since):
        done += await _apply(db, neo4j, settings, store, record, redis)
    return done
