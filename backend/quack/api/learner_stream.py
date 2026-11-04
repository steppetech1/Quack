"""`GET /v1/learner/stream` — the learner change channel (ТЗ Фазы 3 WP7, `Т-§1.6`).

The channel says only "what changed and where": `projection_advanced` with sequence numbers;
the client fetches data with ordinary GETs. Redis `projector:applied:{learner_id}` drives it;
without Redis (or after the subscription breaks) `ops.projector_offsets` is polled every
`learner_stream_poll_ms`. Silent periods send the `: ping` keepalive (WP6.3).

ТЗ Ф4 §4.8, D-A-181/D-A-190 (#188): the projector `rules@3` publishes
`{"applied_seq", "topics"}` after a batch, and `projection_advanced` carries the topics that
changed (`Т-§2.4`); the marks are the Phase 4 ones — `ops.projector_offsets.seq` and
`core.learners.seq`. Polling knows no topics and sends `applied_seq` only.

ТЗ Ф4 §4.8 adds kinds read from the Phase 4 event log: `artifact_updated {artifact_id}` and
`material_parsed {material_id}` — new `artifact.updated` / `material.parsed` after the stream
opened — and `verdict_revised {answer_seq}` for the judged answers of a task whose key was
disputed (`task.key_disputed`, D-B-167). They are read on the signal `learner:{id}` that
`append_events` publishes after commit, or by the same polling without Redis (D-C-130).

`jobs_changed {session_id}` (the plaque, `Т-§13.8`) is not an event of the log: the queue
publishes `{"kind": "jobs_changed", "session_id"}` on the same `learner:{id}` after commit and the
stream forwards it; without Redis there is none — the client reads `GET /v1/sessions/{id}/jobs`
after reconnecting (D-C-141)."""

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

from quack.api.auth import get_current_learner_id
from quack.api.resources import AppResources, get_resources
from quack.api.sse import KEEPALIVE_COMMENT, format_sse, sse_bytes_response
from quack.db.redis import learner_signal_channel, projector_applied_channel
from quack.logging import get_logger

log = get_logger(__name__)

EVENT = "projection_advanced"
JOBS_CHANGED = "jobs_changed"

_OFFSETS = (
    "SELECT (SELECT seq FROM ops.projector_offsets WHERE learner_id = %(learner_id)s) AS applied, "
    "(SELECT seq FROM core.learners WHERE learner_id = %(learner_id)s) AS last"
)

# ТЗ Ф4 §4.8: event type → (message kind, the payload field it names).
CHANGE_EVENTS: dict[str, tuple[str, str]] = {
    "artifact.updated": ("artifact_updated", "artifact_id"),
    "material.parsed": ("material_parsed", "material_id"),
}
_LAST_SEQ = "SELECT coalesce(max(seq), 0) AS seq FROM core.events WHERE learner_id = %(learner_id)s"
# Т-§5.6, D-B-167: a revision of the key — `task.key_disputed {task_id}`; every answer of the
# task that already has a verdict gets `verdict_revised {answer_seq}`.
KEY_DISPUTED = "task.key_disputed"
_CHANGES = (
    "SELECT seq, type, payload FROM core.events WHERE learner_id = %(learner_id)s "
    "AND seq > %(after)s AND type = ANY(%(types)s) ORDER BY seq"
)
_JUDGED_ANSWERS = (
    "SELECT DISTINCT a.seq AS answer_seq FROM core.events a JOIN core.events v "
    "ON v.learner_id = a.learner_id AND v.type = 'verdict.recorded' "
    "AND (v.payload ->> 'answer_seq')::bigint = a.seq "
    "WHERE a.learner_id = %(learner_id)s AND a.type = 'answer.submitted' "
    "AND a.payload ->> 'task_id' = %(task_id)s ORDER BY a.seq"
)

router = APIRouter(prefix="/v1", tags=["learner"])


async def _offsets(resources: AppResources, learner_id: UUID) -> tuple[int, int]:
    """Current `applied_seq` and `last_seq` of the Phase 4 log; 0 when there is no row."""
    row = await resources.db.fetch_one(_OFFSETS, {"learner_id": learner_id})
    if row is None:
        return 0, 0
    return int(row["applied"] or 0), int(row["last"] or 0)


async def _last_seq(resources: AppResources, learner_id: UUID) -> int:
    row = await resources.db.fetch_one(_LAST_SEQ, {"learner_id": learner_id})
    return int(row["seq"]) if row is not None else 0


