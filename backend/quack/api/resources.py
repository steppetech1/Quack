"""Process resources of the API and their FastAPI dependencies (ТЗ Фазы 3 §4.2, WP1.3).

Seam S1: the field set of `AppResources` is a contract shared by all streams. Product
packages import `AppResources` and the dependencies only from this module (§4.1), so that
`quack.api.app` can import product routers without an import cycle.

Stage I: the contract is declared here; Stage III of WP1.3 moves every user of
`quack.api.app.AppResources` / `get_resources` onto this module and removes the old ones.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.llm.gateway import LLMGateway


@dataclass
class AppResources:
    """Heavy objects created once per process (§4.2) and passed explicitly.

    - `neo4j`: outside the projector only read sessions are opened (law 3, WP4);
    - `redis`: `None` means working without Redis (§4.2);
    - `llm`: one gateway per process (WP5.1);
    - `compute`: one client with one `httpx.AsyncClient`.
    """

    settings: Settings
    engine: AsyncEngine
    session_factory: SessionFactory
    neo4j: AsyncDriver
    redis: Redis | None
    llm: LLMGateway
    compute: ComputeClient

    async def close(self) -> None:
        """Close in reverse creation order: compute, llm, redis, neo4j, engine (§4.2)."""
        raise NotImplementedError


def get_resources(request: Request) -> AppResources:
    """`AppResources` of the running application; `RuntimeError` if not initialized."""
    raise NotImplementedError


def get_settings(request: Request) -> Settings:
    """`Settings` of the running application (`resources.settings`)."""
    raise NotImplementedError


def get_llm(request: Request) -> LLMGateway:
    """The process-wide `LLMGateway` (`resources.llm`); replaces per-package `get_llm_gateway`."""
    raise NotImplementedError


def get_compute(request: Request) -> ComputeClient:
    """The process-wide `ComputeClient` (`resources.compute`)."""
    raise NotImplementedError
