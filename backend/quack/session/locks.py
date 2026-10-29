"""Turn lock: Redis `SET NX EX 60`, falling back to a Postgres advisory lock (ТЗ §2.3, Т-§17.3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.errors import ApiError
from quack.db.locks import LockKeyspace, advisory_lock
from quack.db.redis import turn_lock_key
from quack.logging import get_logger

log = get_logger(__name__)

TURN_LOCK_TTL_SEC = 60

_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _turn_locked() -> ApiError:
    return ApiError("TURN_LOCKED", "turn already in progress", status_code=409)


@asynccontextmanager
async def acquire_turn_lock(
    redis: Redis | None,
    engine: AsyncEngine,
    session_id: UUID,
    request_id: str | None = None,
) -> AsyncIterator[None]:
    """Hold the turn lock of `session_id`; raise ApiError TURN_LOCKED (409) if it is taken."""
    token = request_id or str(uuid4())
    key = turn_lock_key(session_id)

    if redis is not None:
        try:
            acquired = await redis.set(key, token, nx=True, ex=TURN_LOCK_TTL_SEC)
        except (RedisError, OSError) as exc:
            log.warning(
                "turn_lock_redis_degraded",
                session_id=str(session_id),
                error=repr(exc),
            )
        else:
            if not acquired:
                raise _turn_locked()
            try:
                yield
            finally:
                try:
                    await redis.eval(_RELEASE_LUA, 1, key, token)  # type: ignore[misc]
                except (RedisError, OSError) as exc:
                    log.warning(
                        "turn_lock_redis_release_failed",
                        session_id=str(session_id),
                        error=repr(exc),
                    )
            return

    async with advisory_lock(engine, LockKeyspace.SESSION_TURN, session_id, wait=False) as acquired:
        if not acquired:
            raise _turn_locked()
        yield
