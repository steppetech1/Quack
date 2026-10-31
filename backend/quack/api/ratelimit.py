"""The single rate limiter of the API (ТЗ Фазы 3 WP1.5, §6.3, Т-§19.1).

Fixed 60 s window, key `ratelimit:{scope}:{key}` (С-§6). Redis is unavailable → a local
in-process counter with the same limit and window (conflict §2.3 п.3; fail-open is gone).
"""

from __future__ import annotations

import time
from collections.abc import Callable

from redis.asyncio import Redis

RATE_LIMIT_WINDOW_SEC = 60


class LocalWindowCounter:
    """Fixed-window counters in the memory of one process (the Redis-less fallback)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    def hit(self, key: str) -> tuple[int, int]:
        """Count one request; returns `(count in the window, seconds left in the window)`."""
        raise NotImplementedError

    def size(self) -> int:
        """Number of keys kept (expired ones are dropped lazily)."""
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


LOCAL_LIMITS = LocalWindowCounter()


async def check_rate_limit(
    redis: Redis | None,
    scope: str,
    key: str,
    limit: int,
    *,
    local: LocalWindowCounter = LOCAL_LIMITS,
) -> None:
    """Count one request in `ratelimit:{scope}:{key}`; `ApiError(RATE_LIMITED, 429)` with
    `details.retry_after_sec` when the count is above `limit`."""
    raise NotImplementedError
