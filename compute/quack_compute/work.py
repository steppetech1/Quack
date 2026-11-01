"""Job bodies of the WP24 endpoints; each runs in a pool process (`TaskPool.run`).

Stage I (ТЗ Фазы 4 §0.3): signatures only. Implementations: PyMuPDF (`pdf_pages`), OpenCV
(`image_*`) — WP24. Rendering lives in `quack_compute.renderer` (one Chromium per service).
"""

from __future__ import annotations

from quack_compute.contracts import (
    AlignResponse,
    CleanDrawingRequest,
    ImageRequest,
    ImageResponse,
    PdfPagesRequest,
    PdfPagesResponse,
    RenderStrokesRequest,
)


def pdf_pages(request: PdfPagesRequest) -> PdfPagesResponse:
    raise NotImplementedError


def image_align(request: ImageRequest) -> AlignResponse:
    raise NotImplementedError


def image_clean_drawing(request: CleanDrawingRequest) -> ImageResponse:
    raise NotImplementedError


def image_render_strokes(request: RenderStrokesRequest) -> ImageResponse:
    raise NotImplementedError
