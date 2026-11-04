"""WP23.7: the job `export.data` — the archive of the learner's data (ТЗ Ф4 WP23.7; Т-§19.6;
issue #263; D-C-231): events as JSON Lines, documents as JSON, the objects as files, the artifacts
in a readable form; the archive goes to the storage, the link lives `export_link_ttl_hours`."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

from quack.artifacts.data_export import export_learner_data
from quack.artifacts.documents import SystemBlock, write_system_blocks
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.jobs.runner import JOB_HANDLERS, JobContext
from quack.storage.objects import StorageUnavailable
from tests.db.fixtures import create_learner
from tests.support.storage import InMemoryObjectStore

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


class _PutFails(InMemoryObjectStore):
    async def put(self, key: str, data: bytes, content_type: str) -> None:
        raise StorageUnavailable("down")


def _ctx(db: Db, store: InMemoryObjectStore, settings: Settings) -> JobContext:
    return cast(JobContext, SimpleNamespace(db=db, storage=store, settings=settings))


async def _event(db: Db, learner: UUID, text: str) -> None:
    await append_events(
        db,
        learner,
        [
            NewEvent(
                type="topic.created",
                payload={
                    "topic_id": uuid4(), "domain_id": uuid4(), "name": text, "origin": "session"
                },
                actor="code",
                idempotency_key=f"topic:{text}",
            )
        ],
    )  # fmt: skip


async def _populate(db: Db, store: InMemoryObjectStore, learner: UUID) -> UUID:
    await _event(db, learner, "Limits")
    await _event(db, learner, "Series")
    topic_id = uuid4()
    await write_system_blocks(
        db, learner_id=learner, kind="notes", owner_id=topic_id,
        blocks=[SystemBlock(kind="definition", content="A limit is $L$.")],
        created_by="artifact_writer@1",
    )  # fmt: skip
    await store.put(f"learners/{learner}/materials/m1/original.pdf", b"%PDF-bytes", "application/pdf")
    return topic_id


def _zip(store: InMemoryObjectStore, key: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(store.objects[key][0]))


async def _export_row(db: Db, export_id: UUID) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT status, object_key, expires_at FROM content.exports WHERE export_id = %(e)s",
        {"e": export_id},
    )


async def _completed(db: Db, learner: UUID) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(l)s AND type = 'agent.completed'",
        {"l": learner},
    )
    return [r["payload"] for r in rows]


async def test_archive_holds_events_documents_objects_and_readable_artifacts(
    pg_db: Db, settings: Settings
) -> None:
    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    store = InMemoryObjectStore()
    topic_id = await _populate(pg_db, store, learner)
    await _event(pg_db, other, "Someone else's topic")
    export_id = uuid4()

    await export_learner_data(
        _ctx(pg_db, store, settings), learner_id=learner, export_id=export_id, now=NOW
    )

    row = await _export_row(pg_db, export_id)
    assert row is not None and row["status"] == "ready"
    key = row["object_key"]
    assert key.startswith(f"learners/{learner}/exports/{export_id}-") and key.endswith(".zip")
    assert row["expires_at"] == NOW + timedelta(hours=settings.export_link_ttl_hours)
    archive = _zip(store, key)
    names = set(archive.namelist())

    lines = [json.loads(x) for x in archive.read("events.jsonl").decode().splitlines()]
    types = [line["type"] for line in lines]
    assert types.count("topic.created") == 2 and "artifact.updated" in types
    assert [line["seq"] for line in lines] == sorted(line["seq"] for line in lines)
    assert "Someone else's topic" not in archive.read("events.jsonl").decode()

    artifacts = json.loads(archive.read("documents/content.artifacts.json"))
    assert [a["artifact_id"] for a in artifacts] and all(a["learner_id"] == str(learner) for a in artifacts)
    assert "documents/content.artifact_versions.json" in names
    assert json.loads(archive.read("documents/core.learners.json"))[0]["learner_id"] == str(learner)

    assert archive.read("objects/materials/m1/original.pdf") == b"%PDF-bytes"
    assert not any(n.startswith("objects/exports/") for n in names)  # no archive in an archive

    notes = archive.read(f"artifacts/notes/{topic_id}.md").decode()
    assert "A limit is $L$." in notes and "definition" in notes


async def test_a_repeated_job_adds_nothing(pg_db: Db, settings: Settings) -> None:
    """Law 5: the same export id — the same archive, one `agent.completed`, one object."""
    learner = await create_learner(pg_db)
    store = InMemoryObjectStore()
    await _populate(pg_db, store, learner)
    export_id = uuid4()
    ctx = _ctx(pg_db, store, settings)

    await export_learner_data(ctx, learner_id=learner, export_id=export_id, now=NOW)
    first = await _export_row(pg_db, export_id)
    assert first is not None
    await export_learner_data(ctx, learner_id=learner, export_id=export_id, now=NOW)

    assert await _export_row(pg_db, export_id) == first
    exports = [k for k in store.objects if "/exports/" in k]
    assert exports == [first["object_key"]]
    (done,) = await _completed(pg_db, learner)
    assert done["ok"] is True and done["kind"] == "data_export"
    assert done["request_id"] == str(export_id) and done["result_ref"] == first["object_key"]


async def test_a_failed_job_commits_nothing(pg_db: Db, settings: Settings) -> None:
    """Law 7: the storage does not take the archive — the error goes to the queue; no row, no
    event, no object."""
    learner = await create_learner(pg_db)
    store = InMemoryObjectStore()
    await _populate(pg_db, store, learner)
    export_id = uuid4()
    broken = _PutFails()
    broken.objects.update(store.objects)

    failed = False
    try:
        await export_learner_data(
            _ctx(pg_db, broken, settings), learner_id=learner, export_id=export_id, now=NOW
        )
    except StorageUnavailable:
        failed = True
    assert failed
    assert await _export_row(pg_db, export_id) is None
    assert await _completed(pg_db, learner) == []
    assert not [k for k in broken.objects if "/exports/" in k]


async def test_an_expired_export_is_not_built_again(pg_db: Db, settings: Settings) -> None:
    learner = await create_learner(pg_db)
    store = InMemoryObjectStore()
    export_id = uuid4()
    await pg_db.execute(
        "INSERT INTO content.exports (export_id, learner_id, status) "
        "VALUES (%(e)s, %(l)s, 'expired')",
        {"e": export_id, "l": learner},
    )
    await export_learner_data(
        _ctx(pg_db, store, settings), learner_id=learner, export_id=export_id, now=NOW
    )
    assert store.objects == {}
    row = await _export_row(pg_db, export_id)
    assert row is not None and row["status"] == "expired"


def test_the_handler_is_registered() -> None:
    from quack.runtime import register_all_handlers

    handlers = register_all_handlers()
    assert handlers["export.data"].__module__ == "quack.artifacts.data_export"
    assert JOB_HANDLERS["export.data"] is handlers["export.data"]
