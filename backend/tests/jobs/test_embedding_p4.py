"""The job `embedding.compute` (ТЗ Ф4 WP7.1 seam S6, WP20.3, D-A-241): the payload of the
dispatcher reaches C's body; Modal down — the job waits without spending an attempt."""

from __future__ import annotations

from uuid import uuid4

import pytest

from quack.config import Settings
from quack.db import Db
from quack.jobs.embedding_p4 import handle_embedding_compute
from quack.jobs.types import ClaimedJob, JobDeferred
from tests.db.fixtures import create_learner
from tests.support.embeddings import FakeEmbeddingClient
from tests.support.resources import build_job_context


async def test_embedding_job_writes_vectors_and_waits_for_modal(
    pg_db: Db, settings: Settings
) -> None:
    learner, chunk = await create_learner(pg_db), uuid4()
    nodes = [{"node_kind": "chunk", "node_id": str(chunk), "text": "x"}]
    job = ClaimedJob(id=uuid4(), kind="embedding.compute", learner_id=learner, lock_key="e",
                     coalesce_key=None, priority=2, payload={"nodes": nodes}, attempt=1,
                     max_attempts=5)  # fmt: skip
    down = FakeEmbeddingClient(settings.embedding_dim, available=False)
    with pytest.raises(JobDeferred) as waits:
        await handle_embedding_compute(job, build_job_context(settings, None, None, db=pg_db,
                                                              embeddings=down))  # fmt: skip
    assert waits.value.delay_sec == settings.job_backoff_max_sec
    up = build_job_context(
        settings, None, None, db=pg_db, embeddings=FakeEmbeddingClient(settings.embedding_dim)
    )
    for _ in range(2):  # a retry writes nothing twice (law 5)
        await handle_embedding_compute(job, up)
    rows = await pg_db.fetch_all(
        "SELECT payload->>'node_id' AS node FROM core.events "
        "WHERE learner_id = %(l)s AND type = 'embedding.ready'",
        {"l": learner},
    )
    assert [r["node"] for r in rows] == [str(chunk)]
