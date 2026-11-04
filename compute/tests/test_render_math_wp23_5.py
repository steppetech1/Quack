"""WP23.5 (D-C-250): formulas of `/v1/render/pdf` are typeset by the pinned KaTeX, offline.

The page marks a formula as `<span class="quack-math" data-display="true|false">TeX</span>`;
the renderer loads KaTeX, its CSS and fonts from the service's own files (no network, Д-§3.2)
and typesets the elements before the page is printed. Runs in Linux like the other render tests
(`playwright install chromium`)."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from quack_compute.app import create_app
from quack_compute.settings import ComputeSettings

ROOT = Path(__file__).resolve().parents[2]
SUMS = ROOT / "deploy" / "static" / "viz" / "SHA256SUMS"
KATEX = Path(ComputeSettings().katex_dir)


def _inline(tex: str) -> str:
    return f'<span class="quack-math" data-display="false">{tex}</span>'


def _display(tex: str) -> str:
    return f'<span class="quack-math" data-display="true" style="display:block">{tex}</span>'


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    settings = ComputeSettings(compute_workers=1, compute_tmp_dir=".")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _render(client: TestClient, body: str) -> tuple[pymupdf.Document, int]:
    response = client.post("/v1/render/pdf", json={"html": f"<!doctype html><body>{body}</body>"})
    assert response.status_code == 200
    data = response.json()
    document = pymupdf.open(stream=base64.b64decode(data["pdf_b64"]), filetype="pdf")
    return document, data["page_count"]


def _fonts(document: pymupdf.Document) -> str:
    return " ".join(font[3] for page in document for font in page.get_fonts())


def test_a_formula_is_typeset_not_left_as_tex(client: TestClient) -> None:
    document, pages = _render(client, f"<p>Slope {_inline(r'\frac{a}{b}+x^2')} here.</p>")
    try:
        text = document[0].get_text()
        assert "\\frac" not in text and "^" not in text and "{" not in text
        assert "KaTeX" in _fonts(document)  # the fonts of the page are the local KaTeX ones
        assert pages == 1
    finally:
        document.close()


def test_a_page_without_formulas_loads_no_katex(client: TestClient) -> None:
    document, _ = _render(client, "<p>No formulas, only text.</p>")
    try:
        assert "KaTeX" not in _fonts(document)
    finally:
        document.close()


def test_a_broken_formula_does_not_fail_the_sheet(client: TestClient) -> None:
    document, pages = _render(client, f"<p>{_inline(r'\frac{a')} and {_inline('y=2')}</p>")
    try:
        assert pages == 1 and "KaTeX" in _fonts(document)
    finally:
        document.close()


def test_the_height_of_formulas_counts_in_the_page_count(client: TestClient) -> None:
    """Т-§17.6: the same text takes one page as TeX source and more once the display formulas
    are typeset — the page count the fit rounds read is the one of the typeset page."""
    formulas = [r"\sum_{k=1}^{n}\frac{\frac{a_k}{b_k}}{\sqrt{1+k^2}}"] * 40
    plain = "".join(f"<div>{tex}</div>" for tex in formulas)
    typeset = "".join(_display(tex) for tex in formulas)
    plain_doc, plain_pages = _render(client, plain)
    typeset_doc, typeset_pages = _render(client, typeset)
    plain_doc.close()
    typeset_doc.close()
    assert plain_pages == 1
    assert typeset_pages > 1


def test_the_katex_files_are_the_pinned_ones() -> None:
    """The copy in the image has the bytes of `deploy/static/viz` (one pin: `SHA256SUMS`)."""
    pinned: dict[str, str] = {}
    for line in SUMS.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition(" *")
        if name.startswith(f"katex/{KATEX.name}/"):
            pinned[name.removeprefix(f"katex/{KATEX.name}/")] = digest
    assert {"katex.min.js", "katex.min.css"} <= pinned.keys()
    copies = [p for p in KATEX.rglob("*") if p.is_file()]
    assert {p.name for p in copies if p.parent.name == "fonts"}  # the fonts are there
    for path in copies:
        relative = path.relative_to(KATEX).as_posix()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == pinned[relative], relative
    css = (KATEX / "katex.min.css").read_text(encoding="utf-8")
    for name in {m for m in css.split("url(")[1:]}:
        woff2 = name.split(")")[0]
        if woff2.endswith(".woff2"):
            assert (KATEX / woff2).is_file(), woff2
