"""Projector and dispatcher of Phase 4 in tests (ТЗ Ф4 WP7.1, WP1.3; seam S9).

Derived jobs appear only when the dispatcher has read the trigger event; a test that checks
`ops.jobs` right after a request calls `dispatch` first. Jobs with `required_seq` are claimable
only after the projector applied the trigger; `project_and_dispatch` runs the projector for the
learner too. Both work on the `Db` of the test (`pg_db`) and the `neo4j` fixture.
"""

from __future__ import annotations

from uuid import UUID

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.graph.projection.runner import project_learner
from quack.jobs.dispatcher_p4 import DispatcherP4


async def dispatch(
    settings: Settings, db: Db, learner_id: UUID, *, redis: Redis | None = None
) -> int:
    """Dispatch the learner's new events; returns the number of events read."""
    return await DispatcherP4(settings, db, redis).process_learner(learner_id)


async def project(
    settings: Settings,
    db: Db,
    neo4j: AsyncDriver,
    learner_id: UUID,
    *,
    redis: Redis | None = None,
) -> None:
    """Apply the learner's events to Neo4j and move `ops.projector_offsets`."""
    while True:
        outcome = await project_learner(db, neo4j, settings, learner_id, redis=redis)
        if outcome.status != "applied":
            return


async def project_and_dispatch(
    settings: Settings,
    db: Db,
    neo4j: AsyncDriver,
    learner_id: UUID,
    *,
    redis: Redis | None = None,
) -> None:
    await project(settings, db, neo4j, learner_id, redis=redis)
    await dispatch(settings, db, learner_id, redis=redis)


async def mark_dispatched(db: Db, learner_id: UUID) -> None:
    """Treat the learner's log as dispatched: for events a test wrote straight into the log,
    whose jobs the test does not look at."""
    await db.execute(
        "INSERT INTO ops.dispatcher_offsets (learner_id, last_dispatched_seq, updated_at) "
        "SELECT learner_id, seq, now() FROM core.learners WHERE learner_id = %(learner_id)s "
        "ON CONFLICT (learner_id) DO UPDATE "
        "SET last_dispatched_seq = EXCLUDED.last_dispatched_seq, updated_at = now()",
        {"learner_id": learner_id},
    )
