"""`GET /v1/sessions/{id}/events`: SSE relay of the Redis channel `session:events:{id}`."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import anyio
import orjson
from redis.exceptions import RedisError
from sqlalchemy import text

from quack.api.app import AppResources
from quack.api.errors import ApiError
from quack.db.redis import session_events_channel
from quack.logging import get_logger

log = get_logger(__name__)

# How long one `get_message` waits before looping; bounds nothing but idle wake-ups.
_POLL_TIMEOUT_SEC = 1.0


async def stream_session_events(
    resources: AppResources, learner_id: UUID, session_id: UUID
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Check ownership (404 NOT_FOUND), then return the relay of published session events.

    The check runs before the response starts, so the 404 reaches the client as JSON.
    Messages `{"event", "data"}` (`extractor_done`, `cheatsheet_ready`) are forwarded as-is.
    """
    async with resources.session_factory() as session:
        owned = (
            await session.execute(
                text("SELECT 1 FROM core.sessions WHERE id = :id AND learner_id = :l"),
                {"id": session_id, "l": learner_id},
            )
        ).one_or_none()
    if owned is None:
        raise ApiError("NOT_FOUND", "session not found", status_code=404)
    return _relay(resources, session_id)


async def _relay(
    resources: AppResources, session_id: UUID
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    redis = resources.redis
    if redis is None:
        return
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(session_events_channel(session_id))
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_POLL_TIMEOUT_SEC
            )
            if message is None:
                continue
            try:
                body = orjson.loads(message["data"])
                event, data = str(body["event"]), body["data"]
            except (ValueError, KeyError, TypeError):
                continue
            if isinstance(data, dict):
                yield event, data
    except (RedisError, OSError) as exc:
        log.warning("session_events_stream_ended", session_id=str(session_id), error=repr(exc))
    finally:
        # The client disconnect cancels the stream; shield so the unsubscribe still runs.
        with anyio.CancelScope(shield=True):
            with contextlib.suppress(RedisError, OSError):
                await pubsub.aclose()
