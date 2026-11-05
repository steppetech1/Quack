"""WP14.4 (Т-§20.7): the Redis turn lock of `ApiTurnPorts` — a second turn of the session is
refused while the first holds it, a foreign holder is not freed, a free lock is retaken."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from quack.config import Settings
from quack.db.redis import turn_lock_key
from quack.session.action_turn import ApiTurnPorts, TurnLockedError

pytest_plugins = ["tests.session.fixtures"]


def _ports(redis: Redis, session_id: Any) -> ApiTurnPorts:
    return ApiTurnPorts(
        db=None,  # type: ignore[arg-type]
        redis=redis,
        settings=Settings(env="test"),
        source=None,  # type: ignore[arg-type]
        learner_id=uuid4(),
        session_id=session_id,
        domain_id=uuid4(),
        recorded=None,  # type: ignore[arg-type]
        action=None,  # type: ignore[arg-type]
        frames=asyncio.Queue(),
    )


async def test_second_turn_is_refused_a_foreign_lock_survives_release(redis: Redis) -> None:
    session = uuid4()
    first, second = _ports(redis, session), _ports(redis, session)

    await first._lock()  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(TurnLockedError):
        await second._lock()  # pyright: ignore[reportPrivateUsage]

    await second.release()  # never took it: the holder's key stays
    assert await redis.get(turn_lock_key(session)) is not None
    await first.release()
    assert await redis.get(turn_lock_key(session)) is None
    await second._lock()  # pyright: ignore[reportPrivateUsage]
    await second.release()
