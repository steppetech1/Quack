"""FastAPI dependencies of Stream 3 and resources of its workers.

`get_current_learner_id` follows the shared JWT contract of ТЗ Потока 1 §2.1 п.2 and checks
`ops.deletion_tombstones` (ТЗ §1.2). Tests replace `get_llm_gateway` / `get_compute_client`
through `app.dependency_overrides`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Header, Request
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text

from quack.api.app import get_resources
from quack.api.errors import ApiError
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db.neo4j import create_driver
from quack.db.postgres import SessionFactory
from quack.db.redis import create_redis
from quack.jobs.runner import JobContext
from quack.llm.gateway import LLMGateway
from quack.llm.provider import HttpLLMProvider

# ТЗ Потока 1 §2.1 п.2: with an empty `jwt_secret` (dev/test only, prod requires it) this signs.
DEV_JWT_SECRET = "quack-dev-jwt-secret"
JWT_ALGORITHM = "HS256"
BEARER_PREFIX = "Bearer "


def _unauthorized(message: str) -> ApiError:
    return ApiError("UNAUTHORIZED", message, status_code=401)


async def get_current_learner_id(
    request: Request, authorization: str | None = Header(None)
) -> UUID:
    """`Authorization: Bearer <HS256 JWT>` → `UUID(sub)`; no tombstone; else 401 UNAUTHORIZED."""
    if authorization is None or not authorization.startswith(BEARER_PREFIX):
        raise _unauthorized("missing bearer token")
    settings: Settings = request.app.state.settings
    secret = settings.jwt_secret.get_secret_value() or DEV_JWT_SECRET
    try:
        payload = jwt.decode(
            authorization.removeprefix(BEARER_PREFIX).strip(), secret, algorithms=[JWT_ALGORITHM]
        )
        learner_id = UUID(str(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        raise _unauthorized("invalid or expired token") from exc
    async with get_resources(request).session_factory() as session:
        tombstone = (
            await session.execute(
                text("SELECT 1 FROM ops.deletion_tombstones WHERE learner_id = :l"),
                {"l": learner_id},
            )
        ).first()
    if tombstone is not None:
        raise _unauthorized("learner is deleted")
    return learner_id


def _provider(settings: Settings) -> HttpLLMProvider:
    return HttpLLMProvider(settings.llm_api_base_url, settings.llm_api_key.get_secret_value())


def get_llm_gateway(request: Request) -> LLMGateway:
    """One `LLMGateway` per application."""
    gateway: LLMGateway | None = getattr(request.app.state, "llm_gateway", None)
    if gateway is None:
        settings: Settings = request.app.state.settings
        gateway = LLMGateway(get_resources(request).session_factory, _provider(settings), settings)
        request.app.state.llm_gateway = gateway
    return gateway


def get_compute_client(request: Request) -> ComputeClient:
    """One `ComputeClient` per application."""
    compute: ComputeClient | None = getattr(request.app.state, "compute_client", None)
    if compute is None:
        settings: Settings = request.app.state.settings
        compute = ComputeClient(settings.compute_url, settings.compute_timeout_sec)
        request.app.state.compute_client = compute
    return compute


@dataclass(frozen=True)
class WorkerDeps:
    """What Stream 3 job handlers need beyond `JobContext` (it gives only settings and the
    Postgres session factory)."""

    settings: Settings
    session_factory: SessionFactory
    gateway: LLMGateway
    neo4j: AsyncDriver
    redis: Redis | None


@asynccontextmanager
async def build_worker_deps(ctx: JobContext) -> AsyncIterator[WorkerDeps]:
    """Create the Neo4j driver, Redis and `LLMGateway` from `ctx.settings`; close them after."""
    driver = create_driver(ctx.settings)
    redis = create_redis(ctx.settings)
    provider = _provider(ctx.settings)
    try:
        yield WorkerDeps(
            settings=ctx.settings,
            session_factory=ctx.session_factory,
            gateway=LLMGateway(ctx.session_factory, provider, ctx.settings),
            neo4j=driver,
            redis=redis,
        )
    finally:
        await provider.client.aclose()
        await redis.aclose()
        await driver.close()
