"""WP1.6: the deferral of `graph.curate` during a live session is `curation_defer_min`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import presence_key
from quack.domains.curation import handle_graph_curate
from quack.jobs import kinds
from quack.jobs.queue import claim_next_job, enqueue_job
from quack.jobs.runner import JobContext


async def test_graph_curate_defer_uses_curation_defer_min(
    seeded_learner: tuple[UUID, str], db: SessionFactory, settings: Settings, redis: Redis
) -> None:
    learner_id, _ = seeded_learner
    domain_id = uuid4()
    cfg = settings.model_copy(update={"curation_defer_min": 3})
    await redis.set(presence_key(learner_id), "active-session", ex=300)
    async with db() as session, session.begin():
        await enqueue_job(
            session, kinds.graph_curate(learner_id, domain_id, {"domain_id": str(domain_id)})
        )
    before = datetime.now(UTC)
    async with db() as session, session.begin():
        claimed = await claim_next_job(session, "test-worker", ["graph.curate"])
    assert claimed is not None

    await handle_graph_curate(claimed, JobContext(settings=cfg, session_factory=db))

    async with db() as session:
        run_after = (
            await session.execute(
                text("SELECT run_after FROM ops.jobs WHERE id = :id"), {"id": claimed.id}
            )
        ).scalar_one()
    assert before + timedelta(minutes=2, seconds=30) <= run_after
    assert run_after <= datetime.now(UTC) + timedelta(minutes=3, seconds=30)
