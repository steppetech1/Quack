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
from typing import Any, Literal, Protocol, cast, get_args, runtime_checkable
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

    One object per deletion, key `{deleted_at}-{deletion_id}.json` where `deleted_at` is a
    fixed-width UTC time (`_KEY_TIME`), so key order is time order and `list_since` can start the
    listing after the key prefix of `since` (Д-§8.5: "по порядку времени"). The body carries the
    identifiers only. Objects are never deleted here (bucket lock without a term). Anything that
    stops the journal answering (no endpoint configured, connection, timeout, a refusal of the
    bucket, a record that does not parse) is `DeletionJournalUnavailable`: the deletion must not
    run (Т-§19.5, Т-§20.7) and the API answers `SERVICE_UNAVAILABLE`.
    """

    def __init__(self, *, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        self._endpoint = endpoint
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket
        self._stack = AsyncExitStack()
        self._client: Any = None

    async def _s3(self) -> Any:
        if self._client is None:
            if not self._endpoint:
                # An empty `endpoint_url` makes botocore talk to AWS: never write there.
                raise DeletionJournalUnavailable("deletions_s3_endpoint is not configured")
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
        body = json.dumps(_to_payload(record), separators=(",", ":")).encode()
        async with _unavailable_on_failure():
            s3 = await self._s3()
            await s3.put_object(
                Bucket=self._bucket,
                Key=deletion_key(record),
                Body=body,
                ContentType="application/json",
            )

    async def list_since(self, since: datetime) -> list[DeletionRecord]:
        after = _utc(since).strftime(_KEY_TIME)
        records: list[DeletionRecord] = []
        async with _unavailable_on_failure():
            s3 = await self._s3()
            paginator = s3.get_paginator("list_objects_v2")
            # `StartAfter` is exclusive: the key of a record at exactly `since` is
            # `{since}-{id}.json`, which sorts after the bare `{since}`.
            async for page in paginator.paginate(Bucket=self._bucket, StartAfter=after):
                for item in page.get("Contents", []):
                    key: str = item["Key"]
                    if not key.endswith(".json"):
                        continue
                    response = await s3.get_object(Bucket=self._bucket, Key=key)
                    async with response["Body"] as stream:
                        raw = await stream.read()
                    record = _from_payload(key, raw)
                    if record.deleted_at >= _utc(since):
                        records.append(record)
        return sorted(records, key=lambda r: (r.deleted_at, str(r.deletion_id)))

    async def aclose(self) -> None:
        await self._stack.aclose()
        self._client = None


# Fixed width, UTC, sorts as time: `20261105T101500.123456Z` (microseconds as `datetime` has).
_KEY_TIME = "%Y%m%dT%H%M%S.%fZ"
_KINDS = frozenset(get_args(DeletionKind.__value__))


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("deletion time must be timezone-aware")
    return moment.astimezone(UTC)


def deletion_key(record: DeletionRecord) -> str:
    """`{deleted_at}-{deletion_id}.json` (Д-§8.5)."""
    return f"{_utc(record.deleted_at).strftime(_KEY_TIME)}-{record.deletion_id}.json"


def _to_payload(record: DeletionRecord) -> dict[str, str]:
    return {
        "deletion_id": str(record.deletion_id),
        "learner_id": str(record.learner_id),
        "kind": record.kind,
        "target_id": str(record.target_id),
        "deleted_at": _utc(record.deleted_at).isoformat(),
    }


def _from_payload(key: str, raw: bytes) -> DeletionRecord:
    try:
        data = json.loads(raw)
        kind = data["kind"]
        if kind not in _KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        return DeletionRecord(
            deletion_id=UUID(data["deletion_id"]),
            learner_id=UUID(data["learner_id"]),
            kind=kind,
            target_id=UUID(data["target_id"]),
            deleted_at=_utc(datetime.fromisoformat(data["deleted_at"])),
        )
    except (ValueError, KeyError, TypeError) as exc:
        # A record that cannot be read must not be skipped silently: it is a deletion the
        # reconcile would miss. Fail the whole read (the job retries; an operator looks).
        raise DeletionJournalUnavailable(f"deletion journal record {key!r} is unreadable") from exc


@asynccontextmanager
async def _unavailable_on_failure() -> AsyncIterator[None]:
    """Connection errors, timeouts and any refusal of the bucket → `DeletionJournalUnavailable`.

    Unlike `S3ObjectStore`, 4xx is mapped too: a wrong key or a missing bucket means the fact of
    deletion is not recorded, which for the caller is the same as R2 being down (503, not 500)."""
    try:
        yield
    except DeletionJournalUnavailable:
        raise
    except (BotoCoreError, OSError) as exc:
        raise DeletionJournalUnavailable(f"deletion journal request failed: {exc!r}") from exc
    except ClientError as exc:
        status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        raise DeletionJournalUnavailable(f"deletion journal returned HTTP {status}") from exc


def create_deletion_journal(settings: Settings) -> DeletionJournal:
    """`DeletionJournal` in R2 bucket `deletions_bucket` (Д-§8.5); for `api` and `workers`."""
    return R2DeletionJournal(
        endpoint=settings.deletions_s3_endpoint,
        access_key=settings.deletions_s3_access_key.get_secret_value(),
        secret_key=settings.deletions_s3_secret_key.get_secret_value(),
        bucket=settings.deletions_bucket,
    )
