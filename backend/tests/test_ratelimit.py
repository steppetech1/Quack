"""The single rate limiter `quack.api.ratelimit` (ТЗ Фазы 3 WP1.5, §6.3).

Fixed 60 s window, key `ratelimit:{scope}:{key}`, counter and TTL set by one Redis operation,
a local in-process counter with the same limit and window when Redis is unavailable.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from quack.api.errors import ApiError
from quack.api.ratelimit import RATE_LIMIT_WINDOW_SEC, LocalWindowCounter, check_rate_limit
from quack.db.redis import ratelimit_key
from tests.support.broken import BrokenRedis


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _blocked(redis: Any, scope: str, key: str, limit: int) -> int:
    """`check_rate_limit` must refuse with 429 RATE_LIMITED; returns `retry_after_sec`."""
    with pytest.raises(ApiError) as caught:
        await check_rate_limit(redis, scope, key, limit)
    error = caught.value
    assert error.status_code == 429
    assert error.code == "RATE_LIMITED"
    retry_after = error.details["retry_after_sec"]
    assert isinstance(retry_after, int)
    assert 0 < retry_after <= RATE_LIMIT_WINDOW_SEC
    return retry_after


def test_window_is_sixty_seconds() -> None:
    assert RATE_LIMIT_WINDOW_SEC == 60


def test_local_counter_fixed_window() -> None:
    clock = FakeClock()
    counter = LocalWindowCounter(clock)
    assert counter.hit("k") == (1, RATE_LIMIT_WINDOW_SEC)
    clock.now += 10
    assert counter.hit("k") == (2, RATE_LIMIT_WINDOW_SEC - 10)
    assert counter.hit("other")[0] == 1
    clock.now += RATE_LIMIT_WINDOW_SEC  # the window of "k" has ended
    assert counter.hit("k")[0] == 1


def test_local_counter_drops_expired_keys() -> None:
    clock = FakeClock()
    counter = LocalWindowCounter(clock)
    for i in range(5):
        counter.hit(f"k{i}")
    clock.now += 2 * RATE_LIMIT_WINDOW_SEC
    counter.hit("fresh")
    assert counter.size() == 1


async def test_redis_counts_and_blocks_over_the_limit(redis: Redis) -> None:
    key = str(uuid4())
    for _ in range(3):
        await check_rate_limit(redis, "actions", key, 3)
    retry_after = await _blocked(redis, "actions", key, 3)
    ttl = await redis.ttl(ratelimit_key("actions", key))
    assert 0 < ttl <= RATE_LIMIT_WINDOW_SEC
    assert ttl <= retry_after <= ttl + 1


async def test_redis_key_always_gets_a_ttl(redis: Redis) -> None:
    """A counter left without TTL (for example by a failure between INCR and EXPIRE of an
    older implementation) is repaired by the next hit: a key without TTL cannot persist."""
    key = str(uuid4())
    redis_key = ratelimit_key("turns", key)
    await redis.set(redis_key, 1)
    assert await redis.ttl(redis_key) == -1
    await check_rate_limit(redis, "turns", key, 5)
    assert 0 < await redis.ttl(redis_key) <= RATE_LIMIT_WINDOW_SEC
    assert int(await redis.get(redis_key) or 0) == 2


async def test_redis_counter_is_atomic_under_concurrency(redis: Redis) -> None:
    key = str(uuid4())
    results = await asyncio.gather(
        *(check_rate_limit(redis, "actions", key, 10) for _ in range(30)),
        return_exceptions=True,
    )
    assert sum(r is None for r in results) == 10
    assert sum(isinstance(r, ApiError) for r in results) == 20
    assert await redis.ttl(ratelimit_key("actions", key)) > 0


@pytest.mark.parametrize("broken", [BrokenRedis(), None], ids=["redis-down", "no-redis"])
async def test_local_fallback_keeps_limit_and_window(broken: Any) -> None:
    key = str(uuid4())
    for _ in range(4):
        await check_rate_limit(broken, "auth", key, 4)
    await _blocked(broken, "auth", key, 4)
    await check_rate_limit(broken, "auth", str(uuid4()), 4)  # another key: own counter
    await check_rate_limit(broken, "actions", key, 4)  # another scope: own counter
