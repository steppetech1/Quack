"""Log dispatcher in tests (ТЗ Фазы 3 WP11.4, §8.9 п.7; stream A fixture, seam S9).

After WP11.4 derived jobs appear only when the dispatcher has read the trigger event. Tests
that checked `ops.jobs` right after a request call `dispatch` first. Jobs with
`required_seq` (`turn.extract`, …) are claimable only after the projector applied the trigger
(WP3.1); `project_and_dispatch` runs the projector for the learner too.
"""

from __future__ import annotations

from uuid import UUID

from neo4j import AsyncDriver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.graph.worker import ProjectorWorker
from quack.jobs.dispatcher import Dispatcher


async def dispatch(settings: Settings, db: SessionFactory, learner_id: UUID) -> int:
    """Dispatch the learner's new events; returns the number of events read."""
    return await Dispatcher(settings, db).process_learner(learner_id)


async def project(
    settings: Settings,
    db: SessionFactory,
    engine: AsyncEngine,
    neo4j: AsyncDriver,
    learner_id: UUID,
) -> None:
    """Apply the learner's events to Neo4j and move `ops.projector_offsets`."""
    projector = ProjectorWorker(settings, engine, db, neo4j, None)
    while True:
        outcome = await projector.process_learner(learner_id)
        if outcome is None or outcome.status != "applied":
            return


async def project_and_dispatch(
    settings: Settings,
    db: SessionFactory,
    engine: AsyncEngine,
    neo4j: AsyncDriver,
    learner_id: UUID,
) -> None:
    await project(settings, db, engine, neo4j, learner_id)
    await dispatch(settings, db, learner_id)


async def mark_dispatched(db: SessionFactory, learner_id: UUID) -> None:
    """Treat the learner's log as dispatched: for events a test wrote straight into the log,
    whose jobs the test does not look at."""
    async with db() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO ops.dispatcher_offsets (learner_id, last_dispatched_seq, updated_at) "
                "SELECT learner_id, last_seq, now() FROM core.learner_seq WHERE learner_id = :l "
                "ON CONFLICT (learner_id) DO UPDATE "
                "SET last_dispatched_seq = EXCLUDED.last_dispatched_seq, updated_at = now()"
            ),
            {"l": learner_id},
        )
