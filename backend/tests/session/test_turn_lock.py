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


def _ports(redis: Redis, session_id: Any, settings: Settings | None = None) -> ApiTurnPorts:
    return ApiTurnPorts(
        db=None,  # type: ignore[arg-type]
        redis=redis,
        settings=settings or Settings(env="test"),
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


async def test_the_holder_renews_the_lock_while_the_turn_runs(redis: Redis) -> None:
    """A turn can outlast the TTL (model timeouts, retries, tools): the lock must not expire
    under it, or a second process starts a parallel turn of the session. Renewal runs until
    `release`; a lock someone else holds is never extended."""
    session = uuid4()
    ports = _ports(
        redis, session, Settings(env="test", turn_lock_ttl_sec=60, turn_lock_renew_sec=0)
    )
    key = turn_lock_key(session)

    await ports._lock()  # pyright: ignore[reportPrivateUsage]
    await redis.expire(key, 1)  # the TTL nearly ran out
    await asyncio.sleep(0.2)  # the TTL is 1 s: without renewal the lock would be about to go
    assert await redis.ttl(key) > 5

    await redis.set(key, "another-holder", ex=1)  # the lock was lost: not ours any more
    await asyncio.sleep(0.1)
    assert await redis.get(key) in ("another-holder", b"another-holder")
    assert await redis.ttl(key) <= 1

    await redis.delete(key)
    await ports.release()
    assert ports._renewal is None  # pyright: ignore[reportPrivateUsage]
