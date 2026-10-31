"""`GET /v1/learner/stream` — the learner change channel (ТЗ Фазы 3 WP7, `Т-§1.6`).

The channel says only "what changed and where": `projection_advanced` with sequence numbers;
the client fetches data with ordinary GETs. Redis `projector:applied:{learner_id}` drives it;
without Redis (or after the subscription breaks) `ops.projector_offsets` is polled every
`learner_stream_poll_ms`. Silent periods send the `: ping` keepalive (WP6.3)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

import orjson
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError
from sqlalchemy import text

from quack.api.auth import get_current_learner_id
from quack.api.resources import AppResources, get_resources
from quack.api.sse import KEEPALIVE_COMMENT, format_sse, sse_bytes_response
from quack.db.redis import projector_applied_channel
from quack.logging import get_logger

log = get_logger(__name__)

EVENT = "projection_advanced"

_OFFSETS = text(
    "SELECT (SELECT applied_seq FROM ops.projector_offsets WHERE learner_id = :l) AS applied, "
    "(SELECT last_seq FROM core.learner_seq WHERE learner_id = :l) AS last"
)

router = APIRouter(prefix="/v1", tags=["learner"])


async def _offsets(resources: AppResources, learner_id: UUID) -> tuple[int, int]:
    """Current `applied_seq` and `last_seq`; 0 when there is no row. The connection is
    released right after the query."""
    async with resources.session_factory() as session:
        row = (await session.execute(_OFFSETS, {"l": learner_id})).one()
    return int(row.applied or 0), int(row.last or 0)


async def _subscribe(resources: AppResources, learner_id: UUID) -> PubSub | None:
    if resources.redis is None:
        return None
    pubsub = resources.redis.pubsub()
    try:
        await pubsub.subscribe(projector_applied_channel(learner_id))
    except (RedisError, OSError) as exc:
        log.warning("learner_stream_subscribe_failed", learner_id=str(learner_id), error=repr(exc))
        await _close(pubsub)
        return None
    return pubsub


async def _close(pubsub: PubSub) -> None:
    with contextlib.suppress(RedisError, OSError):
        await pubsub.aclose()


def _applied_of(message: dict[str, Any]) -> int | None:
    try:
        value = orjson.loads(message["data"])["applied_seq"]
    except (ValueError, KeyError, TypeError):
        return None
    return value if isinstance(value, int) else None


async def learner_stream(resources: AppResources, learner_id: UUID) -> AsyncIterator[bytes]:
    settings = resources.settings
    keepalive_s = settings.sse_keepalive_s
    poll_s = settings.learner_stream_poll_ms / 1000
    pubsub = await _subscribe(resources, learner_id)
    try:
        applied, last = await _offsets(resources, learner_id)
        yield format_sse(EVENT, {"applied_seq": applied, "last_seq": last})
        sent_at = time.monotonic()
        while True:
            wait_s = max(0.0, min(poll_s, keepalive_s - (time.monotonic() - sent_at)))
            advanced: int | None = None
            if pubsub is not None:
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=wait_s
                    )
                except (RedisError, OSError) as exc:
                    log.warning(
                        "learner_stream_redis_lost", learner_id=str(learner_id), error=repr(exc)
                    )
                    await _close(pubsub)
                    pubsub = None
                    continue
                if message is not None:
                    advanced = _applied_of(message)
            else:
                await asyncio.sleep(wait_s)
                current, _ = await _offsets(resources, learner_id)
                advanced = current if current > applied else None
            if advanced is not None:
                applied = max(applied, advanced)
                yield format_sse(EVENT, {"applied_seq": advanced})
                sent_at = time.monotonic()
            elif time.monotonic() - sent_at >= keepalive_s:
                yield KEEPALIVE_COMMENT
                sent_at = time.monotonic()
    finally:
        if pubsub is not None:
            # A client disconnect cancels the stream; shield so the unsubscribe still runs.
            await asyncio.shield(_close(pubsub))


@router.get("/learner/stream")
async def get_learner_stream(
    learner_id: Annotated[UUID, Depends(get_current_learner_id)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> StreamingResponse:
    """SSE `projection_advanced` (WP7); data are fetched with ordinary GETs."""
    return sse_bytes_response(learner_stream(resources, learner_id))
