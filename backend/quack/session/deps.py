"""FastAPI dependencies: Bearer JWT, tombstone check and turn rate limit (ТЗ §2.1)."""

from __future__ import annotations

from uuid import UUID

import jwt
from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text

from quack.api.app import AppResources, get_resources
from quack.api.errors import ApiError
from quack.compute_client.client import ComputeClient
from quack.config import Settings, get_settings
from quack.db.redis import ratelimit_key
from quack.llm.gateway import LLMGateway
from quack.llm.provider import HttpLLMProvider
from quack.logging import get_logger

log = get_logger(__name__)

JWT_ALGORITHM = "HS256"
DEV_JWT_SECRET = "quack-dev-jwt-secret"
TURN_RATE_LIMIT_PER_MINUTE = 20
TURN_RATE_LIMIT_WINDOW_SEC = 60


def get_settings_dep(request: Request) -> Settings:
    settings: Settings | None = getattr(request.app.state, "settings", None)
    return settings or get_settings()


def get_redis(request: Request) -> Redis | None:
    resources: AppResources | None = getattr(request.app.state, "resources", None)
    return None if resources is None else resources.redis


def get_llm_gateway(request: Request) -> LLMGateway:
    gateway: LLMGateway | None = getattr(request.app.state, "llm_gateway", None)
    if gateway is None:
        settings = get_settings_dep(request)
        provider = HttpLLMProvider(
            settings.llm_api_base_url, settings.llm_api_key.get_secret_value()
        )
        gateway = LLMGateway(get_resources(request).session_factory, provider, settings)
        request.app.state.llm_gateway = gateway
    return gateway


def get_compute_client(request: Request) -> ComputeClient:
    compute: ComputeClient | None = getattr(request.app.state, "compute_client", None)
    if compute is None:
        settings = get_settings_dep(request)
        compute = ComputeClient(settings.compute_url, settings.compute_timeout_sec)
        request.app.state.compute_client = compute
    return compute


def _unauthorized(message: str) -> ApiError:
    return ApiError("UNAUTHORIZED", message, status_code=401)


async def current_learner_id(request: Request) -> UUID:
    """Decode `Authorization: Bearer` (HS256, `sub` -> learner_id); 401 for tombstoned learners."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("missing bearer token")
    settings = get_settings_dep(request)
    secret = settings.jwt_secret.get_secret_value() or DEV_JWT_SECRET
    try:
        payload = jwt.decode(token.strip(), secret, algorithms=[JWT_ALGORITHM])
        learner_id = UUID(str(payload["sub"]))
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise _unauthorized("invalid token") from exc
    async with get_resources(request).session_factory() as session:
        tombstoned = await session.scalar(
            text("SELECT 1 FROM ops.deletion_tombstones WHERE learner_id = :l LIMIT 1"),
            {"l": learner_id},
        )
    if tombstoned is not None:
        raise _unauthorized("learner is deleted")
    return learner_id


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
