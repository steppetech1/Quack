"""The session routes and the inactivity sweep over `quack.session.sessions` (ТЗ Ф4 WP13.10).

The library `sessions.py` writes `session.started`/`session.ended` and the row of
`content.sessions` on `Db`; this module maps its errors to the codes of §4.5, keeps the
presence of the learner in Redis (advisory: the session itself lives in Postgres, §4.2) and
holds the handler of the periodic job `session.sweep_inactive` (D-B-210).
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from quack.api.errors import ApiError
from quack.api.resources import AppResources
from quack.config import Settings
from quack.db.redis import presence_key
from quack.events.errors import IdempotencyConflictError
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.session import sessions
from quack.session.schemas import HeartbeatResponse, SessionDetail, SessionListResponse
from quack.session.sessions import SessionConflictError, SessionView, StartSession

_RELEASE_PRESENCE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Open sessions without activity for `session_inactivity_timeout_min`, of every learner
# (Т-§2.2): the sweep serves the whole system, like the job queue.
_STALE = (
    "SELECT learner_id, session_id FROM content.sessions WHERE status = 'active' "
    "AND last_activity_at < now() - make_interval(mins => %(minutes)s) "
    "ORDER BY learner_id, session_id"
)


def _not_found(exc: LookupError) -> ApiError:
    return ApiError("NOT_FOUND", str(exc), status_code=404)


async def _set_presence(
    resources: AppResources, settings: Settings, learner_id: UUID, session_id: UUID
) -> None:
    # No Redis → no presence (§4.2, D-C-005).
    if resources.redis is None:
        return
    with contextlib.suppress(RedisError, OSError):
        await resources.redis.set(
            presence_key(learner_id), str(session_id), ex=settings.heartbeat_ttl_sec
        )


async def _release_presence(redis: Redis | None, learner_id: UUID, session_id: UUID) -> None:
    """Delete the presence key only if it still holds `session_id` (atomic compare-and-delete)."""
    if redis is None:
        return
    with contextlib.suppress(RedisError, OSError):
        await redis.eval(  # type: ignore[misc]
            _RELEASE_PRESENCE_LUA, 1, presence_key(learner_id), str(session_id)
        )


async def start_session(
    resources: AppResources, settings: Settings, learner_id: UUID, body: StartSession
) -> SessionView:
    """`POST /v1/sessions`: a domain of another learner or none — `404`; the key of another
    start — `409 IDEMPOTENCY_CONFLICT`."""
    try:
        started = await sessions.start_session(resources.db, learner_id=learner_id, body=body)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except (SessionConflictError, IdempotencyConflictError) as exc:
        raise ApiError("IDEMPOTENCY_CONFLICT", str(exc), status_code=409) from exc
    await _set_presence(resources, settings, learner_id, started.session_id)
    return started


async def get_active_session(resources: AppResources, learner_id: UUID) -> SessionView | None:
    return await sessions.active_session(resources.db, learner_id=learner_id)


async def list_sessions(
    resources: AppResources, learner_id: UUID, domain_id: UUID | None, limit: int
) -> SessionListResponse:
    """Session history, newest first."""
    found = await sessions.list_sessions(
        resources.db, learner_id=learner_id, domain_id=domain_id, limit=limit
    )
    return SessionListResponse(sessions=found)


async def get_session(resources: AppResources, learner_id: UUID, session_id: UUID) -> SessionDetail:
    db = resources.db
    try:
        view = await sessions.get_session(db, learner_id=learner_id, session_id=session_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    feed = await sessions.session_feed(db, learner_id=learner_id, session_id=session_id)
    return SessionDetail(**view.model_dump(), events=feed)


async def heartbeat(
    resources: AppResources, settings: Settings, learner_id: UUID, session_id: UUID
) -> HeartbeatResponse:
    """`POST …/heartbeat`: activity of an open session; an ended or foreign one — `404`."""
    try:
        await sessions.heartbeat(resources.db, learner_id=learner_id, session_id=session_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    await _set_presence(resources, settings, learner_id, session_id)
    return HeartbeatResponse(status="ok", ttl_sec=settings.heartbeat_ttl_sec)


async def end_session(resources: AppResources, learner_id: UUID, session_id: UUID) -> SessionView:
    """`POST …/end`: `session.ended {reason: learner}`; a repeat changes nothing."""
    try:
        ended = await sessions.end_session(
            resources.db, learner_id=learner_id, session_id=session_id
        )
    except LookupError as exc:
        raise _not_found(exc) from exc
    await _release_presence(resources.redis, learner_id, session_id)
    return ended


@register_job_handler("session.sweep_inactive")
async def sweep_inactive_sessions(job: ClaimedJob, ctx: JobContext) -> None:
    """Close the open sessions without activity (`session.ended {reason: inactive}`); a session
    the learner comes back to is reopened by the first action (WP13.10)."""
    _ = job
    stale = await ctx.db.fetch_all(_STALE, {"minutes": ctx.settings.session_inactivity_timeout_min})
    for row in stale:
        learner_id, session_id = row["learner_id"], row["session_id"]
        ended = await sessions.end_if_inactive(
            ctx.db,
            learner_id=learner_id,
            session_id=session_id,
            settings=ctx.settings,
            now=datetime.now(UTC),
        )
        if ended:
            # WP2.1: the process Redis of the context (None = no Redis, §4.2), never a new client.
            await _release_presence(ctx.redis, learner_id, session_id)
