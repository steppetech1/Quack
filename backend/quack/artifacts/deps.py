"""Resources of the Stream 3 workers.

Authentication and the HTTP dependencies come from `quack.api.auth` and `quack.api.resources`
(ТЗ Фазы 3 WP1.2, WP1.3); the worker resources move to `JobContext` in WP2.1 (stream A).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db.neo4j import create_driver
from quack.db.postgres import SessionFactory
from quack.db.redis import create_redis
from quack.jobs.runner import JobContext
from quack.llm.gateway import LLMGateway
from quack.llm.provider import HttpLLMProvider


def _provider(settings: Settings) -> HttpLLMProvider:
    return HttpLLMProvider(settings.meta_api_base_url, settings.meta_api_key.get_secret_value())


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
