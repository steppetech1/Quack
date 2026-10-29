"""Turn lock: Redis `SET NX EX 60`, falling back to a Postgres advisory lock (ТЗ §2.3, Т-§17.3)."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine


def acquire_turn_lock(
    redis: Redis | None, engine: AsyncEngine, session_id: UUID, request_id: str
) -> AbstractAsyncContextManager[None]:
    """Hold the turn lock of `session_id`; raise ApiError TURN_LOCKED (409) if it is taken."""
    raise NotImplementedError
