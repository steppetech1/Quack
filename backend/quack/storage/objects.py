"""`ObjectStore`: learner objects in S3-compatible storage (Т-§3.3, Д-§8.4, ТЗ Фазы 4 WP19).

Seam S1: the interface and `StorageUnavailable` are shared by all streams; the test double is
`tests.support.storage.InMemoryObjectStore`. Every learner object lives under
`learners/{learner_id}/`, objects are immutable, keys carry a random part (Т-§3.3).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast, runtime_checkable
from uuid import UUID

import aioboto3  # pyright: ignore[reportMissingTypeStubs]
from aiobotocore.config import AioConfig  # pyright: ignore[reportMissingTypeStubs]
from botocore.exceptions import BotoCoreError, ClientError  # pyright: ignore[reportMissingTypeStubs]

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
    """`ObjectStore` on aioboto3 with `addressing_style = "path"` (Contabo, Д-§8.4). WP19.

    One client per process, created on the first call and closed by `aclose`. A storage that
    does not answer (connection, timeout, 5xx) is `StorageUnavailable` (Т-§20.7); a missing
    key on `get` is `KeyError`, as in the test double. The region is the botocore default:
    the documents do not name it (Д-§8.4 «Проверить», D-C-109)."""

    def __init__(self, *, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        self._endpoint = endpoint
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket
        self._stack = AsyncExitStack()
        self._client: Any = None

    async def _s3(self) -> Any:
        if self._client is None:
            session = aioboto3.Session(
                aws_access_key_id=self._access_key, aws_secret_access_key=self._secret_key
            )
            config = AioConfig(signature_version="s3v4", s3={"addressing_style": "path"})
            client = cast(
                AbstractAsyncContextManager[Any],
                session.client("s3", endpoint_url=self._endpoint, config=config),  # pyright: ignore[reportUnknownMemberType]
            )
            self._client = await self._stack.enter_async_context(client)
        return self._client

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        async with _unavailable_on_failure():
            s3 = await self._s3()
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    async def get(self, key: str) -> bytes:
        async with _unavailable_on_failure():
            s3 = await self._s3()
            try:
                response = await s3.get_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in _MISSING:
                    raise KeyError(key) from exc
                raise
            async with response["Body"] as body:
                return await body.read()

    async def presigned_get_url(self, key: str, ttl_s: int) -> str:
        async with _unavailable_on_failure():
            s3 = await self._s3()
            url = await s3.generate_presigned_url(
                "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=ttl_s
            )
        return str(url)

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        async with _unavailable_on_failure():
            s3 = await self._s3()
            found: list[ObjectInfo] = []
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                found.extend(
                    ObjectInfo(
                        key=item["Key"], size=item["Size"], last_modified=item["LastModified"]
                    )
                    for item in page.get("Contents", [])
                )
            return found

    async def delete_keys(self, keys: Sequence[str]) -> None:
        async with _unavailable_on_failure():
            s3 = await self._s3()
            for start in range(0, len(keys), _DELETE_BATCH):
                batch = [{"Key": key} for key in keys[start : start + _DELETE_BATCH]]
                await s3.delete_objects(
                    Bucket=self._bucket, Delete={"Objects": batch, "Quiet": True}
                )

    async def delete_prefix(self, prefix: str) -> int:
        keys = [info.key for info in await self.list_objects(prefix)]
        await self.delete_keys(keys)
        return len(keys)

    async def aclose(self) -> None:
        await self._stack.aclose()
        self._client = None


# S3 `DeleteObjects` takes at most 1000 keys per request.
_DELETE_BATCH = 1000
_MISSING = frozenset({"NoSuchKey", "404"})


@asynccontextmanager
async def _unavailable_on_failure() -> AsyncIterator[None]:
    """Connection errors, timeouts and 5xx of the storage → `StorageUnavailable`. A `ValueError`
    here is aiobotocore rejecting the client configuration itself (for example
    `endpoint_url=""`, raised by `create_endpoint` on first use): storage that cannot be
    reached because it is unset is "does not answer" the same as a connection failure, so it
    gets the same `StorageUnavailable` (Т-§20.7) instead of crashing the caller (WS canvas
    socket included)."""
    try:
        yield
    except (BotoCoreError, OSError, ValueError) as exc:
        raise StorageUnavailable(f"object storage request failed: {exc!r}") from exc
    except ClientError as exc:
        status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        if status >= 500:
            raise StorageUnavailable(f"object storage returned HTTP {status}") from exc
        raise


def create_object_store(settings: Settings) -> ObjectStore:
    """`ObjectStore` of bucket `s3_bucket_learners` (Д-§8.4); for `api` and `workers`."""
    return S3ObjectStore(
        endpoint=settings.s3_endpoint.get_secret_value(),
        access_key=settings.s3_access_key.get_secret_value(),
        secret_key=settings.s3_secret_key.get_secret_value(),
        bucket=settings.s3_bucket_learners,
    )
