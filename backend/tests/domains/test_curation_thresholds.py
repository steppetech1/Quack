"""WP1.6: the deferral of `graph.curate` during a live session is `curation_defer_min`.

WP3.2 (§8.9 item 2): the handler raises `JobDeferred`; the runner turns it into the status,
`run_after` and `attempt` that are checked here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import presence_key
from quack.domains.curation import handle_graph_curate
from quack.jobs import kinds
from quack.jobs.queue import enqueue_job
from quack.jobs.runner import JobRunner
from tests.support.resources import build_job_context


async def test_graph_curate_defer_uses_curation_defer_min(
    seeded_learner: tuple[UUID, str],
    db: SessionFactory,
    settings: Settings,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    learner_id, _ = seeded_learner
    domain_id = uuid4()
    cfg = settings.model_copy(update={"curation_defer_min": 3})
    await redis.set(presence_key(learner_id), "active-session", ex=300)
    async with db() as session, session.begin():
        job_id = await enqueue_job(
            session,
            kinds.graph_curate(
                learner_id, domain_id, {"domain_id": str(domain_id)}, required_seq=None
            ),
        )
    before = datetime.now(UTC)
    runner = JobRunner(
        cfg,
        db,
        "test-worker",
        handlers={"graph.curate": handle_graph_curate},
        context=build_job_context(cfg, db, neo4j, redis),
    )
    assert await runner.run_one()

    async with db() as session:
        row = (
            await session.execute(
                text("SELECT status, attempt, run_after FROM ops.jobs WHERE id = :id"),
                {"id": job_id},
            )
        ).one()
    assert (row.status, row.attempt) == ("queued", 0)
    assert before + timedelta(minutes=2, seconds=30) <= row.run_after
    assert row.run_after <= datetime.now(UTC) + timedelta(minutes=3, seconds=30)
