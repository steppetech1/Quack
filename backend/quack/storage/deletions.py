"""`DeletionJournal`: facts of deletion in R2 bucket `quack-deletions` (Т-§19.5, Д-§8.5).

Seam S9 (stream C → A, WP25): before any physical deletion the API writes the fact here; if the
write fails, the deletion is not performed (`DeletionJournalUnavailable` → `SERVICE_UNAVAILABLE`).
Only identifiers, no learner data. Object key: `{deleted_at}-{deletion_id}.json` (Д-§8.5).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast, runtime_checkable
from uuid import UUID

import aioboto3  # pyright: ignore[reportMissingTypeStubs]
from aiobotocore.config import AioConfig  # pyright: ignore[reportMissingTypeStubs]
from botocore.exceptions import BotoCoreError, ClientError  # pyright: ignore[reportMissingTypeStubs]

from quack.config import Settings

type DeletionKind = Literal["topic", "domain", "account"]


class DeletionJournalUnavailable(Exception):
    """R2 does not answer: the deletion must not run (Т-§20.7)."""


@dataclass(frozen=True)
class DeletionRecord:
    deletion_id: UUID
    learner_id: UUID
    kind: DeletionKind
    target_id: UUID  # topic, domain or learner id (ТЗ §4.9, §6.1 `ops.deletion_log`)
    deleted_at: datetime


@runtime_checkable
class DeletionJournal(Protocol):
    async def write(self, record: DeletionRecord) -> None: ...

    async def list_since(self, since: datetime) -> list[DeletionRecord]:
        """Records with `deleted_at >= since` (`ops replay-deletions`, `deletions.reconcile`)."""
        ...

    async def aclose(self) -> None: ...


class R2DeletionJournal:
    """`DeletionJournal` on the S3 API of R2, key only for `quack-deletions` (Д-§7). WP25.

    One object per deletion: key `{deleted_at}-{deletion_id}.json`, body — the identifiers of
    `DeletionRecord` and nothing else (Д-§8.5). `deleted_at` is written in UTC with a fixed width
    (`2026-11-13T10:00:00.123456Z`), so the listing order of the bucket is the time order. The same
    record gives the same key: a repeated write replaces the object with itself. The client is one
    per process, created on the first call and closed by `aclose`. A bucket that does not answer
    (connection, timeout, 5xx, no endpoint configured) is `DeletionJournalUnavailable` (Т-§20.7):
    the deletion does not run. The region is the botocore default, as in `S3ObjectStore`."""

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

    async def write(self, record: DeletionRecord) -> None:
        body = json.dumps(
            {
                "deletion_id": str(record.deletion_id),
                "learner_id": str(record.learner_id),
                "kind": record.kind,
                "target_id": str(record.target_id),
                "deleted_at": _stamp(record.deleted_at),
            },
            sort_keys=True,
        ).encode()
        async with _unavailable_on_failure():
            s3 = await self._s3()
            await s3.put_object(
                Bucket=self._bucket,
                Key=_key(record),
                Body=body,
                ContentType="application/json",
            )

    async def list_since(self, since: datetime) -> list[DeletionRecord]:
        """Records with `deleted_at >= since`, oldest first. The listing starts at the key stamp of
        `since`, so older objects are not even listed; the body of every object is read."""
        records: list[DeletionRecord] = []
        async with _unavailable_on_failure():
            s3 = await self._s3()
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, StartAfter=_stamp(since)):
                for item in page.get("Contents", []):
                    response = await s3.get_object(Bucket=self._bucket, Key=item["Key"])
                    async with response["Body"] as stream:
                        records.append(_parse(item["Key"], await stream.read()))
        return sorted((r for r in records if r.deleted_at >= since), key=lambda r: r.deleted_at)

    async def aclose(self) -> None:
        await self._stack.aclose()
        self._client = None


def _stamp(moment: datetime) -> str:
    """UTC, fixed width: string order is time order (a naive time is taken as UTC)."""
    utc = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _key(record: DeletionRecord) -> str:
    return f"{_stamp(record.deleted_at)}-{record.deletion_id}.json"


def _parse(key: str, body: bytes) -> DeletionRecord:
    try:
        data = json.loads(body)
        return DeletionRecord(
            deletion_id=UUID(data["deletion_id"]),
            learner_id=UUID(data["learner_id"]),
            kind=data["kind"],
            target_id=UUID(data["target_id"]),
            deleted_at=datetime.fromisoformat(data["deleted_at"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        # A record that cannot be read is a deletion that cannot be repeated: stop and say so
        # instead of skipping it (Т-§19.5).
        raise DeletionJournalUnavailable(f"journal object {key} is not a deletion record") from exc


@asynccontextmanager
async def _unavailable_on_failure() -> AsyncIterator[None]:
    """Connection errors, timeouts, 5xx and a client that cannot be configured (an unset
    endpoint is a bare `ValueError` of aiobotocore) → `DeletionJournalUnavailable`. Other S3
    errors (access denied, a missing bucket) are the same for the caller: the deletion must not
    run without its record."""
    try:
        yield
    except (BotoCoreError, ClientError, OSError, ValueError) as exc:
        raise DeletionJournalUnavailable(f"deletion journal request failed: {exc!r}") from exc


def create_deletion_journal(settings: Settings) -> DeletionJournal:
    """`DeletionJournal` in R2 bucket `deletions_bucket` (Д-§8.5); for `api` and `workers`."""
    return R2DeletionJournal(
        endpoint=settings.deletions_s3_endpoint,
        access_key=settings.deletions_s3_access_key.get_secret_value(),
        secret_key=settings.deletions_s3_secret_key.get_secret_value(),
        bucket=settings.deletions_bucket,
    )
