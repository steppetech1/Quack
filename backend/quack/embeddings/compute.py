"""Body of the job `embedding.compute` (ТЗ Ф4 WP20.3; Т-§3.1, Т-§10.5, Т-§20.7, закон 5).

A batch of nodes with their texts → one `embed_documents` call → `embedding.ready
{node_kind, node_id, model, dim, vector}` per node, all with one `append_events`; the vector is
base64 of little-endian float32 (Т-§3.1 «в двоичном виде»). Nodes whose `embedding.ready` of
this model is in the log already are skipped before the call: a retried job neither writes
twice nor pays Modal again. Modal down — `EmbeddingsUnavailable` and nothing written: the job
waits in the queue (Т-§11.9). The registration of the kind and the shape of its parameters are
seam S6 (stream A).
"""

from __future__ import annotations

import base64
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from redis.asyncio import Redis

from quack.db import Db
from quack.embeddings.client import EMBEDDING_MODEL, EmbeddingClient
from quack.events.event_types import EmbeddingReady
from quack.events.keys import embedding_key
from quack.events.log import NewEvent, append_events

type NodeKind = Literal["evidence", "misconception", "chunk", "skill", "topic"]

_DONE = (
    "SELECT idempotency_key FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'embedding.ready' AND idempotency_key = ANY(%(keys)s)"
)


@dataclass(frozen=True)
class EmbeddingNode:
    node_kind: NodeKind
    node_id: UUID
    text: str


def encode_vector(vector: Sequence[float]) -> str:
    """Т-§3.1: base64 of little-endian float32."""
    return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")


async def compute_embeddings(
    db: Db,
    client: EmbeddingClient,
    *,
    learner_id: UUID,
    nodes: Sequence[EmbeddingNode],
    redis: Redis | None = None,
) -> list[int]:
    """Embed the nodes not embedded yet; the `seq` of the written `embedding.ready`."""
    keys = {embedding_key(n.node_kind, n.node_id, EMBEDDING_MODEL): n for n in nodes}
    if not keys:
        return []
    done = await db.fetch_all(_DONE, {"learner_id": learner_id, "keys": list(keys)})
    todo = [n for key, n in keys.items() if key not in {r["idempotency_key"] for r in done}]
    if not todo:
        return []
    vectors = await client.embed_documents([n.text for n in todo])
    events = [
        NewEvent(
            type="embedding.ready",
            payload=EmbeddingReady(
                node_kind=node.node_kind,
                node_id=node.node_id,
                model=EMBEDDING_MODEL,
                dim=len(vector),
                vector=encode_vector(vector),
            ),
            actor="system",
            idempotency_key=embedding_key(node.node_kind, node.node_id, EMBEDDING_MODEL),
        )
        for node, vector in zip(todo, vectors, strict=True)
    ]
    stored = await append_events(db, learner_id, events, redis=redis)
    return [event.seq for event in stored]
