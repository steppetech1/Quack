"""The mutating `/v1` routes of the assembled app (shared by `T-E2`).

What a route needs from the walks (path, body, flags) is registered by the streams in
`tests/e2e/routes/` (D-C-147); this module only reads the mounted set.
"""

from __future__ import annotations

from fastapi import FastAPI

MUTATING = {"post", "put", "patch", "delete"}


def mutating_routes(app: FastAPI) -> set[tuple[str, str]]:
    """`(METHOD, path)` of mutating `/v1` routes from the OpenAPI schema."""
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/v1/")
        for method in operations
        if method in MUTATING
    }
