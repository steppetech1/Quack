"""Object keys of learner files (Т-§3.3, ТЗ Ф4 WP19).

Every key is under `learners/{learner_id}/`; objects are immutable, so a key is never reused.
A key must not come only from Postgres counters (`seq`, chunk number): after a restore from a
dump they roll back and a new object would overwrite an old one (Т-§3.3). Keys of objects that
may be written more than once per id carry a ULID. The ULID is written on the standard
library: no library for it is named in the documents (ТЗ Ф4 §4.6).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

from quack.storage.objects import learner_prefix

# Crockford base32: 48 bits of milliseconds, then 80 random bits, 26 characters.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_CHARS = 26
_RANDOM_BITS = 80


def new_ulid(at: datetime | None = None) -> str:
    """A ULID: sortable by time to the millisecond, random within it."""
    moment = at or datetime.now(UTC)
    millis = int(moment.timestamp() * 1000)
    value = (millis << _RANDOM_BITS) | secrets.randbits(_RANDOM_BITS)
    chars = []
    for _ in range(_ULID_CHARS):
        value, digit = divmod(value, 32)
        chars.append(_CROCKFORD[digit])
    return "".join(reversed(chars))


def material_original_key(learner_id: UUID, material_id: UUID, ext: str) -> str:
    return f"{learner_prefix(learner_id)}materials/{material_id}/original.{ext}"


def material_page_key(learner_id: UUID, material_id: UUID, page: int) -> str:
    return f"{learner_prefix(learner_id)}materials/{material_id}/pages/{page}.webp"


def canvas_chunk_prefix(learner_id: UUID, session_id: UUID, canvas_id: UUID) -> str:
    """The chunks of one canvas: a session has several canvases (WP21.6, D-C-135)."""
    return f"{learner_prefix(learner_id)}canvas/{session_id}/{canvas_id}/"


def canvas_chunk_key(learner_id: UUID, session_id: UUID, canvas_id: UUID, chunk: int) -> str:
    return f"{canvas_chunk_prefix(learner_id, session_id, canvas_id)}{chunk}-{new_ulid()}.bin"


def photo_original_key(learner_id: UUID, photo_id: UUID) -> str:
    return f"{learner_prefix(learner_id)}photos/{photo_id}/original-{new_ulid()}.webp"


def photo_drawing_key(learner_id: UUID, photo_id: UUID, n: int) -> str:
    """A drawing cut from the photo, without its paper (WP22.3 step 5)."""
    return f"{learner_prefix(learner_id)}photos/{photo_id}/drawing_{n}-{new_ulid()}.webp"


def photo_crop_key(learner_id: UUID, photo_id: UUID, crop: int) -> str:
    return f"{learner_prefix(learner_id)}photos/{photo_id}/crop_{crop}-{new_ulid()}.webp"


def export_key(learner_id: UUID, export_id: UUID) -> str:
    return f"{learner_prefix(learner_id)}exports/{export_id}-{new_ulid()}.zip"


def viz_key(learner_id: UUID, viz_id: UUID) -> str:
    return f"{learner_prefix(learner_id)}viz/{viz_id}-{new_ulid()}.html"


def thumbnail_key(learner_id: UUID, canvas_id: UUID, block_id: str) -> str:
    return f"{learner_prefix(learner_id)}thumbnails/{canvas_id}/{block_id}-{new_ulid()}.webp"
