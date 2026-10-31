"""Phase 1 `ops` commands (Д-§6): migrate, verify-schema, rebuild, replay-dlq, purge-deleted."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from neo4j import AsyncDriver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.locks import LockKeyspace, advisory_lock
from quack.db.postgres import SessionFactory
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.jobs.queue import JobSpec, enqueue_job
from quack.logging import get_logger

log = get_logger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Tables with learner data removed by purge-deleted (Т-§2.9, Т-§16.3).
PURGE_TABLES = (
    "core.events",
    "core.learner_seq",
    "core.sessions",
    "core.artifacts",
    "core.domains",
    "core.task_bank",
    "core.llm_outbox",
    "ops.llm_ledger",
    "ops.jobs",
    "ops.dlq",
    "ops.projector_offsets",
)


def migrate(settings: Settings) -> None:
    """alembic upgrade head."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.sqlalchemy_dsn)
    command.upgrade(cfg, "head")


async def replay_dlq(session_factory: SessionFactory, *, dlq_ids: list[UUID] | None = None) -> int:
    """Re-enqueue failed jobs and release stopped projector streams; marks replayed_at."""
    async with session_factory() as session, session.begin():
        rows = (
            await session.execute(
                text(
                    "SELECT id, source, kind, learner_id, payload FROM ops.dlq "
                    "WHERE replayed_at IS NULL "
                    "AND (CAST(:ids AS UUID[]) IS NULL OR id = ANY(CAST(:ids AS UUID[]))) "
                    "ORDER BY created_at, id FOR UPDATE"
                ),
                {"ids": dlq_ids},
            )
        ).all()
        for row in rows:
            m = row._mapping
            if m["source"] == "job":
                snap = dict(m["payload"])
                learner = snap.get("learner_id")
                await enqueue_job(
                    session,
                    JobSpec(
                        kind=str(snap["kind"]),
                        lock_key=str(snap["lock_key"]),
                        priority=int(snap["priority"]),
                        coalesce_key=snap.get("coalesce_key"),
                        learner_id=UUID(learner) if learner else None,
                        payload=dict(snap.get("payload") or {}),
                        max_attempts=int(snap.get("max_attempts", 5)),
                        required_seq=snap.get("required_seq"),
                    ),
                )
            # source = 'projector': marking it replayed lets the worker retry the stream.
            await session.execute(
                text("UPDATE ops.dlq SET replayed_at = now() WHERE id = :id"), {"id": m["id"]}
            )
    log.info("dlq_replayed", count=len(rows))
    return len(rows)


async def purge_deleted(
    engine: AsyncEngine, session_factory: SessionFactory, driver: AsyncDriver, *, chunk_size: int
) -> list[UUID]:
    """Physically delete learners with a pending_purge tombstone (Т-§2.9)."""
    async with session_factory() as session:
        pending = [
            row[0]
            for row in await session.execute(
                text(
                    "SELECT learner_id FROM ops.deletion_tombstones "
                    "WHERE status = 'pending_purge' ORDER BY requested_at"
                )
            )
        ]
    purged: list[UUID] = []
    for learner_id in pending:
        async with advisory_lock(engine, LockKeyspace.LEARNER_PROJECTION, learner_id) as ok:
            if not ok:
                log.info("purge_skipped_locked", learner_id=str(learner_id))
                continue
            async with session_factory() as session, session.begin():
                for table in PURGE_TABLES:
                    await session.execute(
                        text(f"DELETE FROM {table} WHERE learner_id = :l"), {"l": learner_id}
                    )
                await session.execute(
                    text("DELETE FROM auth.users WHERE id = :l"), {"l": learner_id}
                )
            await Neo4jGraphStore(driver, str(learner_id)).delete_learner(chunk_size=chunk_size)
            async with session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE ops.deletion_tombstones SET status = 'purged', purged_at = now() "
                        "WHERE learner_id = :l"
                    ),
                    {"l": learner_id},
                )
            purged.append(learner_id)
            log.info("learner_purged", learner_id=str(learner_id))
    return purged
