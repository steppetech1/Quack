"""The edge of the API: Caddy, proxy headers, request size (ТЗ Ф4 WP9.2, WP18, WP22.1; Д-§3.1,
Т-§19.8). Reads files of the repository root (`compose.yaml`, `deploy/`): not for the `ct.sh`
container, which has only `backend/`."""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, cast

import pytest
import yaml  # pyright: ignore[reportMissingModuleSource]

from quack import cli
from quack.api.app import create_app
from quack.config import Settings

ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
MIB = 1024 * 1024


def _public_patterns() -> list[str]:
    block = re.search(r"@public\s*\{\s*path\s+([^\n}]+)", CADDYFILE)
    assert block, "deploy/Caddyfile has no @public matcher"
    return block.group(1).split()


def test_caddy_proxies_every_route_of_the_openapi_document() -> None:
    """A route the app documents but Caddy answers with 404 does not work in production: the
    companion pairing is `/companion/pair`, outside `/v1/*` and `/auth/*` (WP22.1)."""
    patterns = _public_patterns()
    paths = create_app(Settings()).openapi()["paths"]
    # `/health/*` is the probe of the compose healthcheck and stays inside (Д-§9.1).
    unreachable = [
        p
        for p in paths
        if not p.startswith("/health/") and not any(fnmatch(p, pattern) for pattern in patterns)
    ]
    assert unreachable == []


def test_caddy_caps_the_request_body_above_upload_max_mb() -> None:
    """Т-§19.8: a multi-gigabyte upload must be refused by the edge (413), not read by `api`
    (`mem_limit` 1g). The cap leaves room for the multipart envelope of a file of the limit."""
    match = re.search(r"request_body\s*\{\s*max_size\s+(\d+)\s*(MiB|MB)", CADDYFILE)
    assert match, "deploy/Caddyfile has no request_body max_size"
    unit = MIB if match.group(2) == "MiB" else 1000 * 1000
    cap = int(match.group(1)) * unit
    assert cap > Settings().upload_max_mb * MIB
    assert cap < 2 * Settings().upload_max_mb * MIB


def test_run_api_trusts_the_proxy_headers_of_the_configured_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without it uvicorn trusts 127.0.0.1 only: behind Caddy (another container) every client
    has Caddy's address, one hourly limit of sign-in links is shared by all, and the link in the
    mail is `http://` (`request.url_for`)."""
    import uvicorn

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: seen.update(kwargs))  # pyright: ignore[reportUnknownLambdaType]
    settings = Settings(api_forwarded_allow_ips="*")
    assert cli._run_api(settings) == 0  # pyright: ignore[reportPrivateUsage]
    assert seen["proxy_headers"] is True
    assert seen["forwarded_allow_ips"] == "*"


def test_compose_lets_api_trust_caddy(monkeypatch: pytest.MonkeyPatch) -> None:
    compose = cast("dict[str, Any]", yaml.safe_load((ROOT / "compose.yaml").read_text("utf-8")))
    env = compose["services"]["api"]["environment"]
    assert env["API_FORWARDED_ALLOW_IPS"] == "*"
    monkeypatch.setenv("API_FORWARDED_ALLOW_IPS", env["API_FORWARDED_ALLOW_IPS"])
    assert Settings().api_forwarded_allow_ips == "*"


async def test_client_request_ids_are_echoed_only_when_short_and_plain() -> None:
    """`X-Request-ID` goes into the response and every log line: a 60 KB value from a client
    was reflected as is."""
    import httpx

    app = create_app(Settings())
    limit = Settings().request_id_max_chars
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        kept = await client.get("/nope", headers={"X-Request-ID": "req-123"})
        long_ = await client.get("/nope", headers={"X-Request-ID": "x" * (limit + 1)})
        spaced = await client.get(
            "/nope", headers={"X-Request-ID": "a b", "X-Trace-ID": "y" * 5000}
        )
    assert kept.headers["X-Request-ID"] == "req-123"
    assert kept.json()["error"]["request_id"] == "req-123"
    assert long_.headers["X-Request-ID"] != "x" * (limit + 1)
    assert len(long_.headers["X-Request-ID"]) <= limit
    assert spaced.headers["X-Request-ID"] != "a b"
    assert len(spaced.headers["X-Trace-ID"]) == 32  # a generated trace id, not the 5000 chars
