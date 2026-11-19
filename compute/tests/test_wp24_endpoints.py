"""WP24 endpoints on fixtures built in the test (ТЗ Ф4 §8.12 `T-CM2`, `T-CM3`; Т-§1.2,
Т-§13.6, Т-§17.6, Д-§4.1). Runs in Linux (P3N D-C-002); rendering needs Chromium of Playwright
(`playwright install chromium`)."""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Iterator

import cv2
import numpy as np
import pymupdf
import pytest
from fastapi.testclient import TestClient

from quack_compute.app import create_app
from quack_compute.contracts import RenderPdfRequest
from quack_compute.renderer import Renderer
from quack_compute.settings import ComputeSettings


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _image(b64: str, flags: int = cv2.IMREAD_UNCHANGED) -> np.ndarray:
    data = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
    image = cv2.imdecode(data, flags)
    assert image is not None, "not an image"
    return image


def _is_webp(b64: str) -> bool:
    data = base64.b64decode(b64)
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    settings = ComputeSettings(compute_workers=1, compute_tmp_dir=".")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _pdf(pages: list[str]) -> bytes:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def test_pdf_pages(client: TestClient) -> None:
    """T-CM2: PDF → pages with their text and a WebP image each (WP20.2)."""
    response = client.post(
        "/v1/pdf/pages", json={"pdf_b64": _b64(_pdf(["Limits of sequences", "Derivatives"]))}
    )
    assert response.status_code == 200
    pages = response.json()["pages"]
    assert [page["page"] for page in pages] == [1, 2]
    assert "Limits of sequences" in pages[0]["text"]
    assert "Derivatives" in pages[1]["text"]
    for page in pages:
        assert _is_webp(page["image_webp_b64"])
        assert _image(page["image_webp_b64"]).shape[0] > 0


def _sheet_photo() -> np.ndarray:
    """A light sheet as a tilted quadrilateral on a dark table."""
    photo = np.full((600, 800, 3), 40, dtype=np.uint8)
    corners = np.array([[150, 80], [650, 120], [620, 540], [120, 500]], dtype=np.int32)
    cv2.fillPoly(photo, [corners], (235, 235, 235))
    cv2.putText(photo, "x^2", (300, 300), cv2.FONT_HERSHEY_SIMPLEX, 2, (20, 20, 20), 4)
    return photo


def test_image_align_finds_the_sheet(client: TestClient) -> None:
    """T-CM2: photo → the sheet straightened by its contour (WP22.3 п.1)."""
    response = client.post("/v1/image/align", json={"image_b64": _b64(_png(_sheet_photo()))})
    assert response.status_code == 200
    body = response.json()
    assert body["aligned"] is True
    aligned = _image(body["image_webp_b64"], cv2.IMREAD_GRAYSCALE)
    # The dark table is gone: the straightened image is the light sheet.
    assert float(np.mean(aligned)) > 180


def test_image_align_without_contour_returns_the_photo(client: TestClient) -> None:
    """WP22.3 п.1: no sheet contour → the image as is, `aligned = false`."""
    plain = np.full((300, 400, 3), 128, dtype=np.uint8)
    response = client.post("/v1/image/align", json={"image_b64": _b64(_png(plain))})
    body = response.json()
    assert body["aligned"] is False
    assert _image(body["image_webp_b64"]).shape[:2] == (300, 400)


def test_image_clean_drawing_removes_background(client: TestClient) -> None:
    """T-CM2: drawing on paper → background transparent, the lines stay (Т-§16.3 п.4)."""
    drawing = np.full((200, 200, 3), 240, dtype=np.uint8)
    cv2.line(drawing, (20, 20), (180, 180), (10, 10, 10), 5)
    mask = np.zeros((200, 200), dtype=np.uint8)
    mask[10:190, 10:190] = 255
    response = client.post(
        "/v1/image/clean-drawing",
        json={"image_b64": _b64(_png(drawing)), "mask_b64": _b64(_png(mask))},
    )
    assert response.status_code == 200
    cleaned = _image(response.json()["image_webp_b64"])
    assert cleaned.shape == (200, 200, 4)
    alpha = cleaned[:, :, 3]
    assert alpha[100, 100] > 200  # on the line
    assert alpha[100, 30] < 50  # paper inside the mask
    assert alpha[5, 5] < 50  # outside the mask


def test_image_render_strokes(client: TestClient) -> None:
    """T-CM2: strokes in board coordinates → an image of the line (WP21.3)."""
    response = client.post(
        "/v1/image/render-strokes",
        json={
            "strokes": [{"points": [[10, 10], [90, 40]]}, {"points": [[10, 40], [90, 10]]}],
            "width": 100,
            "height": 50,
        },
    )
    assert response.status_code == 200
    image = _image(response.json()["image_webp_b64"], cv2.IMREAD_GRAYSCALE)
    assert image.shape == (50, 100)
    assert int(image.min()) < 100 < int(image.max())


def test_render_pdf_counts_a4_pages(client: TestClient) -> None:
    """T-CM2: HTML → A4 PDF and its page count (Т-§17.6)."""
    short = client.post("/v1/render/pdf", json={"html": "<h1>Limits</h1><p>One page.</p>"})
    assert short.status_code == 200
    body = short.json()
    assert body["page_count"] == 1
    document = pymupdf.open(stream=base64.b64decode(body["pdf_b64"]), filetype="pdf")
    width, height = document[0].rect.width, document[0].rect.height
    document.close()
    # A4 is 595.3 × 841.9 pt; Chromium rounds the page to hundredths of an inch.
    assert abs(width - 595.3) < 1.5 and abs(height - 841.9) < 1.5

    long_html = "".join(f"<p>Line {n}</p>" for n in range(300))
    assert client.post("/v1/render/pdf", json={"html": long_html}).json()["page_count"] > 1


