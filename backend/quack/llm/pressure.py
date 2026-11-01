"""Pressure flags and circuit keys of the models in Redis (Т-§22.5, Т-§11.9, WP5.3, WP5.5).

Seam S3: `pressured_models` is read by the job runner (stream A) once per claim iteration;
a model with a flag gets no P2/P3 jobs whose roles run on it (`KIND_LLM_ROLES`). Without
Redis nothing is raised and no model counts as pressured.

Key templates are `quack.db.redis.llm_pressure_key` / `llm_circuit_key` (seam S8).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError

from quack.db.redis import llm_circuit_key, llm_pressure_key
from quack.logging import get_logger

type PressureReason = Literal["429", "headers"]

log = get_logger(__name__)

# KEYS[1] flag, ARGV[1] value, ARGV[2] TTL in ms: set only when it extends the flag (WP5.3).
_SET_LONGER_TTL = """
local current = redis.call('PTTL', KEYS[1])
if current < tonumber(ARGV[2]) then
  redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
end
return current
"""


async def pressured_models(redis: Redis | None) -> frozenset[str]:
    """IDs of the models whose `llm:pressure:{model}` flag is set; empty on `None` or error."""
    if redis is None:
        return frozenset()
    prefix = llm_pressure_key("")
    keys: list[bytes | str] = []
    try:
        cursor = 0
        while True:
            cursor, batch = await redis.scan(cursor=cursor, match=f"{prefix}*")
            keys.extend(batch)
            if cursor == 0:
                break
    except RedisError:
        log.warning("pressure_flags_unreadable")
        return frozenset()
    names = (key.decode() if isinstance(key, bytes) else str(key) for key in keys)
    return frozenset(name.removeprefix(prefix) for name in names if name != prefix)


async def pressure_ttl_s(redis: Redis | None, model: str) -> float | None:
    """Seconds left on the pressure flag of `model`; `None` without a flag, without Redis or
    on a Redis error (the teacher admission of WP5.3 waits at most this long)."""
    if redis is None:
        return None
    try:
        ttl_ms = int(await redis.pttl(llm_pressure_key(model)))
    except RedisError:
        return None
    return ttl_ms / 1000 if ttl_ms > 0 else None


async def set_pressure_flag(
    redis: Redis | None, model: str, ttl_s: float, reason: PressureReason, now: datetime
) -> None:
    """Set or extend the flag with one script: the TTL never gets shorter (WP5.3)."""
    if redis is None or ttl_s <= 0:
        return
    ttl_ms = max(1, math.ceil(ttl_s * 1000))
    try:
        await redis.eval(  # type: ignore[misc]  # redis-py types `eval` as sync-or-async
            _SET_LONGER_TTL, 1, llm_pressure_key(model), f"{now.isoformat()} {reason}", ttl_ms
        )
    except RedisError:
        log.warning("pressure_flag_not_set", model=model, reason=reason)


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """`retry-after` as seconds or HTTP date (Т-§11.2); past or unparsable → `None`."""
    if value is None or not value.strip():
        return None
    raw = value.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            moment = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        seconds = (moment - now).total_seconds()
    return seconds if seconds > 0 else None


def _header(headers: Mapping[str, str], name: str) -> float | None:
    for key, value in headers.items():
        if key.lower() == name:
            try:
                return float(value)
            except ValueError:
                return None
    return None


def low_remaining(headers: Mapping[str, str], share: float) -> bool | None:
    """True if remaining requests or tokens are below `share` of their limit in the same
    response; `None` if the response has no `x-ratelimit-*` headers (flag unchanged)."""
    seen = False
    for unit in ("requests", "tokens"):
        limit = _header(headers, f"x-ratelimit-limit-{unit}")
        remaining = _header(headers, f"x-ratelimit-remaining-{unit}")
        if limit is None or remaining is None or limit <= 0:
            continue
        seen = True
        if remaining < share * limit:
            return True
    return False if seen else None


async def circuit_open_in_redis(redis: Redis | None, model: str) -> bool:
    """True if another process opened the circuit of `model` (`llm:circuit:{model}`)."""
    if redis is None:
        return False
    try:
        return bool(await redis.exists(llm_circuit_key(model)))
    except RedisError:
        return False


async def open_circuit_in_redis(
    redis: Redis | None, model: str, ttl_s: float, now: datetime
) -> None:
    """Publish the open circuit of `model` for other processes and `ops status`."""
    if redis is None:
        return
    try:
        await redis.set(llm_circuit_key(model), now.isoformat(), px=max(1, int(ttl_s * 1000)))
    except RedisError:
        log.warning("circuit_key_not_set", model=model)
