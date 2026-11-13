"""`R2DeletionJournal` (ТЗ Ф4 WP25 п.2, Т-§19.5, Д-§8.5): the record of a deletion in the bucket
`quack-deletions`, against a tiny in-process S3 server (the real client, no mocks of it)."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

import pytest

from quack.storage.deletions import (
    DeletionJournalUnavailable,
    DeletionKind,
    DeletionRecord,
    R2DeletionJournal,
)

BUCKET = "quack-deletions"
T0 = datetime(2026, 11, 13, 10, 0, 0, 123456, tzinfo=UTC)


class _FakeS3(BaseHTTPRequestHandler):
    """PutObject, GetObject and ListObjectsV2 of one bucket; `objects` is shared by the server."""

    # HTTP/1.1: `http.server` then answers `Expect: 100-continue` itself, as botocore waits for it.
    protocol_version = "HTTP/1.1"
    objects: dict[str, bytes]
    fail_with: int | None = None

    def log_message(self, format: str, *args: object) -> None:
        return

    def _reply(self, status: int, body: bytes = b"", content_type: str = "application/xml") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _key(self) -> str | None:
        path = unquote(urlsplit(self.path).path)
        match = re.fullmatch(rf"/{BUCKET}/(.+)", path)
        return match.group(1) if match else None

    def do_PUT(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.fail_with:
            self._reply(self.fail_with)
            return
        key = self._key()
        assert key is not None
        self.objects[key] = body
        self._reply(200)

    def do_GET(self) -> None:
        if self.fail_with:
            self._reply(self.fail_with)
            return
        key = self._key()
        if key is not None:
            if key not in self.objects:
                self._reply(404, b"<Error><Code>NoSuchKey</Code></Error>")
            else:
                self._reply(200, self.objects[key], "application/json")
            return
        after = parse_qs(urlsplit(self.path).query).get("start-after", [""])[0]
        keys = sorted(k for k in self.objects if k > after)
        items = "".join(
            f"<Contents><Key>{k}</Key><Size>{len(self.objects[k])}</Size>"
            "<LastModified>2026-11-13T10:00:00.000Z</LastModified></Contents>"
            for k in keys
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?><ListBucketResult>'
            f"<Name>{BUCKET}</Name><KeyCount>{len(keys)}</KeyCount><MaxKeys>1000</MaxKeys>"
            f"<IsTruncated>false</IsTruncated>{items}</ListBucketResult>"
        )
        self._reply(200, xml.encode())


@pytest.fixture
def s3() -> Iterator[tuple[str, type[_FakeS3]]]:
    handler = type("Handler", (_FakeS3,), {"objects": {}, "fail_with": None})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", handler
    finally:
        server.shutdown()
        server.server_close()


def _journal(endpoint: str) -> R2DeletionJournal:
    return R2DeletionJournal(
        endpoint=endpoint, access_key="key", secret_key="secret", bucket=BUCKET
    )


def _record(at: datetime, kind: DeletionKind = "domain") -> DeletionRecord:
    return DeletionRecord(
        deletion_id=uuid4(), learner_id=uuid4(), kind=kind, target_id=uuid4(), deleted_at=at
    )


async def test_write_puts_the_ids_under_the_time_and_deletion_key(
    s3: tuple[str, type[_FakeS3]],
) -> None:
    """Д-§8.5: `{deleted_at}-{deletion_id}.json`, identifiers only, no learner data."""
    endpoint, server = s3
    journal = _journal(endpoint)
    record = _record(T0)
    try:
        await journal.write(record)
        await journal.write(record)  # a repeat replaces the object with itself
    finally:
        await journal.aclose()
    assert list(server.objects) == [f"2026-11-13T10:00:00.123456Z-{record.deletion_id}.json"]
    body = json.loads(next(iter(server.objects.values())))
    assert set(body) == {"deletion_id", "learner_id", "kind", "target_id", "deleted_at"}
    assert body["deletion_id"] == str(record.deletion_id)
    assert body["learner_id"] == str(record.learner_id)


async def test_list_since_reads_back_records_in_time_order(
    s3: tuple[str, type[_FakeS3]],
) -> None:
    """`ops replay-deletions` walks the records newer than the restore point by time."""
    endpoint, _ = s3
    journal = _journal(endpoint)
    old = _record(T0 - timedelta(days=3), "topic")
    middle = _record(T0)
    new = _record(T0 + timedelta(hours=1), "account")
    try:
        for record in (new, old, middle):
            await journal.write(record)
        assert await journal.list_since(T0 - timedelta(days=5)) == [old, middle, new]
        assert await journal.list_since(T0) == [middle, new]  # `>=`: the boundary is included
        assert await journal.list_since(T0 + timedelta(days=1)) == []
    finally:
        await journal.aclose()


async def test_a_bucket_that_does_not_answer_is_journal_unavailable() -> None:
    """Т-§19.5: no record, no deletion. A refused connection and an unset endpoint (the config
    default) are `DeletionJournalUnavailable`, never a driver error or `NotImplementedError`
    (which made every `DELETE /v1/learner|domains|topics` a 500). One call on the refused
    connection: botocore retries it with its default backoff."""
    refused = _journal("http://127.0.0.1:9")
    unset = _journal("")
    try:
        with pytest.raises(DeletionJournalUnavailable):
            await refused.write(_record(T0))
        with pytest.raises(DeletionJournalUnavailable):
            await unset.write(_record(T0))
        with pytest.raises(DeletionJournalUnavailable):
            await unset.list_since(T0)
    finally:
        await refused.aclose()
        await unset.aclose()


async def test_refusal_and_foreign_objects_are_journal_unavailable(
    s3: tuple[str, type[_FakeS3]],
) -> None:
    endpoint, server = s3
    journal = _journal(endpoint)
    try:
        server.fail_with = 403
        with pytest.raises(DeletionJournalUnavailable):
            await journal.write(_record(T0))
        server.fail_with = None
        server.objects["2026-11-14T00:00:00.000000Z-x.json"] = b"not a record"
        with pytest.raises(DeletionJournalUnavailable):
            await journal.list_since(T0)
    finally:
        await journal.aclose()
