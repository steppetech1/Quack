"""The stroke journal of a canvas (Т-§15.1, Т-§15.2, ТЗ Ф4 WP21.2).

Strokes are raw material, not events: the API keeps the client's batches in memory and writes a
chunk `learners/{id}/canvas/{session_id}/{canvas_id}/{chunk}-{ulid}.bin` every
`stroke_chunk_seconds` (the canvas in the key — D-C-135).
A batch is acknowledged only after its chunk is written; a repeated `batch_no` is dropped; with
the store down nothing is acknowledged and the client sends again (Т-§20.7). Chunk format:
points as deltas `(dx, dy, dt, pressure)`, MessagePack, zstd. An erasure is an operation of
the journal, never a deletion.

`batch_no` seen before is remembered by the journal of one API process only: after a restart a
batch that was written but not yet acknowledged comes again and lands in a second chunk. Strokes
are raw material, not events, so this is allowed; the replay (Т-§15.7, WP21.6) must tolerate a
repeated `batch_no` and `stroke_id` and order chunks by their key (the ULID), not by number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import msgpack
import zstandard

from quack.config import Settings
from quack.storage.keys import canvas_chunk_key
from quack.storage.objects import ObjectStore, StorageUnavailable

CHUNK_CONTENT_TYPE = "application/octet-stream"


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    t_ms: int
    pressure: float


@dataclass(frozen=True)
class Stroke:
    stroke_id: str
    points: tuple[Point, ...]


@dataclass(frozen=True)
class Erasure:
    """The learner erased these strokes at `at_ms` (device time)."""

    stroke_ids: tuple[str, ...]
    at_ms: int


@dataclass(frozen=True)
class StrokeBatch:
    batch_no: int
    strokes: tuple[Stroke, ...] = ()
    erasures: tuple[Erasure, ...] = ()


def encode_chunk(batches: list[StrokeBatch]) -> bytes:
    """Batches → MessagePack with delta points, compressed by zstd."""
    payload = {"batches": [_batch_to_raw(batch) for batch in batches]}
    packed = msgpack.packb(payload, use_bin_type=True)
    assert isinstance(packed, bytes)
    return zstandard.ZstdCompressor().compress(packed)


def decode_chunk(data: bytes) -> list[StrokeBatch]:
    """The inverse of `encode_chunk` (replay, Т-§15.7)."""
    raw = msgpack.unpackb(zstandard.ZstdDecompressor().decompress(data), raw=False)
    return [_batch_from_raw(item) for item in raw["batches"]]


def _batch_to_raw(batch: StrokeBatch) -> dict[str, Any]:
    return {
        "batch_no": batch.batch_no,
        "strokes": [
            {"stroke_id": stroke.stroke_id, "points": _deltas(stroke.points)}
            for stroke in batch.strokes
        ],
        "erasures": [
            {"stroke_ids": list(erasure.stroke_ids), "at_ms": erasure.at_ms}
            for erasure in batch.erasures
        ],
    }


def _batch_from_raw(raw: dict[str, Any]) -> StrokeBatch:
    return StrokeBatch(
        batch_no=raw["batch_no"],
        strokes=tuple(
            Stroke(stroke_id=item["stroke_id"], points=_points(item["points"]))
            for item in raw["strokes"]
        ),
        erasures=tuple(
            Erasure(stroke_ids=tuple(item["stroke_ids"]), at_ms=item["at_ms"])
            for item in raw["erasures"]
        ),
    )


def _deltas(points: tuple[Point, ...]) -> list[list[float | int]]:
    """The first point absolute, the next ones as `(dx, dy, dt, pressure)`."""
    out: list[list[float | int]] = []
    previous: Point | None = None
    for point in points:
        if previous is None:
            out.append([point.x, point.y, point.t_ms, point.pressure])
        else:
            out.append(
                [
                    point.x - previous.x,
                    point.y - previous.y,
                    point.t_ms - previous.t_ms,
                    point.pressure,
                ]
            )
        previous = point
    return out


def _points(raw: list[list[float | int]]) -> tuple[Point, ...]:
    points: list[Point] = []
    for x, y, t, pressure in raw:
        if points:
            last = points[-1]
            points.append(Point(last.x + x, last.y + y, last.t_ms + int(t), float(pressure)))
        else:
            points.append(Point(float(x), float(y), int(t), float(pressure)))
    return tuple(points)


def chunk_max_batches(settings: Settings) -> int:
    """Т-§15.2 «или при переполнении» (D-C-122): a chunk holds the batches the client sends in
    `stroke_chunk_seconds` every `stroke_batch_ms`; more pending is an overflow. No new number:
    the bound comes from the two parameters of Т-§15.2."""
    return math.ceil(settings.stroke_chunk_seconds * 1000 / settings.stroke_batch_ms)


class StrokeJournal:
    """Pending batches of one canvas in one API process."""

    def __init__(
        self,
        storage: ObjectStore,
        *,
        learner_id: UUID,
        session_id: UUID,
        canvas_id: UUID,
        chunk_seconds: int,
        max_batches: int,
    ) -> None:
        self._storage = storage
        self._learner_id = learner_id
        self._session_id = session_id
        self._canvas_id = canvas_id
        self._chunk_seconds = chunk_seconds
        self._max_batches = max_batches
        self._state = _JournalState()

    def add(self, batch: StrokeBatch, *, now: datetime) -> bool:
        """Keep a batch until its chunk is written; `False` for a `batch_no` seen before. Any
        batch, a repeat too, lets a chunk that failed to write be tried again."""
        state = self._state
        state.store_failed = False
        if batch.batch_no in state.seen:
            return False
        state.seen.add(batch.batch_no)
        state.pending.append((batch, now))
        return True

    def written(self, batch_no: int) -> bool:
        """The batch is in a written chunk: a repeat of it is acknowledged again (the first
        `ack` may have been lost on the way to the client)."""
        return batch_no in self._state.written

    def due_in(self, *, now: datetime) -> float | None:
        """Seconds until the oldest pending batch is due for a chunk; `None` with none pending
        or after a failed write, until the client repeats a batch (Т-§20.7)."""
        state = self._state
        if not state.pending or state.store_failed:
            return None
        if len(state.pending) >= self._max_batches:
            return 0.0  # overflow: write now
        due_at = state.pending[0][1] + timedelta(seconds=self._chunk_seconds)
        return max(0.0, (due_at - now).total_seconds())

    async def flush_due(self, *, now: datetime) -> list[int]:
        """Write a chunk if the oldest pending batch waited `chunk_seconds` or `max_batches` are
        pending; the acknowledged batch numbers (empty when nothing was due or the store is
        down)."""
        state = self._state
        if not state.pending:
            return []
        oldest_at = state.pending[0][1]
        overflow = len(state.pending) >= self._max_batches
        if not overflow and now - oldest_at < timedelta(seconds=self._chunk_seconds):
            return []
        return await self.flush()

    async def flush(self) -> list[int]:
        """Write every pending batch now (the socket closes); acknowledged numbers."""
        state = self._state
        if not state.pending:
            return []
        batches = [batch for batch, _ in state.pending]
        key = canvas_chunk_key(
            self._learner_id, self._session_id, self._canvas_id, state.next_chunk
        )
        try:
            await self._storage.put(key, encode_chunk(batches), CHUNK_CONTENT_TYPE)
        except StorageUnavailable:
            state.store_failed = True
            return []  # no ack: the batches stay pending, the client repeats (Т-§20.7)
        state.next_chunk += 1
        # Batches added while the chunk was being written stay pending with their own time.
        state.pending = state.pending[len(batches) :]
        acknowledged = [batch.batch_no for batch in batches]
        state.written.update(acknowledged)
        return acknowledged


@dataclass
class _JournalState:
    seen: set[int] = field(default_factory=set[int])
    written: set[int] = field(default_factory=set[int])
    store_failed: bool = False
    # Pending batches with the time each was added, oldest first.
    pending: list[tuple[StrokeBatch, datetime]] = field(
        default_factory=list[tuple[StrokeBatch, datetime]]
    )
    next_chunk: int = 0
