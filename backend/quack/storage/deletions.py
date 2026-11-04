"""`DeletionJournal`: facts of deletion in R2 bucket `quack-deletions` (Т-§19.5, Д-§8.5).

Seam S9 (stream C → A, WP25): before any physical deletion the API writes the fact here; if the
write fails, the deletion is not performed (`DeletionJournalUnavailable` → `SERVICE_UNAVAILABLE`).
Only identifiers, no learner data. Object key: `{deleted_at}-{deletion_id}.json` (Д-§8.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

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
    """`DeletionJournal` on the S3 API of R2, key only for `quack-deletions` (Д-§7). WP25."""

    def __init__(self, *, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        self._endpoint = endpoint
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket

    async def write(self, record: DeletionRecord) -> None:
        raise NotImplementedError

    async def list_since(self, since: datetime) -> list[DeletionRecord]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


def create_deletion_journal(settings: Settings) -> DeletionJournal:
    """`DeletionJournal` in R2 bucket `deletions_bucket` (Д-§8.5); for `api` and `workers`."""
    return R2DeletionJournal(
        endpoint=settings.deletions_s3_endpoint,
        access_key=settings.deletions_s3_access_key.get_secret_value(),
        secret_key=settings.deletions_s3_secret_key.get_secret_value(),
        bucket=settings.deletions_bucket,
    )
