"""OpenAPI schema of the API and its committed snapshot `backend/openapi.json`
(ТЗ Фазы 3 WP8.7, `Д-§5.2` п.7).

Error responses are documented with the common `ErrorResponse` (`С-§8.5`) for the codes
401, 404, 409, 422, 429, 503 where a route can return them; the rules live in one place
here, so the product routers stay as they are (PHASE3_NOTES D-C-015)."""

from __future__ import annotations

from fastapi import FastAPI


def install_openapi(app: FastAPI) -> None:
    """Replace `app.openapi` with the documented schema (error responses, SSE bodies)."""
    raise NotImplementedError


def render_openapi(app: FastAPI) -> bytes:
    """The schema of `app` serialized deterministically: sorted keys, 2-space indent, UTF-8,
    trailing newline. `ops openapi` and `T-A7` use exactly these bytes."""
    raise NotImplementedError
