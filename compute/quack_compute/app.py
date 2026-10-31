"""compute service: `POST /v1/check/math` and `GET /health/live` (spec §8.4).
No database, network or secrets."""

from __future__ import annotations

from fastapi import FastAPI

from quack_compute.checker import run_check
from quack_compute.schemas import MathCheckRequest, MathCheckResponse

app = FastAPI(title="quack-compute")


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/check/math")
async def check_math(request: MathCheckRequest) -> MathCheckResponse:
    return await run_check(request)
