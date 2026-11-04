"""A photo of the whole sheet (ТЗ Ф4 WP22.2; Т-§22.6, Т-§16.2).

`POST /v1/canvases/{id}/photos` takes a WebP of the sheet from the laptop or from the phone
paired to this canvas. The object goes to the store first, then `photo.uploaded {canvas_id,
photo_id, object_key}` (Т-§2.3): a failed write leaves no event (Т-§20.7). The dispatcher
queues `photo.pipeline` (P1, `lock_key = canvas:{id}`, stream A); the pipeline is WP22.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from redis.asyncio import Redis

from quack.canvas.boards import get_canvas
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import PhotoUploaded
from quack.events.log import NewEvent, append_events
from quack.storage.keys import photo_original_key
from quack.storage.objects import ObjectStore
from quack.storage.uploads import UploadKind, detect_upload

# Т-§22.6: the phone sends the sheet as WebP.
PHOTO_KINDS: frozenset[UploadKind] = frozenset({"webp"})


@dataclass(frozen=True)
class Photo:
    photo_id: UUID
    canvas_id: UUID
    object_key: str


async def upload_photo(
    db: Db,
    storage: ObjectStore,
    settings: Settings,
    *,
    learner_id: UUID,
    canvas_id: UUID,
    data: bytes,
    redis: Redis | None = None,
) -> Photo:
    """Store the sheet and record it. `CanvasNotFound`, `UploadRejected` (not WebP by content
    or over `upload_max_mb`), `StorageUnavailable`."""
    canvas = await get_canvas(db, learner_id=learner_id, canvas_id=canvas_id)
    upload = detect_upload(data, max_mb=settings.upload_max_mb, allowed=PHOTO_KINDS)
    photo_id = uuid4()
    object_key = photo_original_key(learner_id, photo_id)
    await storage.put(object_key, data, upload.content_type)
    event = NewEvent(
        type="photo.uploaded",
        payload=PhotoUploaded(canvas_id=canvas_id, photo_id=photo_id, object_key=object_key),
        actor="learner",
        idempotency_key=f"photo.uploaded:{photo_id}",
        session_id=canvas.session_id,
    )
    await append_events(db, learner_id, [event], redis=redis)
    return Photo(photo_id=photo_id, canvas_id=canvas_id, object_key=object_key)
