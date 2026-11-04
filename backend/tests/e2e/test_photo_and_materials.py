"""E2E: the companion photo journey and the materials journey (ТЗ Ф4 §8.14: T-E8, T-E7).

T-E8: pairing → two photos of one sheet → the second photo writes only its new line → the lines
are in the canvas. T-E7: upload of a PDF → parse → chunks → embeddings (red: the handler
`material.parse` is not written yet, WP20.2)."""

from __future__ import annotations

import base64
import re
from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.api.auth import SESSION_COOKIE
from quack.compute_client.contracts import AlignResponse
from tests.e2e.journey import ok, onboard
from tests.e2e.system import Learner, System
from tests.e2e.text_journey import start_session
from tests.support.images import webp_vp8x

ALIGN = "/v1/image/align"
SHEET = webp_vp8x(200, 100)
ALIGNED = b"RIFF\x00\x00\x00\x00WEBPVP8 aligned"
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"


def line(text: str, top: float) -> dict[str, Any]:
    return {
        "kind": "latex",
        "text": text,
        "sympy": None,
        "bbox": {"left": 0.1, "top": top, "right": 0.9, "bottom": round(top + 0.05, 2)},
        "confidence": 0.8,
        "crossed_out": False,
    }


async def committed_lines(system: System, learner: Learner) -> list[str]:
    rows = await system.resources.db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'canvas.line_committed' ORDER BY seq",
        {"learner_id": learner.learner_id},
    )
    return [r["payload"]["text"] for r in rows]


async def test_companion_photo_journey(system: System) -> None:
    """T-E8."""
    learner = await system.sign_in_by_link()
    domain_id, topic_id = await onboard(system, learner, "te8")
    session_id = await start_session(system, learner, domain_id, topic_id)
    canvas = ok(
        await system.client.post(
            f"/v1/sessions/{session_id}/canvases", json={}, headers=learner.headers
        ),
        201,
    )
    canvas_id = UUID(canvas["canvas_id"])

    # The laptop asks for a token, the phone exchanges it for a companion session.
    issued = ok(
        await system.client.post(f"/v1/canvases/{canvas_id}/pairing", headers=learner.headers),
        201,
    )
    paired = await system.client.post("/companion/pair", json={"token": issued["token"]})
    assert paired.status_code == 200, paired.text
    found = re.search(rf"{SESSION_COOKIE}=([^;]+)", paired.headers["set-cookie"])
    assert found is not None
    phone = {"Cookie": f"{SESSION_COOKIE}={found.group(1)}"}

    aligned = AlignResponse(aligned=True, image_webp_b64=base64.b64encode(ALIGNED).decode())
    system.compute.push_for(ALIGN, *[aligned] * 4)
    sheet: dict[str, list[dict[str, Any]]] = {"lines": []}
    system.llm.respond("RecognizerSheetOutput", lambda _r: dict(sheet))

    async def photo(lines: list[dict[str, Any]]) -> None:
        sheet["lines"] = lines
        uploaded = await system.client.post(
            f"/v1/canvases/{canvas_id}/photos",
            files={"file": (f"{uuid4()}.webp", SHEET, "image/webp")},
            headers=phone,
        )
        assert uploaded.status_code == 201, uploaded.text
        await system.settle()

    await photo([line("2x+4=10", 0.1), line("2x=6", 0.2)])
    assert await committed_lines(system, learner) == ["2x+4=10", "2x=6"]
    # The same sheet again with one new line: the old lines are recognised as the same ones
    # (Т-§22.6 п.3), only the new line is written.
    await photo([line("2x+4=10", 0.1), line("2x=6", 0.2), line("x=3", 0.3)])
    assert await committed_lines(system, learner) == ["2x+4=10", "2x=6", "x=3"]

    shown = ok(await system.client.get(f"/v1/canvases/{canvas_id}", headers=learner.headers))
    assert str(shown["canvas_id"]) == str(canvas_id)
    # The companion sees nothing but its canvas (T-CP1).
    assert (await system.client.get("/v1/me", headers=phone)).status_code == 403


MATERIALS_NOT_PARSED = (
    "A: the handler `material.parse` (WP20.2) is not written: an uploaded material stays "
    "`uploaded`, no pages, no chunks, no embeddings (D-C-191)"
)


@pytest.mark.xfail(strict=True, reason=MATERIALS_NOT_PARSED)
async def test_materials_and_search_journey(system: System) -> None:
    """T-E7: upload → parse → pages and chunks → embeddings."""
    learner = await system.sign_in_by_link()
    domain_id, _ = await onboard(system, learner, "te7")
    uploaded = ok(
        await system.client.post(
            f"/v1/domains/{domain_id}/materials",
            files={"file": ("notes.pdf", PDF, "application/octet-stream")},
            headers=learner.headers,
        ),
        201,
    )
    material_id = uploaded["material_id"]
    await system.settle()
    material = ok(await system.client.get(f"/v1/materials/{material_id}", headers=learner.headers))
    assert material["status"] == "parsed", material
    pages = await system.resources.db.fetch_one(
        "SELECT count(*) AS n FROM content.material_pages WHERE material_id = %(m)s",
        {"m": UUID(material_id)},
    )
    assert pages is not None and pages["n"] > 0
    chunks = await system.resources.db.fetch_one(
        "SELECT count(*) AS n FROM core.events WHERE learner_id = %(l)s AND type = 'chunk.indexed'",
        {"l": learner.learner_id},
    )
    assert chunks is not None and chunks["n"] > 0
    assert system.resources.embeddings.calls > 0  # type: ignore[attr-defined]