def test_render_snapshots(client: TestClient) -> None:
    """T-CM2: HTML + param sets → a snapshot per set, console errors, blank canvas. The page
    reads its set from `window.QUACK_VIZ_PARAMS` (D-B-130)."""
    html = (
        "<body style='margin:0;background:#fff'><canvas id='c' width='200' height='100'></canvas>"
        "<script>const p = window.QUACK_VIZ_PARAMS || {};"
        "if (p.fill) { const g = document.getElementById('c').getContext('2d');"
        "g.fillStyle = '#c00'; g.fillRect(0, 0, 200, 100); }"
        "if (p.fail) { console.error('boom'); }</script></body>"
    )
    response = client.post(
        "/v1/render/snapshots",
        json={"html": html, "param_sets": [{"fill": True}, {}, {"fail": True}]},
    )
    assert response.status_code == 200
    drawn, empty, failed = response.json()["snapshots"]
    assert drawn["blank"] is False and drawn["console_errors"] == []
    assert empty["blank"] is True and empty["console_errors"] == []
    assert failed["console_errors"] == ["boom"]
    for snapshot in (drawn, empty, failed):
        assert _is_webp(snapshot["image_webp_b64"])


def test_render_snapshots_report_uncaught_errors(client: TestClient) -> None:
    """Т-§13.6 п.3: an uncaught exception of the page is a console error too."""
    html = "<script>throw new Error('kaput')</script>"
    response = client.post("/v1/render/snapshots", json={"html": html, "param_sets": []})
    [snapshot] = response.json()["snapshots"]
    assert any("kaput" in error for error in snapshot["console_errors"])


def test_render_snapshots_refuse_every_request_but_the_page(client: TestClient) -> None:
    """Д-§3.2: the HTML is written by a model; every request of the page except the page itself
    is refused by the renderer (net::ERR_FAILED), none is let out to a network; a host that does
    not resolve would fail with another error."""
    html = "<script src='http://elsewhere.quack.internal/lib.js'></script><p>page</p>"
    response = client.post("/v1/render/snapshots", json={"html": html, "param_sets": []})
    assert response.status_code == 200
    [snapshot] = response.json()["snapshots"]
    assert any("ERR_FAILED" in error for error in snapshot["console_errors"]), snapshot


def test_render_jobs_have_the_hard_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """WP24: the render endpoints are under `compute_task_timeout_s` like the pool jobs: a job
    that outlives it is cancelled and the answer is 504."""

    async def hang(self: Renderer, request: object) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(Renderer, "snapshots", hang)
    monkeypatch.setattr(Renderer, "pdf", hang)
    settings = ComputeSettings(compute_workers=1, compute_tmp_dir=".", compute_task_timeout_s=0.3)
    with TestClient(create_app(settings)) as short:
        for path, body in (
            ("/v1/render/snapshots", {"html": "<p>x</p>", "param_sets": []}),
            ("/v1/render/pdf", {"html": "<p>x</p>"}),
        ):
            started = time.monotonic()
            response = short.post(path, json=body)
            assert response.status_code == 504, path
            assert time.monotonic() - started < 5, path


def test_render_snapshots_refuse_too_many_param_sets(client: TestClient) -> None:
    """WP24: the number of parameter sets of one call is bounded by `render_max_param_sets`."""
    limit = ComputeSettings().render_max_param_sets
    sets: list[dict[str, int]] = [{"n": n} for n in range(limit + 1)]
    response = client.post("/v1/render/snapshots", json={"html": "<p>x</p>", "param_sets": sets})
    assert response.status_code == 422
    ok = client.post("/v1/render/snapshots", json={"html": "<p>x</p>", "param_sets": sets[:2]})
    assert ok.status_code == 200 and len(ok.json()["snapshots"]) == 2


def test_image_render_strokes_refuses_a_huge_image_and_skips_empty_strokes(
    client: TestClient,
) -> None:
    """WP21.3: the size of the image comes from client coordinates; past
    `render_strokes_max_side_px` (or below 1) the answer is 422 and nothing is allocated; a
    stroke without points draws nothing instead of failing the call."""
    limit = ComputeSettings().render_strokes_max_side_px
    for width, height in ((limit + 1, 10), (10, 100_000), (0, 10), (10, -1)):
        response = client.post(
            "/v1/image/render-strokes",
            json={"strokes": [{"points": [[0, 0]]}], "width": width, "height": height},
        )
        assert response.status_code == 422, (width, height)
    response = client.post(
        "/v1/image/render-strokes",
        json={"strokes": [{"points": []}, {"points": [[1, 1], [5, 5]]}], "width": 10, "height": 10},
    )
    assert response.status_code == 200
    assert _image(response.json()["image_webp_b64"], cv2.IMREAD_GRAYSCALE).shape == (10, 10)


async def test_chromium_idle_close() -> None:
    """T-CM3: the browser starts on the first render job, serves the next with a new page and
    closes after `chromium_idle_close_s` without jobs."""
    renderer = Renderer(idle_close_s=1)
    try:
        assert renderer.browser_open is False
        await renderer.pdf(RenderPdfRequest(html="<p>first</p>"))
        assert renderer.browser_open is True
        await renderer.pdf(RenderPdfRequest(html="<p>second</p>"))
        assert renderer.browser_open is True
        await asyncio.sleep(1.5)
        assert renderer.browser_open is False
        result = await renderer.pdf(RenderPdfRequest(html="<p>again</p>"))
        assert result.page_count == 1
        assert renderer.browser_open is True
    finally:
        await renderer.close()
    assert renderer.browser_open is False
