"""Seam S10 (ТЗ Фазы 4 WP24, §8.1): `ComputeClient` methods of the WP24 endpoints on the stub."""

from __future__ import annotations

import json

import pytest

from quack.compute_client.client import (
    ComputeJobTimeout,
    ComputeUnavailableError,
    create_compute_client,
)
from quack.compute_client.contracts import (
    AlignResponse,
    ImageRequest,
    PdfPage,
    PdfPagesRequest,
    PdfPagesResponse,
    RenderPdfRequest,
    RenderPdfResponse,
)
from quack.config import Settings
from tests.support.compute import WP24_PATHS, ComputeScenario, scripted_compute


async def test_wp24_methods_post_their_endpoint_and_parse_the_answer() -> None:
    scenario = ComputeScenario()
    pages = PdfPagesResponse(pages=[PdfPage(page=1, text="Lecture 1", image_webp_b64="AA==")])
    scenario.push_for("/v1/pdf/pages", pages)
    scenario.push_for("/v1/render/pdf", RenderPdfResponse(pdf_b64="JVBERg==", page_count=1))
    scenario.push_for("/v1/image/align", AlignResponse(aligned=False, image_webp_b64="AA=="))
    client = scripted_compute(scenario)
    try:
        assert await client.pdf_pages(PdfPagesRequest(pdf_b64="JVBERg==")) == pages
        rendered = await client.render_pdf(RenderPdfRequest(html="<p>x</p>"))
        assert rendered.page_count == 1
        aligned = await client.image_align(ImageRequest(image_b64="AA=="))
        assert aligned.aligned is False
    finally:
        await client.aclose()
    sent = [(r.url.path, json.loads(r.content)) for r in scenario.requests]
    assert sent == [
        ("/v1/pdf/pages", {"pdf_b64": "JVBERg=="}),
        ("/v1/render/pdf", {"html": "<p>x</p>"}),
        ("/v1/image/align", {"image_b64": "AA=="}),
    ]


async def test_job_timeout_and_unavailable() -> None:
    scenario = ComputeScenario()
    scenario.push_for("/v1/pdf/pages", 504)
    client = scripted_compute(scenario)
    try:
        with pytest.raises(ComputeJobTimeout):
            await client.pdf_pages(PdfPagesRequest(pdf_b64="JVBERg=="))
        with pytest.raises(ComputeUnavailableError):  # empty queue: compute is down
            await client.pdf_pages(PdfPagesRequest(pdf_b64="JVBERg=="))
    finally:
        await client.aclose()


def test_stub_covers_every_wp24_endpoint() -> None:
    assert set(ComputeScenario().answers) == set(WP24_PATHS)


async def test_job_timeout_is_longer_than_the_compute_one() -> None:
    settings = Settings()
    client = create_compute_client(settings)
    try:
        assert client.timeout_sec == settings.compute_timeout_sec
        assert client.job_timeout_sec > settings.compute_task_timeout_s
    finally:
        await client.aclose()
