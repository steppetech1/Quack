"""Upload and read of materials (ТЗ Ф4 WP20.1; Т-§2.3, Т-§3.1, Т-§19.8).

The file goes to the object store first, then the `content.materials` row and
`material.uploaded` in one transaction (Т-§2.3); the dispatcher queues `material.parse` (S6,
stream A). The domain must be the learner's: its `domain.created` is in the event log (law 1;
`quack.graph.read` is not there yet, D-C-113).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.events.event_types import MaterialUploaded
from quack.events.log import NewEvent, append_events
from quack.storage.keys import material_original_key
from quack.storage.objects import ObjectStore
from quack.storage.uploads import detect_upload

# D-C-113: `content.materials.status` until `material.parse` runs (WP20.2 sets the next one).
STATUS_UPLOADED = "uploaded"

_DOMAIN_OF_LEARNER = (
    "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'domain.created' AND payload ->> 'domain_id' = %(domain_id)s LIMIT 1"
)
# The session is the learner's when its `session.started` is in the learner's log (law 1), as for
# canvases: a foreign `session_id` must not reach the event and the parse job.
_SESSION_OF_LEARNER = (
    "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'session.started' AND payload ->> 'session_id' = %(session_id)s LIMIT 1"
)
_INSERT_MATERIAL = (
    "INSERT INTO content.materials "
    "(material_id, learner_id, domain_id, session_id, object_key, mime, status) "
    "VALUES (%(material_id)s, %(learner_id)s, %(domain_id)s, %(session_id)s, %(object_key)s, "
    "%(mime)s, %(status)s) "
    "RETURNING material_id, domain_id, session_id, mime, status, pages_count, created_at"
)
_MATERIALS_OF_DOMAIN = (
    "SELECT material_id, domain_id, session_id, mime, status, pages_count, created_at "
    "FROM content.materials WHERE learner_id = %(learner_id)s AND domain_id = %(domain_id)s "
    "ORDER BY created_at DESC, material_id"
)
_MATERIAL = (
    "SELECT material_id, domain_id, session_id, mime, status, pages_count, created_at, "
    "object_key FROM content.materials "
    "WHERE learner_id = %(learner_id)s AND material_id = %(material_id)s"
)
_PAGES = (
    "SELECT page, text, image_key FROM content.material_pages "
    "WHERE learner_id = %(learner_id)s AND material_id = %(material_id)s ORDER BY page"
)


class MaterialNotFound(Exception):
    """No such material (or domain) of this learner: 404."""


@dataclass(frozen=True)
class MaterialPage:
    page: int
    text: str
    image_url: str | None


@dataclass(frozen=True)
class Material:
    material_id: UUID
    domain_id: UUID | None
    session_id: UUID | None
    mime: str
    status: str
    pages_count: int | None
    created_at: datetime


@dataclass(frozen=True)
class MaterialDetail:
    material: Material
    file_url: str
    pages: list[MaterialPage]


async def upload_material(
    db: Db,
    storage: ObjectStore,
    settings: Settings,
    *,
    learner_id: UUID,
    domain_id: UUID,
    data: bytes,
    session_id: UUID | None = None,
    redis: Redis | None = None,
) -> Material:
    """Store a PDF or an image (type by content, `upload_max_mb`) and record it.
    `UploadRejected`, `MaterialNotFound` (unknown domain or a session that is not the learner's),
    `StorageUnavailable`."""
    upload = detect_upload(data, max_mb=settings.upload_max_mb)
    await _check_domain(db, learner_id, domain_id)
    if session_id is not None:
        await _check_session(db, learner_id, session_id)
    material_id = uuid4()
    object_key = material_original_key(learner_id, material_id, upload.extension)
    # The object first: a failed write leaves no row and no event (Т-§20.7).
    await storage.put(object_key, data, upload.content_type)
    event = NewEvent(
        type="material.uploaded",
        payload=MaterialUploaded(
            material_id=material_id,
            object_key=object_key,
            mime=upload.content_type,
            session_id=session_id,
        ),
        actor="learner",
        idempotency_key=f"material.uploaded:{material_id}",
        domain_id=domain_id,
        session_id=session_id,
    )
    async with db.transaction():
        row = await db.fetch_one(
            _INSERT_MATERIAL,
            {
                "material_id": material_id,
                "learner_id": learner_id,
                "domain_id": domain_id,
                "session_id": session_id,
                "object_key": object_key,
                "mime": upload.content_type,
                "status": STATUS_UPLOADED,
            },
        )
        await append_events(db, learner_id, [event], redis=redis)
    assert row is not None
    return _material(row)


async def list_materials(db: Db, *, learner_id: UUID, domain_id: UUID) -> list[Material]:
    """`GET /v1/domains/{id}/materials`, newest first; `MaterialNotFound` for a domain that is
    not the learner's (T-E3: 404, not an empty list)."""
    await _check_domain(db, learner_id, domain_id)
    rows = await db.fetch_all(
        _MATERIALS_OF_DOMAIN, {"learner_id": learner_id, "domain_id": domain_id}
    )
    return [_material(row) for row in rows]


async def get_material(
    db: Db, storage: ObjectStore, settings: Settings, *, learner_id: UUID, material_id: UUID
) -> MaterialDetail:
    """`GET /v1/materials/{id}`: pages with their text and signed links (`object_url_ttl_s`)."""
    params = {"learner_id": learner_id, "material_id": material_id}
    row = await db.fetch_one(_MATERIAL, params)
    if row is None:
        raise MaterialNotFound(f"material {material_id} not found")
    ttl = settings.object_url_ttl_s
    pages = [
        MaterialPage(
            page=page["page"],
            text=page["text"],
            image_url=None
            if page["image_key"] is None
            else await storage.presigned_get_url(page["image_key"], ttl),
        )
        for page in await db.fetch_all(_PAGES, params)
    ]
    return MaterialDetail(
        material=_material(row),
        file_url=await storage.presigned_get_url(row["object_key"], ttl),
        pages=pages,
    )


async def _check_domain(db: Db, learner_id: UUID, domain_id: UUID) -> None:
    found = await db.fetch_one(
        _DOMAIN_OF_LEARNER, {"learner_id": learner_id, "domain_id": str(domain_id)}
    )
    if found is None:
        raise MaterialNotFound(f"domain {domain_id} not found")


async def _check_session(db: Db, learner_id: UUID, session_id: UUID) -> None:
    found = await db.fetch_one(
        _SESSION_OF_LEARNER, {"learner_id": learner_id, "session_id": str(session_id)}
    )
    if found is None:
        raise MaterialNotFound(f"session {session_id} not found")


def _material(row: dict[str, Any]) -> Material:
    return Material(
        material_id=row["material_id"],
        domain_id=row["domain_id"],
        session_id=row["session_id"],
        mime=row["mime"],
        status=row["status"],
        pages_count=row["pages_count"],
        created_at=row["created_at"],
    )
