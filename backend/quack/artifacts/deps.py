"""Resources of the Stream 3 workers.

Authentication and the HTTP dependencies come from `quack.api.auth` and `quack.api.resources`
(ТЗ Фазы 3 WP1.2, WP1.3); the worker resources come from `JobContext` (WP2.1).
"""

from __future__ import annotations

from dataclasses import dataclass

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.jobs.runner import JobContext
from quack.llm.gateway import LLMGateway


@dataclass(frozen=True)
class WorkerDeps:
    """What Stream 3 job handlers need: the shared resources of the `workers` process."""

    settings: Settings
    session_factory: SessionFactory
    gateway: LLMGateway
    neo4j: AsyncDriver
    redis: Redis | None


def worker_deps(ctx: JobContext) -> WorkerDeps:
    """`WorkerDeps` over the resources of `ctx`; nothing is created or closed here (WP2.1)."""
    if ctx.llm is None or ctx.neo4j is None:
        raise RuntimeError("the job needs the LLM gateway and the Neo4j driver in JobContext")
    return WorkerDeps(
        settings=ctx.settings,
        session_factory=ctx.session_factory,
        gateway=ctx.llm,
        neo4j=ctx.neo4j,
        redis=ctx.redis,
    )
