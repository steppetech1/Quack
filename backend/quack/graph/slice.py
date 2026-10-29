"""read_consistent_slice (spec §8.2, Т-§2.6): watermark-controlled reads with lag fallback.

The slice is read between two reads of Learner.appliedSeq; equal values mean the slice is
consistent (every projector commit moves the watermark). If the projector lags behind
`min_seq`, the reader waits for `projector:applied:{learner_id}` up to `timeout_ms` and then
answers anyway with `lag_degraded = True` (law 9: the hot path never blocks on background).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import orjson
from redis.asyncio import Redis

from quack.db.postgres import SessionFactory
from quack.db.redis import projector_applied_channel
from quack.events.reader import last_seq
from quack.logging import get_logger

log = get_logger(__name__)

# Fallback polling interval when Redis is unavailable (Т-§17.3).
POLL_INTERVAL_SEC = 0.1


@dataclass(frozen=True)
class ConsistentSlice[T]:
    data: T
    applied_seq: int
    min_seq: int
    lag_degraded: bool
    stable: bool


@dataclass(frozen=True)
class SliceDeps:
    session_factory: SessionFactory
    redis: Redis | None
    applied_seq_reader: Callable[[UUID], Awaitable[int]]
    snapshot_read_retries: int = 3


async def _watermarked_read[T](
    learner_id: UUID, loader_fn: Callable[[], Awaitable[T]], deps: SliceDeps
) -> tuple[T, int, bool]:
    attempts = max(1, deps.snapshot_read_retries)
    for attempt in range(1, attempts + 1):
        before = await deps.applied_seq_reader(learner_id)
        data = await loader_fn()
        after = await deps.applied_seq_reader(learner_id)
        if before == after or attempt == attempts:
            return data, after, before == after
    raise AssertionError("unreachable")


async def _wait_redis(
    redis: Redis, learner_id: UUID, min_seq: int, deps: SliceDeps, deadline: float
) -> None:
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(projector_applied_channel(learner_id))
        # The projector may have committed between the first read and the subscription.
        if await deps.applied_seq_reader(learner_id) >= min_seq:
            return
        while (remaining := deadline - time.monotonic()) > 0:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=remaining)
            if message is None:
                continue
            try:
                applied = int(orjson.loads(message["data"])["applied_seq"])
            except (ValueError, KeyError, TypeError):
                continue
            if applied >= min_seq:
                return
    finally:
        await pubsub.aclose()


async def _wait_poll(learner_id: UUID, min_seq: int, deps: SliceDeps, deadline: float) -> None:
    while time.monotonic() < deadline:
        if await deps.applied_seq_reader(learner_id) >= min_seq:
            return
        await asyncio.sleep(min(POLL_INTERVAL_SEC, max(0.0, deadline - time.monotonic())))


async def read_consistent_slice[T](
    learner_id: UUID,
    min_seq: int | None,
    loader_fn: Callable[[], Awaitable[T]],
    timeout_ms: int = 2000,
    *,
    deps: SliceDeps,
) -> ConsistentSlice[T]:
    if min_seq is None:
        async with deps.session_factory() as session:
            min_seq = await last_seq(session, learner_id)
    data, applied, stable = await _watermarked_read(learner_id, loader_fn, deps)
    if applied >= min_seq:
        return ConsistentSlice(data, applied, min_seq, False, stable)

    deadline = time.monotonic() + timeout_ms / 1000
    if deps.redis is not None:
        try:
            await _wait_redis(deps.redis, learner_id, min_seq, deps, deadline)
        except Exception:
            log.warning("projector_applied_subscribe_failed", learner_id=str(learner_id))
            await _wait_poll(learner_id, min_seq, deps, deadline)
    else:
        await _wait_poll(learner_id, min_seq, deps, deadline)

    data, applied, stable = await _watermarked_read(learner_id, loader_fn, deps)
    return ConsistentSlice(data, applied, min_seq, applied < min_seq, stable)
