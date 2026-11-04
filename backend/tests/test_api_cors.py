"""CORS of the API (ТЗ Фазы 4 WP9.2, Д-§2.1): an exact origin list with credentials."""

from __future__ import annotations

import httpx
import pytest

from quack.api.app import create_app
from quack.config import Settings

APP = "https://app.qvack.online"
PREVIEW = "https://preview.qvack.online"


def client(**values: object) -> httpx.AsyncClient:
    settings = Settings(_env_file=None, **values)  # type: ignore[call-arg]
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://test"
    )


@pytest.mark.parametrize("origin", [APP, PREVIEW])
async def test_preflight_from_a_listed_origin_is_answered_with_credentials(origin: str):
    async with client() as http:
        response = await http.options(
            "/v1/sessions/active",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "https://app.qvack.online.evil.example"]
)
async def test_a_foreign_origin_gets_no_cors_headers(origin: str):
    async with client() as http:
        response = await http.options(
            "/v1/sessions/active",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )

    # Without `Access-Control-Allow-Origin` the browser refuses the response (Starlette answers
    # a disallowed preflight with 400).
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


async def test_a_simple_request_from_a_listed_origin_carries_the_headers():
    async with client() as http:
        response = await http.get("/no-such-route", headers={"Origin": APP})

    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == APP
    assert response.headers["access-control-allow-credentials"] == "true"


async def test_the_origin_list_is_the_setting():
    async with client(cors_origins=["https://staging.example"]) as http:
        listed = await http.get("/no-such-route", headers={"Origin": "https://staging.example"})
        unlisted = await http.get("/no-such-route", headers={"Origin": APP})

    assert listed.headers["access-control-allow-origin"] == "https://staging.example"
    assert "access-control-allow-origin" not in unlisted.headers
