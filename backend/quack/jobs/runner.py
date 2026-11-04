"""Context and registry of the job handlers (spec §7, Т-§13.1). Handlers of the streams register
with `register_job_handler`; the executor of Phase 4 (`quack.jobs.executor_p4`) runs them."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db import Db
from quack.embeddings.client import EmbeddingClient
from quack.jobs.types import ClaimedJob
from quack.llm.gateway import LLMGateway
from quack.storage.deletions import DeletionJournal
from quack.storage.objects import ObjectStore


@dataclass(frozen=True)
class JobContext:
    """Per-process resources of `workers` (ТЗ Ф4 §4.2, seam S1).

    The field set is a contract: every resource of §4.2 but `email`. Handlers take the heavy
    objects only from here and never create them. `redis = None` means work without Redis.
    Tests build the context with `tests.support.resources.build_job_context`.
    """

    settings: Settings
    db: Db
    neo4j: AsyncDriver
    redis: Redis | None
    llm: LLMGateway
    compute: ComputeClient
    storage: ObjectStore
    embeddings: EmbeddingClient
    deletions: DeletionJournal


type JobHandler = Callable[[ClaimedJob, JobContext], Awaitable[None]]

JOB_HANDLERS: dict[str, JobHandler] = {}


def register_job_handler(
    kind: str, *, registry: dict[str, JobHandler] | None = None
) -> Callable[[JobHandler], JobHandler]:
    target = JOB_HANDLERS if registry is None else registry

    def decorator(handler: JobHandler) -> JobHandler:
        if kind in target:
            raise ValueError(f"handler for job kind {kind!r} is already registered")
        target[kind] = handler
        return handler

    return decorator
