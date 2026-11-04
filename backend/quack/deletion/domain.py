"""Physical deletion of a domain (ТЗ Ф4 WP25 п.2–4; Т-§19.5, Т-§20.7, Д-§8.5–8.6).

Order: the record in the R2 journal first — it fails, nothing is deleted (`SERVICE_UNAVAILABLE`);
then under the learner's advisory lock the rows of the domain within the bounds of `Т-§19.5`
(every event with its `domain_id`, its sessions whole with all their events, materials, canvases,
tasks, artifacts) and their objects; then the row of `ops.deletion_log` with the same id; then
the learner is rebuilt from what is left (WP3.5), so the graph forgets the domain.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.db import LockKeyspace
from quack.db.neo4j import literal, read_session
from quack.graph.projection.rebuild import rebuild_learner
from quack.storage.deletions import DeletionJournal, DeletionRecord
from quack.storage.objects import ObjectStore

# Д-§8.6: the deletion screen names the term of the copies with a margin — 15 days
# (`backup_retention_days` 14 plus a day of asynchronous deletion).
COPIES_DAYS = 15
_REBUILD_TRIES = 5

_OWN_DOMAIN = "MATCH (d:Domain {id: $id}) WHERE d.learnerId = $learner RETURN count(d) AS n"
_SESSIONS = (
    "SELECT session_id FROM content.sessions "
    "WHERE learner_id = %(learner_id)s AND domain_id = %(domain_id)s"
)
_MATERIALS = (
    "SELECT material_id, object_key FROM content.materials WHERE learner_id = %(learner_id)s "
    "AND (domain_id = %(domain_id)s OR session_id = ANY(%(sessions)s))"
)
_PAGE_KEYS = (
    "SELECT image_key FROM content.material_pages WHERE learner_id = %(learner_id)s "
    "AND material_id = ANY(%(materials)s) AND image_key IS NOT NULL"
)
_EVENT_KEYS = (
    "SELECT payload->>'object_key' AS key FROM core.events WHERE learner_id = %(learner_id)s "
    "AND (domain_id = %(domain_id)s OR session_id = ANY(%(sessions)s)) AND payload ? 'object_key'"
)
_ARTIFACTS = (
    "SELECT artifact_id FROM content.artifacts "
    "WHERE learner_id = %(learner_id)s AND domain_id = %(domain_id)s"
)
_DELETES = (
    "DELETE FROM core.events WHERE learner_id = %(learner_id)s "
    "AND (domain_id = %(domain_id)s OR session_id = ANY(%(sessions)s))",
    "DELETE FROM content.material_pages WHERE learner_id = %(learner_id)s "
    "AND material_id = ANY(%(materials)s)",
    "DELETE FROM content.materials WHERE learner_id = %(learner_id)s "
    "AND material_id = ANY(%(materials)s)",
    "DELETE FROM content.canvas_boards WHERE learner_id = %(learner_id)s "
    "AND session_id = ANY(%(sessions)s)",
    "DELETE FROM content.tasks WHERE learner_id = %(learner_id)s AND domain_id = %(domain_id)s",
    "DELETE FROM content.artifact_versions WHERE learner_id = %(learner_id)s "
    "AND artifact_id = ANY(%(artifacts)s)",
    "DELETE FROM content.artifacts WHERE learner_id = %(learner_id)s "
    "AND artifact_id = ANY(%(artifacts)s)",
    "DELETE FROM content.session_plans WHERE learner_id = %(learner_id)s "
    "AND session_id = ANY(%(sessions)s)",
    "DELETE FROM ops.turn_manifests WHERE learner_id = %(learner_id)s "
    "AND session_id = ANY(%(sessions)s)",
    "DELETE FROM content.sessions WHERE learner_id = %(learner_id)s "
    "AND session_id = ANY(%(sessions)s)",
)
_LOG = (
    "INSERT INTO ops.deletion_log (deletion_id, learner_id, kind, target_id, deleted_at) "
    "VALUES (%(deletion_id)s, %(learner_id)s, 'domain', %(target_id)s, %(deleted_at)s) "
    "ON CONFLICT (deletion_id) DO NOTHING"
)


@dataclass(frozen=True)
class DeletionDone:
    deletion_id: UUID
    copies_days: int = COPIES_DAYS


class DeletionBusy(Exception):
    """The learner's projection or another deletion holds the lock: repeat later."""


