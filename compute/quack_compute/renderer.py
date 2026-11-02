"""HTML rendering with Playwright and Chromium (Т-§1.2, Т-§13.6, Т-§17.6, ТЗ Фазы 4 WP24).

Chromium starts on the first render job, serves the next jobs with new pages and closes after
`chromium_idle_close_s` without jobs (Д-§4.1, `T-CM3`). One browser per service process.
Stage I (ТЗ Фазы 4 §0.3): signatures only.
"""

from __future__ import annotations

from quack_compute.contracts import (
    RenderPdfRequest,
    RenderPdfResponse,
    RenderSnapshotsRequest,
    RenderSnapshotsResponse,
)


class Renderer:
    def __init__(self, idle_close_s: int) -> None:
        self._idle_close_s = idle_close_s

    @property
    def browser_open(self) -> bool:
        raise NotImplementedError

    async def snapshots(self, request: RenderSnapshotsRequest) -> RenderSnapshotsResponse:
        raise NotImplementedError

    async def pdf(self, request: RenderPdfRequest) -> RenderPdfResponse:
        raise NotImplementedError

    async def close(self) -> None:
        return None
