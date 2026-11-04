"""Process resources of the API and their FastAPI dependencies (ТЗ Фазы 3 §4.2, WP1.3).

Seam S1: the field set of `AppResources` is a contract shared by all streams. Product
packages import `AppResources` and the dependencies only from this module (§4.1), so that
`quack.api.app` can import product routers without an import cycle.

ТЗ Фазы 4 §4.2 adds `storage`, `embeddings`, `email`, `deletions` (stream C) and `db` (the `Db`
of WP1.2, stream A, seam S2); `engine` and `session_factory` left in WP1.3 (D-C-170). The only
SQLAlchemy object of the API process is the session factory handed to `LLMGateway` for its
outbox until stream B removes that argument (D-B-197, step 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from neo4j import AsyncDriver
from redis.asyncio import Redis
from starlette.requests import HTTPConnection

from quack.api.email import EmailSender, create_email_sender
from quack.compute_client.client import ComputeClient, create_compute_client
from quack.config import Settings
from quack.db import Db, open_db
from quack.db.neo4j import create_driver
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
    db: Db
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
        redis, neo4j, db (§4.2)."""
        await self.deletions.aclose()
        await self.email.aclose()
        await self.embeddings.aclose()
        await self.storage.aclose()
        await self.compute.aclose()
        await self.llm.aclose()
        if self.redis is not None:
            await self.redis.aclose()
        await self.neo4j.close()
        await self.db.close()


async def create_app_resources(settings: Settings) -> AppResources:
    """Production resources of the API process, in the creation order of §4.2. The `Db` pool
    opens without waiting for Postgres (`wait=False`): the API starts and `/health/ready`
    reports a missing database as 503 (Phase 3 readiness contract)."""
    db = await open_db(settings, wait=False)
    neo4j = create_driver(settings)
    redis = create_redis(settings)
    provider = ResponsesClient(settings.meta_api_base_url, settings.meta_api_key.get_secret_value())
    return AppResources(
        settings=settings,
        db=db,
        neo4j=neo4j,
        redis=redis,
        llm=LLMGateway(provider, settings, db=db),
        compute=create_compute_client(settings),
        storage=create_object_store(settings),
        embeddings=create_embedding_client(settings),
        email=create_email_sender(settings),
        deletions=create_deletion_journal(settings),
    )


def get_resources(request: HTTPConnection) -> AppResources:
    """`AppResources` of the running application (a request or a WebSocket); `RuntimeError` if
    not initialized."""
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
