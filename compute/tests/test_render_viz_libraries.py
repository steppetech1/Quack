"""Pinned libraries on a visualization page (d3, plotly, KaTeX at `libraries_base_url`).

The model writes a page that loads its libraries at `VIZ_STATIC_BASE_URL/<path>`; compute has no
network, so the renderer answers those requests from the files of the image (`SHA256SUMS` lists
them) and refuses everything else (Д-§3.2). Runs in Linux like the other render tests
(`playwright install chromium`)."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from quack_compute.app import create_app
from quack_compute.renderer import Renderer, _libraries_base
from quack_compute.settings import ComputeSettings

BASE = "https://api.test.example/static/viz"
D3 = f"{BASE}/d3/7.9.0/d3.min.js"
PAGE = (
    "<body><svg id='s' width='200' height='100'></svg>"
    "<script src='{d3}'></script>"
    "<script>d3.select('#s').append('rect').attr('width', 200).attr('height', 100)"
    ".attr('fill', 'red');</script></body>"
)


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    settings = ComputeSettings(compute_workers=1, compute_tmp_dir=".")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _snapshot(client: TestClient, html: str, base: str | None = BASE) -> dict[str, Any]:
    body: dict[str, Any] = {"html": html, "param_sets": []}
    if base is not None:
        body["libraries_base_url"] = base
    response = client.post("/v1/render/snapshots", json=body)
    assert response.status_code == 200, response.text
    [snapshot] = response.json()["snapshots"]
    return snapshot


def test_a_page_with_d3_renders_without_errors_and_is_not_blank(client: TestClient) -> None:
    snapshot = _snapshot(client, PAGE.format(d3=D3))
    assert snapshot["console_errors"] == []
    assert snapshot["blank"] is False


def test_without_a_base_address_the_page_gets_nothing_but_itself(client: TestClient) -> None:
    """An older backend sends no base address: the behaviour is the previous one."""
    snapshot = _snapshot(client, PAGE.format(d3=D3), base=None)
    errors = snapshot["console_errors"]
    assert any("ERR_FAILED" in error for error in errors), errors
    assert any("d3 is not defined" in error for error in errors), errors


def test_a_foreign_host_and_a_path_out_of_the_directory_are_refused(client: TestClient) -> None:
    foreign = "https://evil.test.example/static/viz/d3/7.9.0/d3.min.js"
    snapshot = _snapshot(client, f"<script src='{foreign}'></script><p>x</p>")
    assert any("ERR_FAILED" in error for error in snapshot["console_errors"]), snapshot
    for path in ("/../SHA256SUMS", "/%2e%2e/SHA256SUMS", "/d3/../../SHA256SUMS"):
        snapshot = _snapshot(client, f"<script src='{BASE}{path}'></script><p>x</p>")
        assert snapshot["console_errors"], path


def test_the_fonts_of_katex_reach_a_page_of_another_origin(client: TestClient) -> None:
    """A CSS font is a cross-origin request: it needs the permission header of the answer."""
    font = f"{BASE}/katex/0.16.11/fonts/KaTeX_Main-Regular.woff2"
    html = (
        "<!doctype html>"
        f"<link rel='stylesheet' href='{BASE}/katex/0.16.11/katex.min.css'>"
        f"<script src='{BASE}/katex/0.16.11/katex.min.js'></script>"
        "<span id='f'></span><script>katex.render('x^2', document.getElementById('f'));"
        f"var x = new XMLHttpRequest(); x.open('GET', '{font}', false); x.send();"
        "if (x.status !== 200) throw new Error('font ' + x.status);</script>"
    )
    snapshot = _snapshot(client, html)
    assert snapshot["console_errors"] == []
    assert snapshot["blank"] is False
    assert base64.b64decode(snapshot["image_webp_b64"])[:4] == b"RIFF"


def test_only_the_files_of_sha256sums_inside_the_directory_are_served(tmp_path: Path) -> None:
    libs = tmp_path / "viz"
    (libs / "d3").mkdir(parents=True)
    pinned = libs / "d3" / "lib.js"
    pinned.write_bytes(b"var d3 = 1;")
    (libs / "d3" / "unlisted.js").write_bytes(b"var x = 1;")
    (tmp_path / "secret.js").write_bytes(b"secret")
    digest = hashlib.sha256(pinned.read_bytes()).hexdigest()
    (libs / "SHA256SUMS").write_text(f"{digest} *d3/lib.js\n", encoding="utf-8")
    renderer = Renderer(idle_close_s=1, viz_libraries_dir=str(libs))
    base = _libraries_base("https://h.example/static/viz/")
    assert base == "https://h.example/static/viz/"
    assert renderer._library_file(f"{base}d3/lib.js?v=1", base) == pinned.resolve()
    for url in (
        f"{base}d3/unlisted.js",  # not in SHA256SUMS
        f"{base}d3/missing.js",
        f"{base}../secret.js",
        f"{base}%2e%2e/secret.js",
        f"{base}d3/..%2f..%2fsecret.js",
        f"{base}d3%5c..%5c..%5csecret.js",
        f"{base}{libs.as_posix()}/d3/lib.js",
        f"{base}",
        f"{base}d3",  # a directory
        "https://other.example/static/viz/d3/lib.js",
        "http://h.example/static/viz/d3/lib.js",
    ):
        assert renderer._library_file(url, base) is None, url


def test_the_base_address_is_an_http_address_or_nothing() -> None:
    assert _libraries_base(None) is None
    assert _libraries_base("") is None
    assert _libraries_base("file:///etc/") is None
    assert _libraries_base("/static/viz") is None
    assert _libraries_base("https://h.example/static/viz?x=1") is None
    assert _libraries_base("https://h.example/static/viz") == "https://h.example/static/viz/"


def test_the_libraries_directory_holds_the_pinned_files() -> None:
    """Every file of `SHA256SUMS` is there with its digest; the three libraries are listed."""
    libs = Path(ComputeSettings().viz_libraries_dir)
    names = []
    for line in (libs / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition(" *")
        assert hashlib.sha256((libs / name).read_bytes()).hexdigest() == digest, name
        names.append(name)
    for library in ("d3/", "plotly/", "katex/"):
        assert any(name.startswith(library) for name in names), library
