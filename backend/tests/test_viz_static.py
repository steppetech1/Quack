"""WP9.2, WP16.5 (Т-§13.6 п.1; D-C-218): the pinned libraries of the visualizations exist where
`viz_libraries` points, are the bytes that were checked, and Caddy serves them."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml  # pyright: ignore[reportMissingModuleSource]

from quack.config import Settings

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "deploy" / "static" / "viz"
COMPOSE = ROOT / "compose.yaml"
CADDYFILE = ROOT / "deploy" / "Caddyfile"
LIBRARIES = {"d3", "plotly", "katex", "katex-css"}


@pytest.fixture(scope="module")
def viz_env() -> dict[str, str]:
    compose = cast("dict[str, Any]", yaml.safe_load(COMPOSE.read_text(encoding="utf-8")))
    return cast("dict[str, str]", compose["x-viz-env"])


def test_the_libraries_of_compose_are_files_of_the_catalog(viz_env: dict[str, str]) -> None:
    libraries = json.loads(viz_env["VIZ_LIBRARIES"])
    assert set(libraries) == LIBRARIES
    for name, path in libraries.items():
        assert not path.startswith("/"), name
        assert (STATIC / path).is_file(), f"{name}: deploy/static/viz/{path} is missing"
        assert re.search(r"\d+\.\d+\.\d+", path), f"{name}: the path holds no pinned version"


def test_settings_read_the_libraries_from_the_environment(
    viz_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIZ_LIBRARIES", viz_env["VIZ_LIBRARIES"])
    monkeypatch.setenv("VIZ_STATIC_BASE_URL", "https://api.example/static/viz")
    settings = Settings()
    assert set(settings.viz_libraries) == LIBRARIES
    assert settings.viz_libraries["d3"] == "d3/7.9.0/d3.min.js"


def test_the_base_address_is_what_caddy_serves(viz_env: dict[str, str]) -> None:
    base = viz_env["VIZ_STATIC_BASE_URL"]
    assert base.endswith("/static/viz}"), base  # https://<API_DOMAIN>/static/viz by default
    caddy = CADDYFILE.read_text(encoding="utf-8")
    assert "handle /static/viz/*" in caddy and "file_server" in caddy
    compose = cast("dict[str, Any]", yaml.safe_load(COMPOSE.read_text(encoding="utf-8")))
    assert "./deploy/static:/srv/static:ro" in compose["services"]["caddy"]["volumes"]
    for service in ("api", "workers"):
        env = compose["services"][service]["environment"]
        assert "VIZ_LIBRARIES" in env and "VIZ_STATIC_BASE_URL" in env, service


def test_the_files_are_the_checked_bytes() -> None:
    """`SHA256SUMS` holds the hashes that matched jsdelivr and cdnjs when the files were taken;
    a file nobody listed, or changed, fails here."""
    listed: dict[str, str] = {}
    for line in (STATIC / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition(" *")
        listed[name] = digest
    files = {
        path.relative_to(STATIC).as_posix()
        for path in STATIC.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    assert files == set(listed)
    for name, digest in listed.items():
        assert hashlib.sha256((STATIC / name).read_bytes()).hexdigest() == digest, name


def test_the_stylesheet_finds_its_fonts() -> None:
    css = (STATIC / "katex" / "0.16.11" / "katex.min.css").read_text(encoding="utf-8")
    fonts = set(re.findall(r"url\((fonts/[^)]+)\)", css))
    assert fonts
    for font in fonts:
        assert (STATIC / "katex" / "0.16.11" / font).is_file(), font
