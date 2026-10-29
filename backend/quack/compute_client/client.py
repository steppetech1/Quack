"""HTTP client of `POST /v1/check/math` of the isolated compute service (spec §8.4)."""

from __future__ import annotations

from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

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


class ComputeClient:
    def __init__(
        self, base_url: str, timeout_sec: float = 5.0, client: httpx.AsyncClient | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
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

    async def aclose(self) -> None:
        await self.client.aclose()