async def _own(neo4j: AsyncDriver, learner_id: UUID, domain_id: UUID) -> bool:
    async with read_session(neo4j) as session:
        result = await session.run(literal(_OWN_DOMAIN), id=str(domain_id), learner=str(learner_id))
        record = await result.single()
    return record is not None and int(record["n"]) > 0


async def erase_domain(db: Db, store: ObjectStore, learner_id: UUID, domain_id: UUID) -> int:
    """The rows and objects of the domain (`Т-§19.5`); returns the number of objects."""
    params: dict[str, object] = {"learner_id": learner_id, "domain_id": domain_id}
    async with db.transaction():
        params["sessions"] = [r["session_id"] for r in await db.fetch_all(_SESSIONS, params)]
        materials = await db.fetch_all(_MATERIALS, params)
        params["materials"] = [r["material_id"] for r in materials]
        params["artifacts"] = [r["artifact_id"] for r in await db.fetch_all(_ARTIFACTS, params)]
        keys = {r["object_key"] for r in materials}
        keys |= {r["image_key"] for r in await db.fetch_all(_PAGE_KEYS, params)}
        keys |= {r["key"] for r in await db.fetch_all(_EVENT_KEYS, params) if r["key"]}
        for statement in _DELETES:
            await db.execute(statement, params)
    if keys:
        await store.delete_keys(sorted(keys))
    return len(keys)


async def rebuild_after(
    db: Db, neo4j: AsyncDriver, settings: Settings, learner_id: UUID, *, redis: Redis | None
) -> None:
    """The graph forgets what was deleted (WP3.5); the projector may hold the lock a moment."""
    for _ in range(_REBUILD_TRIES):
        outcome = await rebuild_learner(
            db, neo4j, settings, learner_id, redis=redis, respect_presence=False
        )
        if outcome.status != "busy":
            return
        await asyncio.sleep(0.2)


async def apply_domain_record(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    store: ObjectStore,
    learner_id: UUID,
    record: DeletionRecord,
    *,
    redis: Redis | None = None,
) -> None:
    """The deletion the record names, under the learner's lock, then its row of
    `ops.deletion_log` and the rebuild. Repeatable: a second run finds nothing to delete and
    the log row exists (WP25 п.5)."""
    domain_id = record.target_id
    async with db.advisory_lock(LockKeyspace.LEARNER_PROJECTION, learner_id) as acquired:
        if not acquired:
            raise DeletionBusy(str(learner_id))
        await erase_domain(db, store, learner_id, domain_id)
        log_row = {
            "deletion_id": record.deletion_id,
            "learner_id": learner_id,
            "target_id": domain_id,
            "deleted_at": record.deleted_at,
        }
        await db.execute(_LOG, log_row)
    await rebuild_after(db, neo4j, settings, learner_id, redis=redis)


async def delete_domain(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    journal: DeletionJournal,
    store: ObjectStore,
    learner_id: UUID,
    domain_id: UUID,
    now: datetime,
    *,
    redis: Redis | None = None,
) -> DeletionDone | None:
    """None — not the learner's domain (404). `DeletionJournalUnavailable` from the journal
    propagates (503): without the record nothing is deleted."""
    if not await _own(neo4j, learner_id, domain_id):
        return None
    record = DeletionRecord(uuid4(), learner_id, "domain", domain_id, now)
    await journal.write(record)
    await apply_domain_record(db, neo4j, settings, store, learner_id, record, redis=redis)
    return DeletionDone(record.deletion_id)
