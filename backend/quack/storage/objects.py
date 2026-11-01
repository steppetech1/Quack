"""`ObjectStore`: learner objects in S3-compatible storage (Т-§3.3, Д-§8.4, ТЗ Фазы 4 WP19).

Seam S1: the interface and `StorageUnavailable` are shared by all streams; the test double is
`tests.support.storage.InMemoryObjectStore`. Every learner object lives under
`learners/{learner_id}/`, objects are immutable, keys carry a random part (Т-§3.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from quack.config import Settings


class StorageUnavailable(Exception):
    """Object storage does not answer; the API maps it to `SERVICE_UNAVAILABLE` (Т-§20.7)."""


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    last_modified: datetime


def learner_prefix(learner_id: UUID) -> str:
    """The prefix of all objects of a learner; account deletion removes it (Т-§3.3)."""
    return f"learners/{learner_id}/"


@runtime_checkable
class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Write a new object; an existing key is never overwritten (Т-§3.3)."""
        ...

    async def get(self, key: str) -> bytes: ...

    async def presigned_get_url(self, key: str, ttl_s: int) -> str:
        """Signed GET link, path-style (Д-§8.4)."""
        ...

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        """Objects under `prefix` with their time (`ops orphan-objects`, Д-§8.3)."""
        ...

    async def delete_keys(self, keys: Sequence[str]) -> None:
        """Delete by key list; missing keys are not an error (WP25, seam S9)."""
        ...

    async def delete_prefix(self, prefix: str) -> int:
        """Delete everything under `prefix`; returns the number of deleted objects (S9)."""
        ...

    async def aclose(self) -> None: ...


class S3ObjectStore:
    """`ObjectStore` on aioboto3 with `addressing_style = "path"` (Contabo, Д-§8.4). WP19."""

    def __init__(self, *, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        self._endpoint = endpoint
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        raise NotImplementedError

    async def get(self, key: str) -> bytes:
        raise NotImplementedError

    async def presigned_get_url(self, key: str, ttl_s: int) -> str:
        raise NotImplementedError

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        raise NotImplementedError

    async def delete_keys(self, keys: Sequence[str]) -> None:
        raise NotImplementedError

    async def delete_prefix(self, prefix: str) -> int:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


def create_object_store(settings: Settings) -> ObjectStore:
    """`ObjectStore` of bucket `s3_bucket_learners` (Д-§8.4); for `api` and `workers`."""
    return S3ObjectStore(
        endpoint=settings.s3_endpoint.get_secret_value(),
        access_key=settings.s3_access_key.get_secret_value(),
        secret_key=settings.s3_secret_key.get_secret_value(),
        bucket=settings.s3_bucket_learners,
    )
