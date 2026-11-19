"""Uploads through the API: the type by content, not by name, and the size limit (Т-§19.8,
Д-§8.4, ТЗ Ф4 WP19). Only PDF and images; anything else is `UPLOAD_REJECTED`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

type UploadKind = Literal["pdf", "png", "jpeg", "webp"]
type RejectReason = Literal["type", "size"]

ALL_KINDS: frozenset[UploadKind] = frozenset({"pdf", "png", "jpeg", "webp"})
CONTENT_TYPES: dict[UploadKind, str] = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}
EXTENSIONS: dict[UploadKind, str] = {"pdf": "pdf", "png": "png", "jpeg": "jpg", "webp": "webp"}
_BYTES_PER_MB = 1024 * 1024
# ТЗ Ф4 §4.5: wrong type — 415, too large — 413.
_STATUS: dict[RejectReason, int] = {"type": 415, "size": 413}


class UploadRejected(Exception):
    """The upload is not accepted; the API answers `UPLOAD_REJECTED` (ТЗ Ф4 §4.5)."""

    def __init__(self, reason: RejectReason, message: str) -> None:
        super().__init__(message)
        self.reason: RejectReason = reason
        self.status_code = _STATUS[reason]


@dataclass(frozen=True)
class DetectedUpload:
    kind: UploadKind

    @property
    def content_type(self) -> str:
        return CONTENT_TYPES[self.kind]

    @property
    def extension(self) -> str:
        return EXTENSIONS[self.kind]


def _sniff(data: bytes) -> UploadKind | None:
    """File signatures of PDF, PNG, JPEG and WebP."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def check_upload_size(size: int | None, *, max_mb: int) -> None:
    """Over `max_mb` (`upload_max_mb`) → `UploadRejected` (413). `None` — the size is not known
    yet (the body is checked again once read)."""
    if size is not None and size > max_mb * _BYTES_PER_MB:
        raise UploadRejected("size", f"upload is larger than {max_mb} MB")


class _ReadableUpload(Protocol):
    """What of `starlette.datastructures.UploadFile` `read_upload` needs."""

    @property
    def size(self) -> int | None: ...

    async def read(self, size: int = -1, /) -> bytes: ...


async def read_upload(upload: _ReadableUpload, *, max_mb: int) -> bytes:
    """The body of an uploaded file, never more than `max_mb` into memory (Т-§19.8): the size the
    framework already knows is checked before the first byte is read, and the read itself stops
    one byte past the limit, so a file of any size is `UploadRejected` (413), not an
    out-of-memory kill of `api`."""
    check_upload_size(upload.size, max_mb=max_mb)
    data = await upload.read(max_mb * _BYTES_PER_MB + 1)
    check_upload_size(len(data), max_mb=max_mb)
    return data


def detect_upload(
    data: bytes,
    *,
    max_mb: int,
    allowed: frozenset[UploadKind] = ALL_KINDS,
    filename: str | None = None,
) -> DetectedUpload:
    """The kind of an upload by its first bytes; `filename` is never looked at (Т-§19.8).
    Over `max_mb` (`upload_max_mb`) or not one of `allowed` → `UploadRejected`."""
    del filename  # the name and its extension do not decide the type
    check_upload_size(len(data), max_mb=max_mb)
    kind = _sniff(data)
    if kind is None or kind not in allowed:
        kinds = ", ".join(sorted(allowed))
        raise UploadRejected("type", f"upload must be one of: {kinds}")
    return DetectedUpload(kind)
