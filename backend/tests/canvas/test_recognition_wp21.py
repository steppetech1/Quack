"""WP21.3: recognition of canvas lines (ТЗ Ф4 WP21.3; Т-§15.1, Т-§15.3; D-C-131)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from quack.api.app import create_app
from quack.api.auth import SESSION_COOKIE
from quack.auth.sessions import open_session
from quack.canvas.boards import open_canvas
from quack.canvas.recognition import (
    LineBook,
    commit_typed,
    erase_line,
    recognize_line,
    render_request,
)
from quack.canvas.strokes import Erasure, Point, Stroke, StrokeBatch
from quack.compute_client.contracts import ImageResponse
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import CanvasLineCommitted
from quack.events.log import NewEvent, append_events
from quack.llm.provider import ImageInput
from tests.db.fixtures import create_learner
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources
from tests.support.storage import InMemoryObjectStore
from tests.support.ws import AsgiWebSocket

RENDER = "/v1/image/render-strokes"
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 line"
READ = {"kind": "latex", "text": "x^2", "sympy": "x**2", "confidence": 0.9, "crossed_out": False}


def _stroke(stroke_id: str, *xy: tuple[float, float]) -> Stroke:
    return Stroke(stroke_id, tuple(Point(x, y, i * 16, 0.5) for i, (x, y) in enumerate(xy)))


async def _canvas(db: Db, learner: UUID) -> tuple[UUID, UUID]:
    session_id = uuid4()
    started = NewEvent(
        type="session.started",
        payload={"session_id": str(session_id), "domain_id": str(uuid4()), "intent": "learn"},
        actor="learner",
        idempotency_key=str(uuid4()),
    )
    await append_events(db, learner, [started])
    canvas = await open_canvas(db, learner_id=learner, session_id=session_id)
    return session_id, canvas.canvas_id


async def _lines(db: Db, learner: UUID, type_: str = "canvas.line_committed") -> list[Any]:
    return await db.fetch_all(
        "SELECT payload, actor, session_id FROM core.events "
        "WHERE learner_id = %(learner_id)s AND type = %(type)s ORDER BY seq",
        {"learner_id": learner, "type": type_},
    )


def _render_answer() -> ImageResponse:
    return ImageResponse(image_webp_b64=base64.b64encode(WEBP).decode())


def test_render_request_crops_the_line() -> None:
    """The strokes move to the corner of their own box; the box comes from the points."""
    request = render_request([_stroke("a", (100.0, 50.0), (110.5, 55.0)), _stroke("b", (104, 60))])
    assert (request.width, request.height) == (12, 11)
    assert request.strokes[0].points == [(0.0, 0.0), (10.5, 5.0)]
    assert request.strokes[1].points == [(4.0, 10.0)]


async def test_line_is_rendered_read_and_committed(pg_db: Db, settings: Settings) -> None:
    """Т-§15.3: compute renders the line, `recognizer@1` reads the image, the log gets
    `canvas.line_committed {source: pen}`; a repeated `line_ready` writes nothing new; with
    compute down nothing is written."""
    learner = await create_learner(pg_db)
    session_id, canvas_id = await _canvas(pg_db, learner)
    scenario = ComputeScenario()
    scenario.push_for(RENDER, _render_answer(), _render_answer())
    provider = ScriptedLLMProvider()
    provider.respond("RecognizerOutput", lambda _request: READ)
    resources = build_app_resources(
        settings, None, None, db=pg_db, provider=provider,
        compute=scripted_compute(scenario),
    )  # fmt: skip
    book = LineBook()
    book.add(StrokeBatch(1, (_stroke("s1", (0, 0), (5, 2)), _stroke("s2", (6, 0), (8, 3)))))
    line = book.line("l1", ["s1", "s2", "unknown"], (0.0, 0.0, 8.0, 3.0), 1_000, 2_000)
    where: dict[str, Any] = {
        "learner_id": learner,
        "session_id": session_id,
        "canvas_id": canvas_id,
    }

    committed = await recognize_line(
        resources.db, resources.llm, resources.compute, line=line, **where
    )
    assert committed is not None
    again = await recognize_line(resources.db, resources.llm, resources.compute, line=line, **where)
    assert again == committed

    (row,) = await _lines(pg_db, learner)
    assert row["actor"] == "recognizer@1" and row["session_id"] == session_id
    assert row["payload"] == {
        "canvas_id": str(canvas_id),
        "line_id": "l1",
        "text": "x^2",
        "sympy": "x**2",
        "kind": "latex",
        "bbox": [0.0, 0.0, 8.0, 3.0],
        "stroke_ids": ["s1", "s2"],
        "started_at_ms": 1_000,
        "ended_at_ms": 2_000,
        "source": "pen",
        "photo_id": None,
        "recognition_confidence": 0.9,
        "crossed_out": False,
    }
    # D-B-212: the repeat calls the model again (no outbox); the event stays single by its key.
    calls = [c for c in provider.calls if c.schema_name == "RecognizerOutput"]
    assert len(calls) == 2
    assert calls[0].images == (ImageInput("image/webp", WEBP),)

    down = book.line("l2", ["s1"], (0.0, 0.0, 5.0, 2.0), 3_000, 4_000)
    assert (
        await recognize_line(resources.db, resources.llm, resources.compute, line=down, **where)
        is None
    )  # the render queue is empty: compute answers 503
    assert len(await _lines(pg_db, learner)) == 1


async def test_typed_formula_and_erased_line(pg_db: Db) -> None:
    """Т-§15.3: a typed formula is LaTeX without recognition (`source: typed`); Т-§15.1: a
    recognized line whose strokes are all erased is `canvas.line_erased`."""
    learner = await create_learner(pg_db)
    session_id, canvas_id = await _canvas(pg_db, learner)
    where: dict[str, Any] = {
        "learner_id": learner,
        "session_id": session_id,
        "canvas_id": canvas_id,
    }
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    for latex in ("a+b", "a+b", "a-b"):
        await commit_typed(pg_db, block_id="t1", latex=latex, at_ms=now_ms, **where)
    typed = await _lines(pg_db, learner)
    assert [(r["payload"]["text"], r["payload"]["source"]) for r in typed] == [
        ("a+b", "typed"),
        ("a-b", "typed"),
    ]

    book = LineBook()
    book.add(StrokeBatch(1, (_stroke("s1", (0, 0)), _stroke("s2", (1, 1)))))
    book.recognized(
        CanvasLineCommitted(
            canvas_id=canvas_id, line_id="l1", text="x=1", kind="latex", bbox=(0, 0, 1, 1),
            stroke_ids=["s1", "s2"], started_at_ms=0, ended_at_ms=1, source="pen",
            recognition_confidence=0.8, crossed_out=False,
        )
    )  # fmt: skip
    assert book.add(StrokeBatch(2, erasures=(Erasure(("s1",), 5),))) == []
    assert book.add(StrokeBatch(3, erasures=(Erasure(("s2",), 6),))) == [("l1", "x=1")]
    assert book.line("l1", ["s1", "s2"], (0, 0, 1, 1), 0, 1).strokes == ()
    await erase_line(pg_db, line_id="l1", text="x=1", **where)
    await erase_line(pg_db, line_id="l1", text="x=1", **where)
    erased = await _lines(pg_db, learner, "canvas.line_erased")
    assert [r["payload"] for r in erased] == [
        {"canvas_id": str(canvas_id), "line_id": "l1", "text": "x=1"}
    ]


async def test_socket_recognizes_a_line(pg_db: Db, settings: Settings) -> None:
    """WP21.2–21.3 over the socket: strokes, then `line_ready` → `line_recognized` with the
    reading (П-9.7) while batches keep being acknowledged; a line of unknown strokes —
    `recognized: false`."""
    learner = await create_learner(pg_db)
    _, canvas_id = await _canvas(pg_db, learner)
    scenario = ComputeScenario()
    scenario.push_for(RENDER, _render_answer())
    provider = ScriptedLLMProvider()
    provider.respond("RecognizerOutput", lambda _request: READ)
    cfg = settings.model_copy(update={"stroke_chunk_seconds": 0})
    resources = build_app_resources(
        cfg, None, None, db=pg_db, provider=provider,
        compute=scripted_compute(scenario), storage=InMemoryObjectStore(),
    )  # fmt: skip
    app = create_app(cfg, resources=resources)
    token = await open_session(pg_db, cfg, learner_id=learner, now=datetime.now(UTC))
    stroke = {"stroke_id": "s1", "points": [[10.0, 20.0, 0, 0.5], [14.0, 22.0, 16, 0.6]]}
    async with AsgiWebSocket(
        app, f"/v1/canvases/{canvas_id}/ws", cookies={SESSION_COOKIE: token}
    ) as ws:
        await ws.send_json({"type": "stroke_batch", "batch_no": 1, "strokes": [stroke]})
        assert await ws.receive_json() == {"type": "ack", "batch_no": 1}
        line = {"line_id": "l1", "bbox": [10, 20, 14, 22], "started_at": 0, "ended_at": 16}
        await ws.send_json({"type": "line_ready", "stroke_ids": ["s1"], **line})
        assert await ws.receive_json() == {
            "type": "line_recognized",
            "line_id": "l1",
            "recognized": True,
            "kind": "latex",
            "text": "x^2",
            "sympy": "x**2",
            "confidence": 0.9,
            "crossed_out": False,
        }
        await ws.send_json({"type": "line_ready", "stroke_ids": ["nope"], **line, "line_id": "l2"})
        assert await ws.receive_json() == {
            "type": "line_recognized",
            "line_id": "l2",
            "recognized": False,
        }
    assert len(await _lines(pg_db, learner)) == 1
