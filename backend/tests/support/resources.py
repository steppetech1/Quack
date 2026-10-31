"""Builders of process resources with test dependencies (ТЗ Фазы 3 §4.2, §8.1, seam S1).

`build_app_resources` assembles `quack.api.resources.AppResources` from the test pools,
`build_job_context` assembles `quack.jobs.runner.JobContext` (WP2.1, conflict 20 of §2.3).
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
from quack.jobs.runner import JobContext
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


def build_job_context(
    settings: Settings,
    session_factory: SessionFactory,
    neo4j: AsyncDriver | Any,
    redis: Redis | Any,
    *,
    engine: AsyncEngine | None = None,
    llm: LLMGateway | None = None,
    provider: LLMProvider | None = None,
    compute: ComputeClient | None = None,
) -> JobContext:
    """`JobContext` over the test pools. `llm` is used as is (e.g. `FakeLLMProvider.gateway`);
    otherwise a gateway over `provider`, by default the LLM is down (`UnavailableLLMProvider`).
    Compute defaults to an empty `ComputeScenario` (answers 503)."""
    return JobContext(
        settings=settings,
        session_factory=session_factory,
        engine=engine,
        neo4j=cast(AsyncDriver, neo4j),
        redis=cast(Redis, redis),
        llm=llm or LLMGateway(session_factory, provider or UnavailableLLMProvider(), settings),
        compute=compute or scripted_compute(ComputeScenario()),
    )
