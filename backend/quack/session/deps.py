"""FastAPI dependencies of the session stream: settings, Redis, auth and turn rate limit."""

from __future__ import annotations

from uuid import UUID

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from quack.api.auth import get_current_learner_id
from quack.api.errors import ApiError
from quack.api.resources import AppResources, get_compute, get_llm
from quack.config import Settings, get_settings
from quack.db.redis import ratelimit_key
from quack.logging import get_logger

log = get_logger(__name__)

TURN_RATE_LIMIT_PER_MINUTE = 20
TURN_RATE_LIMIT_WINDOW_SEC = 60


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
    """20 turns per minute per learner; 429 RATE_LIMITED, fail-open when Redis is down."""
    redis: Redis | None = resources.redis
    if redis is None:
        return
    key = ratelimit_key("turns", str(learner_id))
    try:
        count = int(await redis.incr(key))
        if count == 1:
            await redis.expire(key, TURN_RATE_LIMIT_WINDOW_SEC)
    except (RedisError, OSError) as exc:
        log.warning("turn_rate_limit_fail_open", learner_id=str(learner_id), error=repr(exc))
        return
    if count > TURN_RATE_LIMIT_PER_MINUTE:
        raise ApiError(
            "RATE_LIMITED",
            "too many turns, try again in a minute",
            status_code=429,
            details={"limit": TURN_RATE_LIMIT_PER_MINUTE, "window_sec": TURN_RATE_LIMIT_WINDOW_SEC},
        )
