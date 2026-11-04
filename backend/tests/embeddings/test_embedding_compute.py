"""Body of `embedding.compute` (ТЗ Ф4 WP20.3; Т-§3.1, Т-§20.7, закон 5): a batch of nodes →
one `embed_documents` call → `embedding.ready` per node with the vector as base64 float32.
Integration tests on the Phase 4 database (`pg_db`)."""

from __future__ import annotations

import base64
import struct
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.config import Settings
from quack.db import Db
from quack.embeddings.client import EMBEDDING_MODEL, EmbeddingsUnavailable
from quack.embeddings.compute import EmbeddingNode, compute_embeddings
from tests.db.fixtures import create_learner
from tests.support.embeddings import FakeEmbeddingClient


class CountingClient(FakeEmbeddingClient):
    def __init__(self, dim: int) -> None:
        super().__init__(dim)
        self.batches: list[list[str]] = []

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return await super().embed_documents(texts)


def _decode(vector: str) -> list[float]:
    raw = base64.b64decode(vector)
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


async def _ready(db: Db, learner: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT idempotency_key, payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'embedding.ready' ORDER BY seq",
        {"learner_id": learner},
    )


async def test_batch_writes_one_event_per_node(pg_db: Db, settings: Settings) -> None:
    learner = await create_learner(pg_db)
    client = CountingClient(settings.embedding_dim)
    nodes = [
        EmbeddingNode("chunk", uuid4(), "The limit of sin x over x is 1"),
        EmbeddingNode("topic", uuid4(), "Limits"),
    ]

    seqs = await compute_embeddings(pg_db, client, learner_id=learner, nodes=nodes)

    assert client.batches == [[n.text for n in nodes]]  # one call for the batch
    rows = await _ready(pg_db, learner)
    assert len(seqs) == 2 and len(rows) == 2
    expected = await FakeEmbeddingClient(settings.embedding_dim).embed_documents(
        [n.text for n in nodes]
    )
    for node, row, vector in zip(nodes, rows, expected, strict=True):
        payload = row["payload"]
        assert payload["node_kind"] == node.node_kind and payload["node_id"] == str(node.node_id)
        assert payload["model"] == EMBEDDING_MODEL and payload["dim"] == settings.embedding_dim
        assert _decode(payload["vector"]) == pytest.approx(vector, abs=1e-6)
        assert row["idempotency_key"] == f"emb:{node.node_kind}:{node.node_id}:{EMBEDDING_MODEL}"


async def test_repeat_embeds_only_new_nodes(pg_db: Db, settings: Settings) -> None:
    """Закон 5: a retried job writes nothing twice and does not pay Modal for done nodes."""
    learner = await create_learner(pg_db)
    done = EmbeddingNode("skill", uuid4(), "Compute a limit")
    await compute_embeddings(
        pg_db, FakeEmbeddingClient(settings.embedding_dim), learner_id=learner, nodes=[done]
    )
    client = CountingClient(settings.embedding_dim)
    fresh = EmbeddingNode("misconception", uuid4(), "Cancels terms across a sum")

    await compute_embeddings(pg_db, client, learner_id=learner, nodes=[done, fresh])
    await compute_embeddings(pg_db, client, learner_id=learner, nodes=[done, fresh])

    assert client.batches == [[fresh.text]]
    assert len(await _ready(pg_db, learner)) == 2


async def test_modal_down_writes_nothing(pg_db: Db, settings: Settings) -> None:
    """Т-§20.7: Modal does not answer — the job fails to be retried, nothing is written."""
    learner = await create_learner(pg_db)
    client = FakeEmbeddingClient(settings.embedding_dim, available=False)
    with pytest.raises(EmbeddingsUnavailable):
        await compute_embeddings(
            pg_db, client, learner_id=learner, nodes=[EmbeddingNode("topic", uuid4(), "Limits")]
        )
    assert await _ready(pg_db, learner) == []
    assert await compute_embeddings(pg_db, client, learner_id=learner, nodes=[]) == []
