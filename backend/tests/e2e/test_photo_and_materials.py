"""E2E: the companion photo journey and the materials journey (ТЗ Ф4 §8.14: T-E8, T-E7).

T-E8: pairing → two photos of one sheet → the second photo writes only its new line → the lines
are in the canvas. T-E7: upload of a PDF → parse → chunks → embeddings → a turn with
`search_materials`."""

from __future__ import annotations

import base64
import re
from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.api.auth import SESSION_COOKIE
from quack.compute_client.contracts import AlignResponse, PdfPage, PdfPagesResponse
from tests.e2e.journey import ok, onboard, sse_events
from tests.e2e.system import Learner, System
from tests.e2e.text_journey import SCHEMA, act, script_teacher, start_session
from tests.support.images import webp_vp8x
from tests.support.llm import ScriptedToolCall, ScriptedToolStep

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


PAGES = "/v1/pdf/pages"
PAGE_ONE = (
    "Chain rule\n\nIf f and g are differentiable, then (f(g(x)))' = f'(g(x)) g'(x). "
    "It needs the derivative of a power first."
)
PAGE_TWO = "Integration by parts\n\nWe use the product rule backwards: int u dv = uv - int v du."
TOPICS = ("Derivatives", "Integrals")
SKILLS = {
    "Chain rule": "Derivatives",
    "Power rule": "Derivatives",
    "Integration by parts": "Integrals",
    "Product rule": "Derivatives",
}


def parser_answer(request: Any) -> dict[str, Any]:
    """One mark per chunk of the input; the numbers are read from the prompt."""
    numbers = [int(n) for n in re.findall(r"^\[(\d+)\]", request.prompt, flags=re.M)]
    chunks = []
    for n in numbers:
        first = "Chain rule" in request.prompt.split(f"[{n}]", 1)[1].split("[", 1)[0]
        chunks.append(
            {
                "n": n,
                "topic": "Derivatives" if first else "Integrals",
                "notation": ["f'(g(x))"] if first else ["int u dv"],
                "teaches": ["Chain rule"] if first else ["Integration by parts"],
                "uses": ["Power rule"] if first else ["Product rule"],
            }
        )
    return {"title": "Calculus lectures", "chunks": chunks, "deadlines": []}


def builder_answer(_request: Any) -> dict[str, Any]:
    """The reconciliation creates every subject of the material (`BuilderOutput`)."""
    items = [
        {
            "name": name,
            "kind": "topic",
            "decision": "create",
            "target": None,
            "topic_hint": None,
            "same_idea": None,
            "same_idea_confidence": None,
        }
        for name in TOPICS
    ] + [
        {
            "name": name,
            "kind": "skill",
            "decision": "create",
            "target": None,
            "topic_hint": topic,
            "same_idea": None,
            "same_idea_confidence": None,
        }
        for name, topic in SKILLS.items()
    ]
    return {"items": items}


async def parsed_material(system: System) -> tuple[Learner, UUID, UUID]:
    """Upload a PDF and wait until it is parsed: the learner, the domain and the topic."""
    pages = [
        PdfPage(page=i, text=t, image_webp_b64=base64.b64encode(ALIGNED).decode())
        for i, t in enumerate((PAGE_ONE, PAGE_TWO), 1)
    ]
    system.compute.push_for(PAGES, PdfPagesResponse(pages=pages))
    system.llm.respond("MaterialParserOutput", parser_answer)
    system.llm.respond("BuilderOutput", builder_answer)

    learner = await system.sign_in_by_link()
    domain_id, topic_id = await onboard(system, learner, "te7")
    uploaded = ok(
        await system.client.post(
            f"/v1/domains/{domain_id}/materials",
            files={"file": ("notes.pdf", PDF, "application/octet-stream")},
            headers=learner.headers,
        ),
        201,
    )
    material_id = uploaded["material_id"]
    await system.settle(timeout_s=60)
    db = system.resources.db
    detail = ok(await system.client.get(f"/v1/materials/{material_id}", headers=learner.headers))
    material = detail["material"]
    assert material["status"] == "parsed" and material["pages_count"] == 2, material
    stored = await db.fetch_all(
        "SELECT page, text FROM content.material_pages WHERE material_id = %(m)s ORDER BY page",
        {"m": UUID(material_id)},
    )
    assert [(r["page"], r["text"]) for r in stored] == [(1, PAGE_ONE), (2, PAGE_TWO)]
    chunks = await db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(l)s AND type = 'chunk.indexed' "
        "ORDER BY seq",
        {"l": learner.learner_id},
    )
    assert [c["payload"]["page"] for c in chunks] == [1, 2]
    assert system.resources.embeddings.calls >= 2  # type: ignore[attr-defined]
    return learner, domain_id, topic_id


async def test_materials_journey(system: System) -> None:
    """T-E7, the first half: upload → parse (pages, then the structure under the lock of the
    domain) → chunks → embeddings."""
    await parsed_material(system)


MODAL_NOT_WIRED = (
    "B: `source_of` builds `MemoryTurnContextSource` without the embeddings client, so the "
    "`ToolContext` has no `search`: `search_materials` always answers «search temporarily "
    "unavailable» in the real process (WP14.4, D-C-191)"
)


@pytest.mark.xfail(strict=True, reason=MODAL_NOT_WIRED)
async def test_search_materials_tool_finds_the_chunk(system: System) -> None:
    """T-E7, the second half: a teacher turn calls `search_materials` and gets the chunk."""
    learner, domain_id, topic_id = await parsed_material(system)
    script_teacher(system, ())
    system.llm.script_tools(
        SCHEMA,
        ScriptedToolStep(
            (ScriptedToolCall("search_materials", {"query": "chain rule", "skills": []}),)
        ),
    )
    session_id = await start_session(system, learner, domain_id, topic_id)
    said = await act(
        system, learner, session_id, {"kind": "message", "text": "What is the chain rule?"}
    )
    frames = sse_events(said)
    assert "error" not in [n for n, _ in frames], frames
    outputs = [
        item
        for call in system.llm.stream_calls
        for item in call.input_items
        if item.get("type") == "function_call_output"
    ]
    assert outputs and "chain" in repr(outputs).lower(), outputs