async def _changes(
    resources: AppResources, learner_id: UUID, after: int
) -> tuple[list[bytes], int]:
    """§4.8 messages of the change events after `after`, and the new cursor."""
    rows = await resources.db.fetch_all(
        _CHANGES,
        {"learner_id": learner_id, "after": after, "types": [*CHANGE_EVENTS, KEY_DISPUTED]},
    )
    messages: list[bytes] = []
    for row in rows:
        after = max(after, int(row["seq"]))
        if row["type"] == KEY_DISPUTED:
            judged = await resources.db.fetch_all(
                _JUDGED_ANSWERS,
                {"learner_id": learner_id, "task_id": row["payload"]["task_id"]},
            )
            messages.extend(
                format_sse("verdict_revised", {"answer_seq": int(j["answer_seq"])}) for j in judged
            )
            continue
        kind, field = CHANGE_EVENTS[row["type"]]
        messages.append(format_sse(kind, {field: row["payload"][field]}))
    return messages, after


async def _subscribe(resources: AppResources, learner_id: UUID) -> PubSub | None:
    if resources.redis is None:
        return None
    pubsub = resources.redis.pubsub()
    try:
        await pubsub.subscribe(
            projector_applied_channel(learner_id), learner_signal_channel(learner_id)
        )
    except (RedisError, OSError) as exc:
        log.warning("learner_stream_subscribe_failed", learner_id=str(learner_id), error=repr(exc))
        await _close(pubsub)
        return None
    return pubsub


async def _close(pubsub: PubSub) -> None:
    with contextlib.suppress(RedisError, OSError):
        await pubsub.aclose()


def _jobs_changed(message: dict[str, Any]) -> bytes | None:
    """D-C-141: `{"kind": "jobs_changed", "session_id"}` on `learner:{id}` → the §4.8 message;
    None for the plain signal of `append_events` (a sequence number)."""
    try:
        data = orjson.loads(message["data"])
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("kind") != JOBS_CHANGED:
        return None
    return format_sse(JOBS_CHANGED, {"session_id": data.get("session_id")})


def _applied_of(message: dict[str, Any]) -> dict[str, Any] | None:
    """`projection_advanced` data from the projector's message: `applied_seq` and, when the
    projector names them, the `topics` that changed (D-A-190)."""
    try:
        data = orjson.loads(message["data"])
        value = data["applied_seq"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(value, int):
        return None
    advanced: dict[str, Any] = {"applied_seq": value}
    topics = data.get("topics")
    if isinstance(topics, list):
        advanced["topics"] = [str(t) for t in topics]
    return advanced


async def learner_stream(resources: AppResources, learner_id: UUID) -> AsyncIterator[bytes]:
    settings = resources.settings
    keepalive_s = settings.sse_keepalive_s
    poll_s = settings.learner_stream_poll_ms / 1000
    pubsub = await _subscribe(resources, learner_id)
    signal = learner_signal_channel(learner_id)
    try:
        cursor = await _last_seq(resources, learner_id)
        applied, last = await _offsets(resources, learner_id)
        yield format_sse(EVENT, {"applied_seq": applied, "last_seq": last})
        sent_at = time.monotonic()
        while True:
            wait_s = max(0.0, min(poll_s, keepalive_s - (time.monotonic() - sent_at)))
            advanced: dict[str, Any] | None = None
            changed = False
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
                if message is not None and message.get("channel") == signal:
                    if (plaque := _jobs_changed(message)) is not None:
                        yield plaque
                        sent_at = time.monotonic()
                        continue
                    changed = True
                elif message is not None:
                    advanced = _applied_of(message)
            else:
                await asyncio.sleep(wait_s)
                current, _ = await _offsets(resources, learner_id)
                advanced = {"applied_seq": current} if current > applied else None
                changed = True
            if changed:
                messages, cursor = await _changes(resources, learner_id, cursor)
                for message_bytes in messages:
                    yield message_bytes
                if messages:
                    sent_at = time.monotonic()
            if advanced is not None:
                applied = max(applied, int(advanced["applied_seq"]))
                yield format_sse(EVENT, advanced)
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
    """SSE `projection_advanced` (WP7), `artifact_updated`, `material_parsed`, `verdict_revised`,
    `jobs_changed` (ТЗ Ф4 §4.8); data are fetched with ordinary GETs."""
    return sse_bytes_response(learner_stream(resources, learner_id))
