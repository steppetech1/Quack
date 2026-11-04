"""Requests and responses of the compute WP24 endpoints (ТЗ Фазы 4 WP24, seam S10).

The same models live field for field in `compute/quack_compute/contracts.py`. compute has no
network (Д-§3.2): files come in the request body and results go back in the response; binary
data is base64 in JSON (Phase 4 decision D-C-107). Math endpoints (`/v1/math/*`) are stream B's
(WP11.5).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PdfPagesRequest(_Model):
    pdf_b64: str


class PdfPage(_Model):
    page: int  # 1-based
    text: str
    image_webp_b64: str


class PdfPagesResponse(_Model):
    pages: list[PdfPage]


class ImageRequest(_Model):
    image_b64: str


class AlignResponse(_Model):
    """`aligned = False` — no sheet contour; the image is returned as is (ТЗ WP22.3 п.1)."""

    aligned: bool
    image_webp_b64: str


class CleanDrawingRequest(_Model):
    image_b64: str
    mask_b64: str | None = None  # the drawing region from `segmenter` (Т-§16.3 п.4)


class ImageResponse(_Model):
    image_webp_b64: str


class Stroke(_Model):
    points: list[tuple[float, float]]  # absolute coordinates of the board


class RenderStrokesRequest(_Model):
    strokes: list[Stroke]
    width: int
    height: int


class RenderSnapshotsRequest(_Model):
    html: str
    param_sets: list[dict[str, Any]]


class Snapshot(_Model):
    """One parameter set: the picture, console errors and the "blank canvas" sign
    (Т-§13.6, Т-§22.10)."""

    image_webp_b64: str
    console_errors: list[str]
    blank: bool


class RenderSnapshotsResponse(_Model):
    snapshots: list[Snapshot]


class RenderPdfRequest(_Model):
    html: str


class RenderPdfResponse(_Model):
    """A4 PDF and its page count (Т-§17.6)."""

    pdf_b64: str
    page_count: int


class JobError(_Model):
    """Body of 504 (job timeout)."""

    error: str
