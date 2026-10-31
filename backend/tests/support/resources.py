"""Builders of process resources with test dependencies (ТЗ Фазы 3 §4.2, §8.1, seam S1).

`build_app_resources` assembles `quack.api.resources.AppResources` from the test pools.
The `JobContext` builder is added by stream A together with its fields (seam S1, WP2.1).
"""

from __future__ import annotations

from typing import Any, cast

from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.resources import AppResources
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db.postgres import SessionFactory, create_session_factory
from quack.llm.gateway import LLMGateway
from quack.llm.provider import LLMProvider
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import UnavailableLLMProvider


def build_app_resources(
    settings: Settings,
    engine: AsyncEngine,
    neo4j: AsyncDriver | Any,
    redis: Redis | Any | None,
    *,
    session_factory: SessionFactory | None = None,
    provider: LLMProvider | None = None,
    compute: ComputeClient | None = None,
) -> AppResources:
    """`AppResources` over the test pools. `neo4j` / `redis` may be the broken doubles of
    `tests.support.broken`; by default the LLM is down (`UnavailableLLMProvider`) and compute is an
    empty `ComputeScenario` (answers 503)."""
    factory = session_factory or create_session_factory(engine)
    return AppResources(
        settings=settings,
        engine=engine,
        session_factory=factory,
        neo4j=cast(AsyncDriver, neo4j),
        redis=cast(Redis | None, redis),
        llm=LLMGateway(factory, provider or UnavailableLLMProvider(), settings),
        compute=compute or scripted_compute(ComputeScenario()),
    )
