"""Process resources of the API and their FastAPI dependencies (ТЗ Фазы 3 §4.2, WP1.3).

Seam S1: the field set of `AppResources` is a contract shared by all streams. Product
packages import `AppResources` and the dependencies only from this module (§4.1), so that
`quack.api.app` can import product routers without an import cycle.

ТЗ Фазы 4 §4.2 adds `storage`, `embeddings`, `email`, `deletions` (stream C); `db` comes with
WP1.2 (stream A, seam S2) and replaces `engine` and `session_factory` in WP1.3.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.email import EmailSender, create_email_sender
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db.neo4j import create_driver
from quack.db.postgres import SessionFactory, create_engine, create_session_factory
from quack.db.redis import create_redis
from quack.embeddings.client import EmbeddingClient, create_embedding_client
from quack.llm.gateway import LLMGateway
from quack.llm.responses_client import ResponsesClient
from quack.storage.deletions import DeletionJournal, create_deletion_journal
from quack.storage.objects import ObjectStore, create_object_store


@dataclass
class AppResources:
    """Heavy objects created once per process (§4.2) and passed explicitly.

    - `neo4j`: outside the projector only read sessions are opened (law 3, WP4);
    - `redis`: `None` means working without Redis (§4.2);
    - `llm`: one gateway per process (WP5.1);
    - `compute`: one client with one `httpx.AsyncClient`;
    - `storage`, `embeddings`, `email`, `deletions`: external clients of ТЗ Фазы 4 §4.2.
    """

    settings: Settings
    engine: AsyncEngine
    session_factory: SessionFactory
    neo4j: AsyncDriver
    redis: Redis | None
    llm: LLMGateway
    compute: ComputeClient
    storage: ObjectStore
    embeddings: EmbeddingClient
    email: EmailSender
    deletions: DeletionJournal

    async def close(self) -> None:
        """Close in reverse creation order: deletions, email, embeddings, storage, compute, llm,
        redis, neo4j, engine (§4.2)."""
        await self.deletions.aclose()
        await self.email.aclose()
        await self.embeddings.aclose()
        await self.storage.aclose()
        await self.compute.aclose()
        await self.llm.aclose()
        if self.redis is not None:
            await self.redis.aclose()
        await self.neo4j.close()
        await self.engine.dispose()


def create_app_resources(settings: Settings) -> AppResources:
    """Production resources of the API process, in the creation order of §4.2."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    neo4j = create_driver(settings)
    redis = create_redis(settings)
    provider = ResponsesClient(settings.meta_api_base_url, settings.meta_api_key.get_secret_value())
    return AppResources(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        neo4j=neo4j,
        redis=redis,
        llm=LLMGateway(session_factory, provider, settings),
        compute=ComputeClient(settings.compute_url, settings.compute_timeout_sec),
        storage=create_object_store(settings),
        embeddings=create_embedding_client(settings),
        email=create_email_sender(settings),
        deletions=create_deletion_journal(settings),
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


def get_storage(request: Request) -> ObjectStore:
    """The process-wide `ObjectStore` (`resources.storage`)."""
    return get_resources(request).storage


def get_embeddings(request: Request) -> EmbeddingClient:
    """The process-wide `EmbeddingClient` (`resources.embeddings`)."""
    return get_resources(request).embeddings


def get_email(request: Request) -> EmailSender:
    """The process-wide `EmailSender` (`resources.email`)."""
    return get_resources(request).email


def get_deletions(request: Request) -> DeletionJournal:
    """The process-wide `DeletionJournal` (`resources.deletions`)."""
    return get_resources(request).deletions
