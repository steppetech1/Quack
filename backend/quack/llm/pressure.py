"""Pressure flags and circuit keys of the models in Redis (Т-§22.5, Т-§11.9, WP5.3, WP5.5).

Seam S3: `pressured_models` is read by the job runner (stream A) once per claim iteration;
a model with a flag gets no P2/P3 jobs whose roles run on it (`KIND_LLM_ROLES`). Without
Redis nothing is raised and no model counts as pressured.

Key templates are `quack.db.redis.llm_pressure_key` / `llm_circuit_key` (seam S8).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from redis.asyncio import Redis

type PressureReason = Literal["429", "headers"]


async def pressured_models(redis: Redis | None) -> frozenset[str]:
    """IDs of the models whose `llm:pressure:{model}` flag is set; empty on `None` or error."""
    raise NotImplementedError


async def set_pressure_flag(
    redis: Redis | None, model: str, ttl_s: float, reason: PressureReason, now: datetime
) -> None:
    """Set or extend the flag with one script: the TTL never gets shorter (WP5.3)."""
    raise NotImplementedError


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """`retry-after` as seconds or HTTP date (Т-§11.2); past or unparsable → `None`."""
    raise NotImplementedError


def low_remaining(headers: Mapping[str, str], share: float) -> bool | None:
    """True if remaining requests or tokens are below `share` of their limit in the same
    response; `None` if the response has no `x-ratelimit-*` headers (flag unchanged)."""
    raise NotImplementedError


async def circuit_open_in_redis(redis: Redis | None, model: str) -> bool:
    """True if another process opened the circuit of `model` (`llm:circuit:{model}`)."""
    raise NotImplementedError


async def open_circuit_in_redis(
    redis: Redis | None, model: str, ttl_s: float, now: datetime
) -> None:
    """Publish the open circuit of `model` for other processes and `ops status`."""
    raise NotImplementedError
