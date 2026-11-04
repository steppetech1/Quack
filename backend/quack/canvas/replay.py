"""Replay of a canvas (ТЗ Ф4 WP21.6; Т-§15.7, П-9.8).

The replay is built from the stroke journal with its time: the client plays it fast and shows
what was erased. The API gives signed links to the chunks of the canvas
(`canvas/{session_id}/{canvas_id}/`, D-C-135) in the order they were written — by the ULID of
the key, not by the chunk number alone, which starts again with every socket (D-C-114). The client
decodes a chunk (MessagePack, zstd, delta points) and drops a repeated `batch_no` and
`stroke_id`.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from quack.storage.keys import canvas_chunk_prefix
from quack.storage.objects import ObjectStore


@dataclass(frozen=True)
class ReplayChunk:
    url: str
    size: int


# The first 10 characters of a ULID are its milliseconds.
_ULID_TIME_CHARS = 10


def _written_order(key: str) -> tuple[str, int]:
    """`{chunk}-{ulid}.bin` → the millisecond of the ULID, then the chunk number: one socket
    may write two chunks within a millisecond, and the random part of the ULID would mix them."""
    chunk, ulid = key.rsplit("/", 1)[-1].split("-", 1)
    return ulid[:_ULID_TIME_CHARS], int(chunk)


async def replay_chunks(
    storage: ObjectStore, *, learner_id: UUID, session_id: UUID, canvas_id: UUID, ttl_s: int
) -> list[ReplayChunk]:
    """Signed links to the chunks of the canvas, oldest first; links live `ttl_s`."""
    objects = await storage.list_objects(canvas_chunk_prefix(learner_id, session_id, canvas_id))
    objects.sort(key=lambda item: _written_order(item.key))
    return [
        ReplayChunk(url=await storage.presigned_get_url(item.key, ttl_s), size=item.size)
        for item in objects
    ]
