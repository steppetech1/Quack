"""The glue of the structure step (`quack.ops.material_parse`, 2026-11-24): every topic and
skill of the domain has its vector before the reconciliation looks for candidates."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest

from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.jobs.runner import JobContext
from quack.jobs.types import JobDeferred
from quack.ops import material_parse as glue
from quack.ops.material_parse import embed_domain_nodes, node_text, wait_projected
from tests.db.fixtures import create_learner
from tests.support.embeddings import FakeEmbeddingClient


def test_a_topic_is_embedded_by_name_and_a_skill_by_name_and_summary() -> None:
    topic, skill = uuid4(), uuid4()
    node = node_text("topic.created", {"topic_id": str(topic), "name": "ЦПТ"})
    assert (node.node_kind, node.node_id, node.text) == ("topic", topic, "ЦПТ")
    node = node_text(
        "skill.created", {"skill_id": str(skill), "name": "находить SE", "summary": "σ/√n"}
    )
    assert (node.node_kind, node.node_id, node.text) == ("skill", skill, "находить SE\nσ/√n")


async def _domain_with_nodes(db: Db) -> tuple[UUID, UUID, UUID, UUID]:
    learner = await create_learner(db)
    domain, topic, skill = uuid4(), uuid4(), uuid4()
    await append_events(
        db,
        learner,
        [
            NewEvent(
                type="domain.created",
                payload={"domain_id": str(domain), "name": "Статистика"},
                actor="learner",
                idempotency_key=str(uuid4()),
            ),
            NewEvent(
                type="topic.created",
                payload={
                    "topic_id": str(topic),
                    "domain_id": str(domain),
                    "name": "Выборочные распределения",
                    "origin": "material",
                },
                actor="builder@1",
                idempotency_key=str(uuid4()),
                domain_id=domain,
            ),
            NewEvent(
                type="skill.created",
                payload={
                    "skill_id": str(skill),
                    "topic_id": str(topic),
                    "domain_id": str(domain),
                    "name": "находить стандартную ошибку",
                    "summary": "SE = σ / √n",
                    "status": "active",
                    "origin": "material",
                },
                actor="builder@1",
                idempotency_key=str(uuid4()),
                domain_id=domain,
            ),
        ],
    )
    return learner, domain, topic, skill


def _ctx(db: Db, settings: Settings, client: FakeEmbeddingClient) -> JobContext:
    return cast(
        JobContext,
        SimpleNamespace(db=db, settings=settings, embeddings=client, redis=None, neo4j=None),
    )


async def test_the_nodes_of_the_domain_get_their_vectors_once(
    pg_db: Db, settings: Settings
) -> None:
    learner, domain, topic, skill = await _domain_with_nodes(pg_db)
    client = FakeEmbeddingClient(settings.embedding_dim)
    ctx = _ctx(pg_db, settings, client)

    await embed_domain_nodes(ctx, learner_id=learner, domain_id=domain)
    rows = await pg_db.fetch_all(
        "SELECT payload->>'node_kind' AS kind, payload->>'node_id' AS node FROM core.events "
        "WHERE learner_id = %(l)s AND type = 'embedding.ready' ORDER BY seq",
        {"l": learner},
    )
    assert {(r["kind"], r["node"]) for r in rows} == {("topic", str(topic)), ("skill", str(skill))}
    sent = client.calls

    # a repeat embeds nothing again (law 5) and asks Modal nothing
    await embed_domain_nodes(ctx, learner_id=learner, domain_id=domain)
    assert client.calls == sent
    assert (
        len(
            await pg_db.fetch_all(
                "SELECT 1 FROM core.events WHERE learner_id = %(l)s AND type = 'embedding.ready'",
                {"l": learner},
            )
        )
        == 2
    )


async def test_without_embeddings_the_step_waits(pg_db: Db, settings: Settings) -> None:
    """Without vectors the builder would only create twins: the job waits instead (Т-§20.7)."""
    learner, domain, _topic, _skill = await _domain_with_nodes(pg_db)
    ctx = _ctx(pg_db, settings, FakeEmbeddingClient(settings.embedding_dim, available=False))
    with pytest.raises(JobDeferred):
        await embed_domain_nodes(ctx, learner_id=learner, domain_id=domain)


async def test_wait_projected_reads_projected_seq_watermark(
    pg_db: Db, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`wait_projected` waits for `Learner.projectedSeq` (via `read_watermark`) to reach the
    latest graph event of the learner, and defers if the projector stays behind."""
    learner, _domain, _topic, _skill = await _domain_with_nodes(pg_db)
    fast = settings.model_copy(update={"material_projection_wait_s": 0.05})
    ctx = cast(
        JobContext,
        SimpleNamespace(db=pg_db, settings=fast, embeddings=None, redis=None, neo4j=object()),
    )

    async def _behind(_driver: object, _learner: str) -> tuple[int, str] | None:
        return (1, "rules@3")

    monkeypatch.setattr(glue, "read_watermark", _behind)
    with pytest.raises(JobDeferred):
        await wait_projected(ctx, learner_id=learner)

    async def _caught_up(_driver: object, _learner: str) -> tuple[int, str] | None:
        return (100, "rules@3")

    monkeypatch.setattr(glue, "read_watermark", _caught_up)
    await wait_projected(ctx, learner_id=learner)
