"""`R2DeletionJournal` (seam S9, D-C-102; Т-§19.5, Т-§20.7, Д-§8.5): the record, the key, the
listing by time, and every failure as `DeletionJournalUnavailable` (→ 503, never a raw error).

The S3 client is replaced by an in-memory double with the calls the journal makes; the real R2
is not reached from tests (AGENT.md, red line 9)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI

from quack.api.errors import ApiError, install_error_handlers
from quack.storage.deletions import (
    DeletionJournalUnavailable,
    DeletionKind,
    DeletionRecord,
    R2DeletionJournal,
    deletion_key,
)

NOW = datetime(2026, 11, 5, 10, 15, 0, 123456, tzinfo=UTC)


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aenter__(self) -> _Body:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def read(self) -> bytes:
        return self._data


class _Paginator:
    def __init__(self, client: _FakeS3) -> None:
        self._client = client

    async def paginate(self, *, Bucket: str, StartAfter: str = "") -> AsyncIterator[dict[str, Any]]:
        self._client.calls.append(("list", Bucket, StartAfter))
        keys = sorted(k for k in self._client.objects if k > StartAfter)
        for start in range(0, max(len(keys), 1), 2):  # two keys per page: paging is exercised
            yield {"Contents": [{"Key": k} for k in keys[start : start + 2]]}


class _FakeS3:
    """`put_object`, `get_object` and the `list_objects_v2` paginator over a dict."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, ...]] = []
        self.error: Exception | None = None

    def _check(self) -> None:
        if self.error is not None:
            raise self.error

    async def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self._check()
        self.calls.append(("put", Bucket, Key, ContentType))
        self.objects[Key] = Body

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self._check()
        return {"Body": _Body(self.objects[Key])}

    def get_paginator(self, name: str) -> _Paginator:
        assert name == "list_objects_v2"
        self._check()
        return _Paginator(self)


def _journal(endpoint: str = "https://account.r2.cloudflarestorage.com") -> R2DeletionJournal:
    return R2DeletionJournal(
        endpoint=endpoint, access_key="key", secret_key="secret", bucket="quack-deletions"
    )


def _with_fake(journal: R2DeletionJournal) -> _FakeS3:
    fake = _FakeS3()
    journal._client = fake  # pyright: ignore[reportPrivateUsage]
    return fake


def _record(at: datetime, kind: DeletionKind = "topic") -> DeletionRecord:
    return DeletionRecord(uuid4(), uuid4(), kind, uuid4(), at)


def _client_error(status: int) -> ClientError:
    return ClientError(
        {"Error": {"Code": "X"}, "ResponseMetadata": {"HTTPStatusCode": status}}, "PutObject"
    )


def test_key_is_utc_time_then_deletion_id() -> None:
    record = _record(NOW)
    assert deletion_key(record) == f"20261105T101500.123456Z-{record.deletion_id}.json"
    # The same moment in another zone gives the same time part; keys sort as time.
    almaty = NOW.astimezone(timezone(timedelta(hours=5)))
    assert deletion_key(_record(almaty)).split("-")[0] == "20261105T101500.123456Z"
    assert deletion_key(_record(NOW)) < deletion_key(_record(NOW + timedelta(microseconds=1)))


def test_naive_time_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        deletion_key(_record(datetime(2026, 11, 5, 10, 0)))


async def test_write_puts_identifiers_only_under_the_dated_key() -> None:
    journal = _journal()
    fake = _with_fake(journal)
    record = _record(NOW, "account")
    await journal.write(record)

    key = deletion_key(record)
    assert fake.calls == [("put", "quack-deletions", key, "application/json")]
    assert json.loads(fake.objects[key]) == {
        "deletion_id": str(record.deletion_id),
        "learner_id": str(record.learner_id),
        "kind": "account",
        "target_id": str(record.target_id),
        "deleted_at": "2026-11-05T10:15:00.123456+00:00",
    }


async def test_list_since_returns_records_from_the_moment_in_time_order() -> None:
    journal = _journal()
    fake = _with_fake(journal)
    old = _record(NOW - timedelta(days=3), "domain")
    edge = _record(NOW)
    late_a = _record(NOW + timedelta(hours=1), "account")
    late_b = _record(NOW + timedelta(hours=2))
    for record in (late_b, old, late_a, edge):
        await journal.write(record)
    fake.objects["README.txt"] = b"not a record"  # foreign objects are not records

    found = await journal.list_since(NOW)

    assert found == [edge, late_a, late_b]
    assert fake.calls[-1] == ("list", "quack-deletions", "20261105T101500.123456Z")
    assert await journal.list_since(NOW + timedelta(days=1)) == []
    assert await journal.list_since(NOW - timedelta(days=9)) == [old, edge, late_a, late_b]


async def test_a_record_that_cannot_be_read_is_not_skipped() -> None:
    """A missed record is a deletion the reconcile would not redo: fail, do not hide it."""
    journal = _journal()
    fake = _with_fake(journal)
    fake.objects[f"20261105T110000.000000Z-{uuid4()}.json"] = b'{"kind": "topic"}'
    with pytest.raises(DeletionJournalUnavailable, match="unreadable"):
        await journal.list_since(NOW)
    fake.objects.clear()
    fake.objects[f"20261105T110000.000000Z-{uuid4()}.json"] = b"not json"
    with pytest.raises(DeletionJournalUnavailable):
        await journal.list_since(NOW)


@pytest.mark.parametrize(
    "error",
    [
        EndpointConnectionError(endpoint_url="https://r2"),
        TimeoutError(),
        ConnectionResetError(),
        _client_error(500),
        _client_error(403),  # wrong key / no access: still "not recorded", not a 500
        _client_error(404),  # bucket missing
    ],
)
async def test_any_failure_is_deletion_journal_unavailable(error: Exception) -> None:
    journal = _journal()
    fake = _with_fake(journal)
    fake.error = error
    with pytest.raises(DeletionJournalUnavailable):
        await journal.write(_record(NOW))
    with pytest.raises(DeletionJournalUnavailable):
        await journal.list_since(NOW)


async def test_unreachable_r2_is_unavailable_not_a_driver_error() -> None:
    """Nothing listens on the port: the real aioboto3 client fails, the journal says so in its
    own words (one call: botocore retries a refused connection with its default backoff)."""
    journal = _journal("http://127.0.0.1:9")
    try:
        with pytest.raises(DeletionJournalUnavailable):
            await journal.write(_record(NOW))
    finally:
        await journal.aclose()


@pytest.mark.parametrize("call", ["write", "list_since"])
async def test_no_endpoint_is_unavailable_and_never_reaches_aws(call: str) -> None:
    """`deletions_s3_endpoint = ""` (not configured) must not fall through to botocore's default
    endpoint (AWS): it was the 500 seen on the stand, now a 503."""
    journal = _journal("")
    with pytest.raises(DeletionJournalUnavailable, match="not configured"):
        if call == "write":
            await journal.write(_record(NOW))
        else:
            await journal.list_since(NOW)


async def test_the_api_answers_503_when_the_journal_is_down() -> None:
    """The `DELETE` endpoints map `DeletionJournalUnavailable` to `SERVICE_UNAVAILABLE`."""
    app = FastAPI()
    install_error_handlers(app)
    journal = _journal("")

    @app.delete("/delete")
    async def delete() -> None:
        try:
            await journal.write(_record(NOW))
        except DeletionJournalUnavailable as exc:
            raise ApiError(
                "SERVICE_UNAVAILABLE", "deletion is not possible now, repeat later", status_code=503
            ) from exc

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.delete("/delete")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
