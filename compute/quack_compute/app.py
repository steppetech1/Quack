"""compute service (Т-§1.2, ТЗ Фазы 4 WP24): `GET /health/live`, the WP24 endpoints and the
Phase 3 `POST /v1/check/math`. No database, network or secrets.

Seam S10: the pool (`app.state.pool`) and the renderer (`app.state.renderer`) are created once
per process in the lifespan. Stream B declares `/v1/math/check` and `/v1/math/transition` here;
their bodies live in `quack_compute/math/` (WP11.5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from quack_compute import work
from quack_compute.checker import run_check
from quack_compute.contracts import (
    AlignResponse,
    CleanDrawingRequest,
    ImageRequest,
    ImageResponse,
    JobError,
    PdfPagesRequest,
    PdfPagesResponse,
    RenderPdfRequest,
    RenderPdfResponse,
    RenderSnapshotsRequest,
    RenderSnapshotsResponse,
    RenderStrokesRequest,
)
from quack_compute.math import service as math_service
from quack_compute.math.schemas import (
    MathCheckRequest as MathCheckRequestV4,
)
from quack_compute.math.schemas import (
    MathCheckResponse as MathCheckResponseV4,
)
from quack_compute.math.schemas import TransitionRequest, TransitionResponse
from quack_compute.pool import JobTimeout, TaskPool
from quack_compute.renderer import Renderer
from quack_compute.schemas import MathCheckRequest, MathCheckResponse
from quack_compute.settings import ComputeSettings


def create_app(settings: ComputeSettings | None = None) -> FastAPI:
    settings = settings or ComputeSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.pool = TaskPool(settings.compute_workers)
        app.state.pool.start()
        app.state.renderer = Renderer(settings.chromium_idle_close_s)
        try:
            yield
        finally:
            await app.state.renderer.close()
            app.state.pool.stop()

    app = FastAPI(title="quack-compute", lifespan=lifespan)

    @app.exception_handler(JobTimeout)
    async def job_timeout(request: Request, exc: JobTimeout) -> JSONResponse:
        return JSONResponse(status_code=504, content=JobError(error=str(exc)).model_dump())

    async def in_pool[R](request: Request, fn: Callable[..., R], *args: Any) -> R:
        pool: TaskPool = request.app.state.pool
        timeout_s = request.app.state.settings.compute_task_timeout_s
        return await pool.run(fn, *args, timeout_s=timeout_s)

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/check/math")
    async def check_math(request: MathCheckRequest) -> MathCheckResponse:
        """Phase 3 contract; replaced by `/v1/math/check` (WP11.5)."""
        return await run_check(request)

    @app.post("/v1/math/check")
    async def math_check(body: MathCheckRequestV4, request: Request) -> MathCheckResponseV4:
        """WP11.5 (stream B): `Т-§6.5`–`Т-§6.7`; the call is killed past `timeout_ms`."""
        pool: TaskPool = request.app.state.pool
        return await pool.run(math_service.check_math, body, timeout_s=body.timeout_ms / 1000)

    @app.post("/v1/math/transition")
    async def math_transition(body: TransitionRequest, request: Request) -> TransitionResponse:
        """WP11.5 (stream B): does a canvas line follow from the previous one (`Т-§15.4`)."""
        pool: TaskPool = request.app.state.pool
        return await pool.run(math_service.check_transition, body, timeout_s=body.timeout_ms / 1000)

    @app.post("/v1/pdf/pages")
    async def pdf_pages(body: PdfPagesRequest, request: Request) -> PdfPagesResponse:
        return await in_pool(request, work.pdf_pages, body)

    @app.post("/v1/image/align")
    async def image_align(body: ImageRequest, request: Request) -> AlignResponse:
        return await in_pool(request, work.image_align, body)

    @app.post("/v1/image/clean-drawing")
    async def image_clean_drawing(body: CleanDrawingRequest, request: Request) -> ImageResponse:
        return await in_pool(request, work.image_clean_drawing, body)

    @app.post("/v1/image/render-strokes")
    async def image_render_strokes(body: RenderStrokesRequest, request: Request) -> ImageResponse:
        return await in_pool(request, work.image_render_strokes, body)

    @app.post("/v1/render/snapshots")
    async def render_snapshots(
        body: RenderSnapshotsRequest, request: Request
    ) -> RenderSnapshotsResponse:
        renderer: Renderer = request.app.state.renderer
        return await renderer.snapshots(body)

    @app.post("/v1/render/pdf")
    async def render_pdf(body: RenderPdfRequest, request: Request) -> RenderPdfResponse:
        renderer: Renderer = request.app.state.renderer
        return await renderer.pdf(body)

    return app


app = create_app()
