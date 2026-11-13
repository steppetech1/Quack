"""Deletion of the whole account (ТЗ Ф4 WP25 п.3; Т-§19.5, Д-§8.5–8.6).

Every row of the learner in the schema of Phase 4, the prefix `learners/{id}/` of the object
store, the learner's subgraph in Neo4j and its keys in Redis; the record in the journal of R2
first, the row of `ops.deletion_log` last (the log row stays: it is what reconcile and replay
read). `DELETE /v1/learner` of Phase 3 (a tombstone) is switched to this with WP1.3.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.db import Db
from quack.db.db import LockKeyspace
from quack.db.redis import presence_key
from quack.deletion.domain import DeletionBusy, DeletionDone
from quack.graph.projection.store import delete_learner_graph
from quack.llm.tracing import Tracer
from quack.storage.deletions import DeletionJournal, DeletionRecord
from quack.storage.objects import ObjectStore

# The mark of the dispatcher is locked first: a batch of the dispatcher in flight commits its jobs
# before the deletion of `ops.jobs` looks for them, and one that starts later finds the row
# locked and skips the learner (`FOR UPDATE SKIP LOCKED`); no job of the learner outlives it.
_ENSURE_MARK = (
    "INSERT INTO ops.dispatcher_offsets (learner_id) VALUES (%(learner_id)s) "
    "ON CONFLICT (learner_id) DO NOTHING"
)
_LOCK_MARK = "SELECT 1 FROM ops.dispatcher_offsets WHERE learner_id = %(learner_id)s FOR UPDATE"
_EMAIL = "SELECT email FROM core.learners WHERE learner_id = %(learner_id)s"
# Children before parents; `ops.deletion_log` is kept on purpose.
_DELETES = (
    "DELETE FROM core.auth_links WHERE email = %(email)s",
    "DELETE FROM core.auth_sessions WHERE learner_id = %(learner_id)s",
    "DELETE FROM core.pairing_tokens WHERE learner_id = %(learner_id)s",
    "DELETE FROM content.artifact_versions WHERE learner_id = %(learner_id)s",
    "DELETE FROM content.artifacts WHERE learner_id = %(learner_id)s",
    "DELETE FROM content.material_pages WHERE learner_id = %(learner_id)s",
    "DELETE FROM content.materials WHERE learner_id = %(learner_id)s",
    "DELETE FROM content.canvas_boards WHERE learner_id = %(learner_id)s",
    "DELETE FROM content.tasks WHERE learner_id = %(learner_id)s",
    "DELETE FROM content.session_plans WHERE learner_id = %(learner_id)s",
    "DELETE FROM content.study_plans WHERE learner_id = %(learner_id)s",
    "DELETE FROM content.learner_skills WHERE learner_id = %(learner_id)s",
    "DELETE FROM content.exports WHERE learner_id = %(learner_id)s",
    "DELETE FROM content.sessions WHERE learner_id = %(learner_id)s",
    "DELETE FROM ops.jobs WHERE learner_id = %(learner_id)s",
    "DELETE FROM ops.coverage WHERE learner_id = %(learner_id)s",
    "DELETE FROM ops.turn_manifests WHERE learner_id = %(learner_id)s",
    "DELETE FROM ops.llm_usage WHERE learner_id = %(learner_id)s",
    "DELETE FROM ops.projector_failures WHERE learner_id = %(learner_id)s",
    "DELETE FROM ops.projector_offsets WHERE learner_id = %(learner_id)s",
    "DELETE FROM ops.dispatcher_offsets WHERE learner_id = %(learner_id)s",
    "DELETE FROM core.events WHERE learner_id = %(learner_id)s",
    "DELETE FROM core.learners WHERE learner_id = %(learner_id)s",
)
_LOG = (
    "INSERT INTO ops.deletion_log (deletion_id, learner_id, kind, target_id, deleted_at) "
    "VALUES (%(deletion_id)s, %(learner_id)s, 'account', %(learner_id)s, %(deleted_at)s) "
    "ON CONFLICT (deletion_id) DO NOTHING"
)


async def apply_account_record(
    db: Db,
    neo4j: AsyncDriver,
    store: ObjectStore,
    learner_id: UUID,
    record: DeletionRecord,
    *,
    redis: Redis | None = None,
) -> None:
    """Everything of the learner, then the log row; repeatable (a second run finds nothing)."""
    async with db.advisory_lock(LockKeyspace.LEARNER_PROJECTION, learner_id) as acquired:
        if not acquired:
            raise DeletionBusy(str(learner_id))
        async with db.transaction():
            await db.execute(_ENSURE_MARK, {"learner_id": learner_id})
            await db.fetch_one(_LOCK_MARK, {"learner_id": learner_id})
            row = await db.fetch_one(_EMAIL, {"learner_id": learner_id})
            params = {"learner_id": learner_id, "email": None if row is None else row["email"]}
            for statement in _DELETES:
                await db.execute(statement, params)
        await store.delete_prefix(f"learners/{learner_id}/")
        await delete_learner_graph(neo4j, str(learner_id))
        if redis is not None:
            await redis.delete(presence_key(learner_id))
        log_row = {
            "deletion_id": record.deletion_id,
            "learner_id": learner_id,
            "deleted_at": record.deleted_at,
        }
        await db.execute(_LOG, log_row)


async def delete_account(
    db: Db,
    neo4j: AsyncDriver,
    journal: DeletionJournal,
    store: ObjectStore,
    learner_id: UUID,
    now: datetime,
    *,
    redis: Redis | None = None,
    tracer: Tracer | None = None,
    pseudonym: str | None = None,
) -> DeletionDone:
    """The journal record first (`Д-§8.5`): `DeletionJournalUnavailable` propagates (503).

    The learner's traces go last, by their pseudonym (WP10.6, #210): `False` from the tracer —
    nothing more, the traces expire by `langsmith_retention_days` (D-B-202)."""
    record = DeletionRecord(uuid4(), learner_id, "account", learner_id, now)
    await journal.write(record)
    await apply_account_record(db, neo4j, store, learner_id, record, redis=redis)
    if tracer is not None and pseudonym is not None:
        await tracer.delete_learner(pseudonym)
    return DeletionDone(record.deletion_id)
