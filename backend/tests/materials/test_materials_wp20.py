"""WP20.1 upload and read of materials, WP20.5 full-text search (ТЗ Ф4 §8.11 `T-MT5`;
Т-§2.3, Т-§3.1, Т-§19.8). Integration tests on the Phase 4 database (`pg_db`)."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.events.registry import TEXT_TYPES
from quack.materials import search as search_module
from quack.materials.library import (
    MaterialNotFound,
    get_material,
    list_materials,
    upload_material,
)
from quack.materials.search import search_events
from quack.storage.objects import StorageUnavailable
from quack.storage.uploads import UploadRejected
from tests.conftest import BACKEND_DIR
from tests.db.fixtures import create_learner
from tests.support.storage import BrokenObjectStore, InMemoryObjectStore

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"
EXE = b"MZ\x90\x00\x03\x00\x00\x00"


async def _event(
    db: Db,
    learner: UUID,
    type_: str,
    payload: dict[str, Any],
    session_id: UUID | None = None,
) -> None:
    event = NewEvent(
        type=type_,
        payload=payload,
        actor="learner",
        idempotency_key=str(uuid4()),
        session_id=session_id,
    )
    await append_events(db, learner, [event])


async def _domain(db: Db, learner: UUID) -> UUID:
    domain_id = uuid4()
    await _event(db, learner, "domain.created", {"domain_id": str(domain_id), "name": "Calculus"})
    return domain_id


async def _session(db: Db, learner: UUID, domain: UUID) -> UUID:
    """A session of the learner: its `session.started` is in the log."""
    session_id = uuid4()
    await _event(
        db,
        learner,
        "session.started",
        {"session_id": str(session_id), "domain_id": str(domain), "intent": "learn"},
        session_id=session_id,
    )
    return session_id


async def test_upload_refuses_a_session_of_another_learner(pg_db: Db, settings: Settings) -> None:
    """A foreign or made-up `session_id` must not reach `material.uploaded` and the parse job."""
    owner = await create_learner(pg_db)
    stranger = await create_learner(pg_db)
    owner_domain = await _domain(pg_db, owner)
    foreign = await _session(pg_db, owner, owner_domain)
    own_domain = await _domain(pg_db, stranger)
    storage = InMemoryObjectStore()
    for session_id in (foreign, uuid4()):
        with pytest.raises(MaterialNotFound):
            await upload_material(
                pg_db,
                storage,
                settings,
                learner_id=stranger,
                domain_id=own_domain,
                data=PDF,
                session_id=session_id,
            )
    assert storage.objects == {}
    rows = await pg_db.fetch_all(
        "SELECT 1 FROM core.events WHERE type = 'material.uploaded' AND learner_id = %(l)s",
        {"l": stranger},
    )
    assert rows == []


async def test_upload_material_stores_object_row_and_event(pg_db: Db, settings: Settings) -> None:
    """WP20.1: object under `learners/{id}/materials/{material_id}/original.pdf`, the row and
    `material.uploaded` in one transaction (Т-§2.3)."""
    learner = await create_learner(pg_db)
    domain = await _domain(pg_db, learner)
    storage = InMemoryObjectStore()
    session_id = await _session(pg_db, learner, domain)

    material = await upload_material(
        pg_db,
        storage,
        settings,
        learner_id=learner,
        domain_id=domain,
        data=PDF,
        session_id=session_id,
    )
    key = f"learners/{learner}/materials/{material.material_id}/original.pdf"
    assert storage.objects[key][0] == PDF and storage.objects[key][1] == "application/pdf"
    assert material.mime == "application/pdf" and material.domain_id == domain
    events = await pg_db.fetch_all(
        "SELECT type, payload, session_id FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'material.uploaded'",
        {"learner_id": learner},
    )
    assert [e["payload"] for e in events] == [
        {
            "material_id": str(material.material_id),
            "object_key": key,
            "mime": "application/pdf",
            "session_id": str(session_id),
        }
    ]
    [listed] = await list_materials(pg_db, learner_id=learner, domain_id=domain)
    assert listed.material_id == material.material_id and listed.session_id == session_id


async def test_upload_refuses_bad_type_unknown_domain_and_storage_down(
    pg_db: Db, settings: Settings
) -> None:
    """Т-§19.8: type by content; a domain of another learner is not found; storage down
    writes nothing (Т-§20.7)."""
    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    domain = await _domain(pg_db, learner)
    storage = InMemoryObjectStore()
    with pytest.raises(UploadRejected):
        await upload_material(
            pg_db, storage, settings, learner_id=learner, domain_id=domain, data=EXE
        )
    with pytest.raises(MaterialNotFound):
        await upload_material(
            pg_db, storage, settings, learner_id=other, domain_id=domain, data=PDF
        )
    with pytest.raises(StorageUnavailable):
        await upload_material(
            pg_db, BrokenObjectStore(), settings, learner_id=learner, domain_id=domain, data=PDF
        )
    assert storage.objects == {}
    rows = await pg_db.fetch_all(
        "SELECT material_id FROM content.materials WHERE learner_id = %(learner_id)s",
        {"learner_id": learner},
    )
    assert rows == []


async def test_get_material_pages_and_links(pg_db: Db, settings: Settings) -> None:
    """WP20.1: pages with their text, signed links for the file and page images; another
    learner gets 404."""
    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    domain = await _domain(pg_db, learner)
    storage = InMemoryObjectStore()
    material = await upload_material(
        pg_db, storage, settings, learner_id=learner, domain_id=domain, data=PDF
    )
    image_key = f"learners/{learner}/materials/{material.material_id}/pages/1.webp"
    await storage.put(image_key, b"RIFF....WEBP", "image/webp")
    await pg_db.execute(
        "INSERT INTO content.material_pages (material_id, page, learner_id, text, image_key) "
        "VALUES (%(m)s, 1, %(learner_id)s, 'Limits', %(k)s)",
        {"m": material.material_id, "learner_id": learner, "k": image_key},
    )
    detail = await get_material(
        pg_db, storage, settings, learner_id=learner, material_id=material.material_id
    )
    assert detail.material.material_id == material.material_id
    assert f"ttl={settings.object_url_ttl_s}" in detail.file_url
    assert [(p.page, p.text) for p in detail.pages] == [(1, "Limits")]
    assert detail.pages[0].image_url is not None and image_key in detail.pages[0].image_url
    with pytest.raises(MaterialNotFound):
        await get_material(
            pg_db, storage, settings, learner_id=other, material_id=material.material_id
        )


async def test_fulltext_english(pg_db: Db) -> None:
    """T-MT5: «where did I solve the integral» finds the event with its session, `seq`, a
    snippet and the type; another learner's text and a deleted event are not found."""
    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    session_id = uuid4()
    await _event(
        pg_db,
        learner,
        "message.sent",
        {"text": "I solved the integrals of x squared"},
        session_id=session_id,
    )
    await _event(pg_db, learner, "message.sent", {"text": "Limits of sequences"})
    await _event(pg_db, other, "message.sent", {"text": "Another integral"})

    [hit] = await search_events(
        pg_db, learner_id=learner, query="where I solved integral", limit=10
    )
    assert hit.session_id == session_id and hit.type == "message.sent" and hit.seq >= 1
    assert "integral" in hit.snippet.lower()

    await pg_db.execute(
        "DELETE FROM core.events WHERE learner_id = %(learner_id)s AND seq = %(seq)s",
        {"learner_id": learner, "seq": hit.seq},
    )
    assert await search_events(pg_db, learner_id=learner, query="integral", limit=10) == []


def _quoted_types(text: str) -> set[str]:
    return set(re.findall(r"'([a-z_]+\.[a-z_]+)'", text))


def test_search_types_match_the_registry_and_the_index() -> None:
    """The type list of the search, `TEXT_TYPES` of the registry and the predicate of the
    partial index `idx_events_fts_en` are one list (review of #98): a new text type that is
    missing in the query would turn the search into a scan without the index."""
    migration = next((BACKEND_DIR / "db" / "migrations").glob("*_baseline.sql"))
    up = migration.read_text(encoding="utf-8").split("-- migrate:down")[0]
    index = up[up.index("CREATE INDEX idx_events_fts_en") :].split(";")[0]
    query = search_module._SEARCH  # pyright: ignore[reportPrivateUsage]
    assert _quoted_types(query) == set(TEXT_TYPES)
    assert _quoted_types(index) == set(TEXT_TYPES)
