"""FastAPI dependencies of the session stream: settings, Redis, auth and the turn limit."""

from __future__ import annotations

from uuid import UUID

from fastapi import Request
from redis.asyncio import Redis

from quack.api.auth import get_current_learner_id
from quack.api.ratelimit import check_turn_rate_limit
from quack.api.resources import AppResources, get_compute, get_llm
from quack.config import Settings, get_settings


def get_settings_dep(request: Request) -> Settings:
    settings: Settings | None = getattr(request.app.state, "settings", None)
    return settings or get_settings()


def get_redis(request: Request) -> Redis | None:
    resources: AppResources | None = getattr(request.app.state, "resources", None)
    return None if resources is None else resources.redis


# ТЗ Фазы 3 WP1.2, WP1.3 (§7.3 case 1): the stream's own JWT check and per-app gateway and
# compute factories are replaced by the shared ones. The names stay as aliases of the same
# functions, so `quack/session/router.py` and `app.dependency_overrides` keep working.
current_learner_id = get_current_learner_id
get_llm_gateway = get_llm
get_compute_client = get_compute


async def enforce_turn_rate_limit(
    resources: AppResources, settings: Settings, learner_id: UUID
) -> None:
    """`turns_per_min` turns per learner (WP1.5): the single limiter of `quack.api.ratelimit`,
    a local counter when Redis is down. `settings` is kept for the callers' signature."""
    await check_turn_rate_limit(resources, learner_id)
