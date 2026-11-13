"""HTML rendering with Playwright and Chromium (Т-§1.2, Т-§13.6, Т-§17.6, ТЗ Фазы 4 WP24).

Chromium starts on the first render job, serves the next jobs with new pages and closes after
`chromium_idle_close_s` without jobs (Д-§4.1, `T-CM3`). One browser per service process.
Visualizations get their param set as `window.QUACK_VIZ_PARAMS` (D-B-130). A printed page gets
its formulas typeset by the pinned KaTeX of the service's own files (WP23.5, D-C-250).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import cv2
import numpy as np
import pymupdf
from playwright.async_api import (
    Browser,
    ConsoleMessage,
    Playwright,
    Route,
    async_playwright,
)

from quack_compute.contracts import (
    RenderPdfRequest,
    RenderPdfResponse,
    RenderSnapshotsRequest,
    RenderSnapshotsResponse,
    Snapshot,
)
from quack_compute.settings import ComputeSettings

# D-B-130: the address a visualization page is served at inside Chromium.
VIZ_PAGE_URL = "http://viz.quack.internal/"
# D-C-250: the address a printed page is served at inside Chromium; KaTeX files under /katex/.
PDF_PAGE_URL = "http://pdf.quack.internal/"
KATEX_URL_PATH = "/katex/"
_KATEX_TYPES = {
    ".js": "text/javascript",
    ".css": "text/css",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
}
# A formula is `<span class="quack-math" data-display="true|false">TeX</span>`. A broken formula
# stays on the page as its source (`throwOnError: false`): the sheet is still printed.
_TYPESET_JS = """async () => {
  for (const el of document.querySelectorAll('.quack-math')) {
    katex.render(el.textContent, el, {
      displayMode: el.dataset.display === 'true',
      throwOnError: false,
      output: 'html',
      strict: 'ignore',
    });
  }
  document.body.offsetHeight;
  await document.fonts.ready;
}"""


def _console_error(errors: list[str], message: ConsoleMessage) -> None:
    if message.type == "error":
        errors.append(message.text)


class Renderer:
    def __init__(self, idle_close_s: int, katex_dir: str = ComputeSettings.katex_dir) -> None:
        self._idle_close_s = idle_close_s
        self._katex_dir = Path(katex_dir).resolve()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._active = 0
        self._idle_timer: asyncio.Task[None] | None = None

    @property
    def browser_open(self) -> bool:
        return self._browser is not None

    async def snapshots(self, request: RenderSnapshotsRequest) -> RenderSnapshotsResponse:
        """A snapshot per param set (Т-§13.6 п.2, D-B-130): the set goes to the page as
        `window.QUACK_VIZ_PARAMS` before any of its scripts; console errors and uncaught page
        errors are collected; `blank` — every pixel of the snapshot has the same colour."""
        browser = await self._acquire()
        try:
            snapshots = [
                await self._snapshot(browser, request.html, params)
                for params in request.param_sets or [{}]
            ]
        finally:
            self._release()
        return RenderSnapshotsResponse(snapshots=snapshots)

    async def _snapshot(self, browser: Browser, html: str, params: dict[str, Any]) -> Snapshot:
        page = await browser.new_page()
        errors: list[str] = []
        page.on("console", lambda message: _console_error(errors, message))
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.add_init_script(f"window.QUACK_VIZ_PARAMS = {json.dumps(params)};")
            # Init scripts run on navigation, not on `set_content`: the page is served from
            # memory at an internal address. The HTML is written by a model (untrusted): every
            # other request of the page is refused, none reaches a network (Д-§3.2).
            await page.route("**/*", lambda route: self._serve_viz_page(route, html))
            await page.goto(VIZ_PAGE_URL, wait_until="load")
            png = await page.screenshot(full_page=True)
        finally:
            await page.close()
        image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("the snapshot is not an image")
        ok, webp = cv2.imencode(".webp", image)
        if not ok:
            raise ValueError("WebP encoding failed")
        return Snapshot(
            image_webp_b64=base64.b64encode(webp.tobytes()).decode(),
            console_errors=errors,
            blank=bool((image == image[0, 0]).all()),
        )

    async def _serve_viz_page(self, route: Route, html: str) -> None:
        if route.request.url == VIZ_PAGE_URL:
            await route.fulfill(body=html, content_type="text/html; charset=utf-8")
        else:
            await route.abort()

    async def pdf(self, request: RenderPdfRequest) -> RenderPdfResponse:
        """HTML → A4 PDF and its page count (Т-§17.6: the one-page check of a cheat sheet).
        The formulas are typeset before printing, so the page count is that of the typeset
        page. No network: the page and KaTeX come from memory and disk, every other request is
        refused."""
        browser = await self._acquire()
        try:
            page = await browser.new_page()
            try:
                await page.route("**/*", lambda route: self._serve_pdf_page(route, request.html))
                await page.goto(PDF_PAGE_URL, wait_until="load")
                if await page.locator(".quack-math").count():
                    base = PDF_PAGE_URL.rstrip("/") + KATEX_URL_PATH
                    await page.add_style_tag(url=base + "katex.min.css")
                    await page.add_script_tag(url=base + "katex.min.js")
                    await page.evaluate(_TYPESET_JS)
                data = await page.pdf(format="A4", print_background=True)
            finally:
                await page.close()
        finally:
            self._release()
        document = pymupdf.open(stream=data, filetype="pdf")
        try:
            page_count = document.page_count
        finally:
            document.close()
        return RenderPdfResponse(pdf_b64=base64.b64encode(data).decode(), page_count=page_count)

    async def _serve_pdf_page(self, route: Route, html: str) -> None:
        url = urlsplit(route.request.url)
        if f"{url.scheme}://{url.netloc}/" != PDF_PAGE_URL:
            await route.abort()
        elif url.path == "/":
            await route.fulfill(body=html, content_type="text/html; charset=utf-8")
        elif (file := self._katex_file(url.path)) is not None:
            await route.fulfill(path=file, content_type=_KATEX_TYPES[file.suffix])
        else:
            await route.abort()

    def _katex_file(self, url_path: str) -> Path | None:
        if not url_path.startswith(KATEX_URL_PATH):
            return None
        file = (self._katex_dir / url_path.removeprefix(KATEX_URL_PATH)).resolve()
        if file.suffix in _KATEX_TYPES and file.is_relative_to(self._katex_dir) and file.is_file():
            return file
        return None

    async def close(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None
        async with self._lock:
            await self._shutdown()

    # --- browser lifetime ---

    async def _acquire(self) -> Browser:
        async with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch()
            self._active += 1
            return self._browser

    def _release(self) -> None:
        self._active -= 1
        if self._active == 0:
            self._idle_timer = asyncio.create_task(self._close_when_idle())

    async def _close_when_idle(self) -> None:
        await asyncio.sleep(self._idle_close_s)
        async with self._lock:
            if self._active == 0:
                await self._shutdown()

    async def _shutdown(self) -> None:
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None
