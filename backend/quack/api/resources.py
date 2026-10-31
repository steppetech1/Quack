"""Process resources of the API and their FastAPI dependencies (ТЗ Фазы 3 §4.2, WP1.3).

Seam S1: the field set of `AppResources` is a contract shared by all streams. Product
packages import `AppResources` and the dependencies only from this module (§4.1), so that
`quack.api.app` can import product routers without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import Request
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db.neo4j import create_driver
from quack.db.postgres import SessionFactory, create_engine, create_session_factory
from quack.db.redis import create_redis
from quack.llm.gateway import LLMGateway
from quack.llm.provider import HttpLLMProvider


@dataclass
class AppResources:
    """Heavy objects created once per process (§4.2) and passed explicitly.

    - `neo4j`: outside the projector only read sessions are opened (law 3, WP4);
    - `redis`: by §4.2 the contract type is `Redis | None` (`None` = working without Redis).
      It stays `Redis` until `quack/session/lifecycle.py::_set_presence` handles `None`
      (D-C-005, stream B); the code below already treats `None` as "no Redis";
    - `llm`: one gateway per process (WP5.1);
    - `compute`: one client with one `httpx.AsyncClient`.
    """

    settings: Settings
    engine: AsyncEngine
    session_factory: SessionFactory
    neo4j: AsyncDriver
    redis: Redis  # contract: Redis | None (D-C-005)
    llm: LLMGateway
    compute: ComputeClient

    async def close(self) -> None:
        """Close in reverse creation order: compute, llm, redis, neo4j, engine (§4.2)."""
        await self.compute.aclose()
        # `LLMGateway` has no close of its own yet (seam S1, stream B): close the HTTP
        # client of the provider when it has one.
        provider_client = getattr(self.llm.provider, "client", None)
        if isinstance(provider_client, httpx.AsyncClient):
            await provider_client.aclose()
        if self.redis is not None:  # D-C-005: None until the contract type lands
            await self.redis.aclose()
        await self.neo4j.close()
        await self.engine.dispose()


def create_app_resources(settings: Settings) -> AppResources:
    """Production resources of the API process, in the creation order of §4.2."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    neo4j = create_driver(settings)
    redis = create_redis(settings)
    provider = HttpLLMProvider(settings.llm_api_base_url, settings.llm_api_key.get_secret_value())
    return AppResources(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        neo4j=neo4j,
        redis=redis,
        llm=LLMGateway(session_factory, provider, settings),
        compute=ComputeClient(settings.compute_url, settings.compute_timeout_sec),
    )


def get_resources(request: Request) -> AppResources:
    """`AppResources` of the running application; `RuntimeError` if not initialized."""
    resources: AppResources | None = getattr(request.app.state, "resources", None)
    if resources is None:
        raise RuntimeError("application resources are not initialized")
    return resources


def get_settings(request: Request) -> Settings:
    """`Settings` of the running application (`resources.settings`)."""
    return get_resources(request).settings


def get_llm(request: Request) -> LLMGateway:
    """The process-wide `LLMGateway` (`resources.llm`); replaces per-package `get_llm_gateway`."""
    return get_resources(request).llm


def get_compute(request: Request) -> ComputeClient:
    """The process-wide `ComputeClient` (`resources.compute`)."""
    return get_resources(request).compute
