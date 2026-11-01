"""HTTP client of the isolated compute service: `POST /v1/check/math` (spec §8.4) and the
WP24 endpoints (ТЗ Фазы 4 WP24, seam S10). Math endpoints of WP11.5 are added by stream B.
"""

from __future__ import annotations

from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from quack.compute_client.contracts import (
    AlignResponse,
    CleanDrawingRequest,
    ImageRequest,
    ImageResponse,
    PdfPagesRequest,
    PdfPagesResponse,
    RenderPdfRequest,
    RenderPdfResponse,
    RenderSnapshotsRequest,
    RenderSnapshotsResponse,
    RenderStrokesRequest,
)
from quack.config import Settings

type MathMode = Literal[
    "expression_equiv", "equation_roots", "derivative", "integral", "matrix_eq", "numeric_tol"
]


class MathCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: MathMode
    student_expr: Annotated[str, Field(max_length=1000)]
    reference_expr: Annotated[str, Field(max_length=1000)]
    variables: list[str] = ["x"]
    tolerance: float = 1e-6
    timeout_ms: int = 1500


class MathCheckResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    status: Literal["completed", "parse_error", "timeout", "unsupported"]
    is_equivalent: bool | None
    normalized_student: str | None
    diff_hint: str | None
    error_message: str | None


class ComputeUnavailableError(Exception):
    """compute did not answer in time or returned a non-200 status."""


class ComputeJobTimeout(ComputeUnavailableError):
    """compute killed the job on its hard timeout (HTTP 504, Т-§1.2)."""


class ComputeClient:
    def __init__(
        self,
        base_url: str,
        timeout_sec: float = 5.0,
        client: httpx.AsyncClient | None = None,
        *,
        job_timeout_sec: float | None = None,
    ) -> None:
        """`timeout_sec` — math checks; `job_timeout_sec` — PDF, image and render jobs (the
        compute job timeout plus `timeout_sec`, so that compute answers 504 first)."""
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.job_timeout_sec = job_timeout_sec if job_timeout_sec is not None else timeout_sec
        self.client = client or httpx.AsyncClient()

    async def check_math(self, request: MathCheckRequest) -> MathCheckResponse:
        try:
            response = await self.client.post(
                f"{self.base_url}/v1/check/math",
                json=request.model_dump(mode="json"),
                timeout=self.timeout_sec,
            )
        except httpx.HTTPError as exc:
            raise ComputeUnavailableError(f"compute request failed: {exc!r}") from exc
        if response.status_code != 200:
            raise ComputeUnavailableError(f"compute returned HTTP {response.status_code}")
        try:
            return MathCheckResponse.model_validate_json(response.content)
        except ValueError as exc:
            raise ComputeUnavailableError("compute returned an invalid response") from exc

    async def pdf_pages(self, request: PdfPagesRequest) -> PdfPagesResponse:
        return await self._post("/v1/pdf/pages", request, PdfPagesResponse)

    async def image_align(self, request: ImageRequest) -> AlignResponse:
        return await self._post("/v1/image/align", request, AlignResponse)

    async def image_clean_drawing(self, request: CleanDrawingRequest) -> ImageResponse:
        return await self._post("/v1/image/clean-drawing", request, ImageResponse)

    async def image_render_strokes(self, request: RenderStrokesRequest) -> ImageResponse:
        return await self._post("/v1/image/render-strokes", request, ImageResponse)

    async def render_snapshots(self, request: RenderSnapshotsRequest) -> RenderSnapshotsResponse:
        return await self._post("/v1/render/snapshots", request, RenderSnapshotsResponse)

    async def render_pdf(self, request: RenderPdfRequest) -> RenderPdfResponse:
        return await self._post("/v1/render/pdf", request, RenderPdfResponse)

    async def _post[M: BaseModel](self, path: str, body: BaseModel, model: type[M]) -> M:
        try:
            response = await self.client.post(
                f"{self.base_url}{path}",
                json=body.model_dump(mode="json"),
                timeout=self.job_timeout_sec,
            )
        except httpx.HTTPError as exc:
            raise ComputeUnavailableError(f"compute request failed: {exc!r}") from exc
        if response.status_code == 504:
            raise ComputeJobTimeout(f"compute job timed out: {path}")
        if response.status_code != 200:
            raise ComputeUnavailableError(f"compute returned HTTP {response.status_code}")
        try:
            return model.model_validate_json(response.content)
        except ValueError as exc:
            raise ComputeUnavailableError("compute returned an invalid response") from exc

    async def aclose(self) -> None:
        await self.client.aclose()


def create_compute_client(settings: Settings) -> ComputeClient:
    """The process-wide `ComputeClient` (§4.2): math checks wait `compute_timeout_sec`, jobs —
    `compute_task_timeout_s` plus `compute_timeout_sec`, so compute answers 504 first."""
    return ComputeClient(
        settings.compute_url,
        settings.compute_timeout_sec,
        job_timeout_sec=settings.compute_task_timeout_s + settings.compute_timeout_sec,
    )
