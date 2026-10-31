"""The single rate limiter of the API (ТЗ Фазы 3 WP1.5, §6.3, Т-§19.1).

Fixed 60 s window, key `ratelimit:{scope}:{key}` (С-§6). The counter and its TTL are set by
one Redis script, so a key without TTL cannot appear between two commands; a counter that
somehow has no TTL gets one on the next hit. Redis unavailable → a local in-process counter
with the same limit and window (conflict §2.3 п.3; fail-open is gone).

Dependencies for the routers:
- `enforce_auth_rate_limit`: scope `auth`, key = client IP (register, login);
- `enforce_action_rate_limit`: scope `actions`, key = learner id, for every POST/PATCH/DELETE
  except the heartbeat;
- `check_turn_rate_limit`: scope `turns`, called by the turn endpoint (together with `actions`).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from quack.api.auth import get_current_learner_id
from quack.api.errors import ApiError
from quack.api.resources import AppResources, get_resources
from quack.db.redis import ratelimit_key
from quack.logging import get_logger

log = get_logger(__name__)

# Contract (§6.3): the window is 60 s; the limits are the `*_per_min` parameters of Settings.
RATE_LIMIT_WINDOW_SEC = 60

# INCR and EXPIRE in one atomic step; a key that exists without TTL is repaired.
# Returns {count, ttl}.
_HIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if count == 1 or ttl < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""

# The heartbeat is a keep-alive of an open session, not a learner action (WP1.5).
HEARTBEAT_PATH = "/v1/sessions/{session_id}/heartbeat"
LIMITED_METHODS = frozenset({"POST", "PATCH", "DELETE"})


class LocalWindowCounter:
    """Fixed-window counters in the memory of one process (the Redis-less fallback)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._windows: dict[str, tuple[int, float]] = {}
        self._next_sweep = clock() + RATE_LIMIT_WINDOW_SEC

    def hit(self, key: str) -> tuple[int, int]:
        """Count one request; returns `(count in the window, seconds left in the window)`."""
        now = self._clock()
        if now >= self._next_sweep:
            self._windows = {k: w for k, w in self._windows.items() if w[1] > now}
            self._next_sweep = now + RATE_LIMIT_WINDOW_SEC
        count, expires_at = self._windows.get(key, (0, 0.0))
        if expires_at <= now:
            count, expires_at = 0, now + RATE_LIMIT_WINDOW_SEC
        self._windows[key] = (count + 1, expires_at)
        return count + 1, max(1, round(expires_at - now))

    def size(self) -> int:
        """Number of keys kept (expired ones are dropped lazily, once per window)."""
        return len(self._windows)

    def clear(self) -> None:
        self._windows.clear()


LOCAL_LIMITS = LocalWindowCounter()


async def _redis_hit(redis: Redis, key: str) -> tuple[int, int]:
    count, ttl = await redis.eval(_HIT_SCRIPT, 1, key, RATE_LIMIT_WINDOW_SEC)  # type: ignore[misc]
    return int(count), max(1, int(ttl))


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
    redis_key = ratelimit_key(scope, key)
    hit: tuple[int, int] | None = None
    if redis is not None:
        try:
            hit = await _redis_hit(redis, redis_key)
        except (RedisError, OSError) as exc:
            log.warning("rate_limit_local_fallback", scope=scope, error=repr(exc))
    count, retry_after = hit if hit is not None else local.hit(redis_key)
    if count > limit:
        raise ApiError(
            "RATE_LIMITED",
            "too many requests, try again later",
            status_code=429,
            details={"retry_after_sec": retry_after},
        )


def _client_ip(request: Request) -> str:
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


async def enforce_auth_rate_limit(request: Request) -> None:
    """`auth` scope: requests per client IP of `/v1/auth/register` and `/v1/auth/login`."""
    resources = get_resources(request)
    await check_rate_limit(
        resources.redis,
        "auth",
        _client_ip(request),
        resources.settings.auth_requests_per_ip_per_min,
    )


async def enforce_action_rate_limit(
    request: Request, learner_id: Annotated[UUID, Depends(get_current_learner_id)]
) -> None:
    """`actions` scope: POST/PATCH/DELETE of an authenticated learner, except the heartbeat.
    Depends on the auth dependency, so an unauthenticated request is refused with 401 first."""
    if request.method not in LIMITED_METHODS:
        return
    route = request.scope.get("route")
    if getattr(route, "path", None) == HEARTBEAT_PATH:
        return
    resources = get_resources(request)
    await check_rate_limit(
        resources.redis, "actions", str(learner_id), resources.settings.learner_actions_per_min
    )


async def check_turn_rate_limit(resources: AppResources, learner_id: UUID) -> None:
    """`turns` scope: turns of one learner per minute (on top of `actions`)."""
    await check_rate_limit(
        resources.redis, "turns", str(learner_id), resources.settings.turns_per_min
    )
