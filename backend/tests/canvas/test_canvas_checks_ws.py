"""WP21.4-21.5 over the socket: a line written on a task canvas is checked, the mark comes after
the grace, self-correction and the quiet mode give none, "проверь" shows it (ТЗ Ф4 WP21.4, WP21.5;
Т-§15.4, Т-§15.5; D-C-158, D-C-159). Real Postgres; compute and the models are doubles."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from quack.api.app import create_app
from quack.api.auth import SESSION_COOKIE
from quack.auth.sessions import open_session
from quack.canvas.boards import get_canvas
from quack.compute_client.contracts import ImageResponse
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from tests.canvas.steps_support import TRANSITION, events, follows, make_canvas
from tests.db.fixtures import create_learner
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources
from tests.support.storage import InMemoryObjectStore
from tests.support.ws import AsgiWebSocket

RENDER = "/v1/image/render-strokes"
WEBP = base64.b64encode(b"RIFF\x00\x00\x00\x00WEBPVP8 line").decode()
GRACE_MS = 150


def _reading(text: str, sympy: str) -> dict[str, Any]:
    return {"kind": "latex", "text": text, "sympy": sympy, "confidence": 0.9, "crossed_out": False}


def _readings() -> Iterator[dict[str, Any]]:
    yield _reading("2x=4", "Eq(2*x, 4)")
    yield _reading("x=3", "Eq(x, 3)")


def _stroke(stroke_id: str, y: float) -> dict[str, Any]:
    return {"stroke_id": stroke_id, "points": [[10.0, y, 0, 0.5], [14.0, y + 2, 16, 0.6]]}


class Rig:
    def __init__(self, app: Any, cookies: dict[str, str], learner: UUID, canvas_id: UUID) -> None:
        self.app, self.cookies, self.learner, self.canvas_id = app, cookies, learner, canvas_id


async def _rig(pg_db: Db, settings: Settings) -> Rig:
    learner = await create_learner(pg_db)
    _, canvas = await make_canvas(pg_db, learner)
    scenario = ComputeScenario()
    scenario.push_for(RENDER, *[ImageResponse(image_webp_b64=WEBP)] * 4)
    scenario.push_for(TRANSITION, follows(False), follows(False))
    provider = ScriptedLLMProvider()
    readings = _readings()
    provider.respond("RecognizerOutput", lambda _request: next(readings))
    provider.respond(
        "CanvasDiagnoserOutput",
        lambda request: {
            "error": request.prompt.rstrip().endswith("x=3"),
            "gap_type": "slip" if request.prompt.rstrip().endswith("x=3") else "none",
            "misconception": None,
            "misconception_hypothesis": None,
            "root_cause": None,
            "confidence": 0.9,
        },
    )
    cfg = settings.model_copy(
        update={
            "stroke_chunk_seconds": 0,
            "error_grace_ms": GRACE_MS,
            "min_intervention_gap_s": 0,
        }
    )
    resources = build_app_resources(
        cfg, None, None, db=pg_db, provider=provider,
        compute=scripted_compute(scenario), storage=InMemoryObjectStore(),
    )  # fmt: skip
    token = await open_session(pg_db, cfg, learner_id=learner, now=datetime.now(UTC))
    return Rig(
        create_app(cfg, resources=resources), {SESSION_COOKIE: token}, learner, canvas.canvas_id
    )


async def _write_line(ws: AsgiWebSocket, number: int, y: float) -> None:
    """Strokes of a line, then `line_ready`; waits for the acknowledgement and the reading."""
    await ws.send_json(
        {"type": "stroke_batch", "batch_no": number, "strokes": [_stroke(f"s{number}", y)]}
    )
    assert await ws.receive_json() == {"type": "ack", "batch_no": number}
    box = [10, y, 14, y + 2]
    await ws.send_json(
        {"type": "line_ready", "line_id": f"l{number}", "stroke_ids": [f"s{number}"], "bbox": box,
         "started_at": 0, "ended_at": 16}
    )  # fmt: skip
    reading = await ws.receive_json()
    assert reading["type"] == "line_recognized" and reading["recognized"] is True


async def test_error_mark_comes_after_the_grace(pg_db: Db, settings: Settings) -> None:
    """T-CV4 + T-CV5 over the socket: the first line is read, the second contradicts it; the
    discrepancy is `observation.diagnosed {method: symbolic}` at once, the `mark` comes only
    after `error_grace_ms`, and the mark is in the teacher layer of the board."""
    rig = await _rig(pg_db, settings)
    async with AsgiWebSocket(
        rig.app, f"/v1/canvases/{rig.canvas_id}/ws", cookies=rig.cookies
    ) as ws:
        await _write_line(ws, 1, 20.0)
        await _write_line(ws, 2, 40.0)
        started = asyncio.get_running_loop().time()
        mark = await asyncio.wait_for(ws.receive_json(), 5)
        waited = asyncio.get_running_loop().time() - started
    assert mark == {"type": "mark", "mark_id": "mark:l2", "line_id": "l2", "kind": "error"}
    assert waited < GRACE_MS / 1000 + 1
    [diagnosed] = await events(pg_db, rig.learner, "observation.diagnosed")
    assert diagnosed["payload"]["method"] == "symbolic" and diagnosed["payload"]["line_id"] == "l2"
    doc = (await get_canvas(pg_db, learner_id=rig.learner, canvas_id=rig.canvas_id)).doc
    assert [(m.line_id, m.kind) for m in doc.teacher.marks] == [("l2", "error")]


async def test_erased_line_gets_no_mark(pg_db: Db, settings: Settings) -> None:
    """Т-§15.5: the learner erased the wrong line within the grace - no mark (self-correction);
    the evidence stays in the log as usual."""
    rig = await _rig(pg_db, settings)
    async with AsgiWebSocket(
        rig.app, f"/v1/canvases/{rig.canvas_id}/ws", cookies=rig.cookies
    ) as ws:
        await _write_line(ws, 1, 20.0)
        await _write_line(ws, 2, 40.0)
        erase = {"stroke_ids": ["s2"], "at_ms": 99}
        await ws.send_json({"type": "stroke_batch", "batch_no": 3, "erasures": [erase]})
        assert await ws.receive_json() == {"type": "ack", "batch_no": 3}
        try:
            unexpected = await asyncio.wait_for(ws.receive_json(), GRACE_MS / 1000 * 4)
        except TimeoutError:
            unexpected = None
    assert unexpected is None
    doc = (await get_canvas(pg_db, learner_id=rig.learner, canvas_id=rig.canvas_id)).doc
    assert doc.teacher.marks == []
    assert len(await events(pg_db, rig.learner, "canvas.line_erased")) == 1


async def test_quiet_mode_and_the_check_request(pg_db: Db, settings: Settings) -> None:
    """Т-§15.5: in the quiet mode (`preference.set canvas_quiet`) no unprompted mark comes, the
    evidence is written; "проверь" shows the mark at once, taking the diagnosis already
    written (no second one)."""
    rig = await _rig(pg_db, settings)
    await append_events(
        pg_db,
        rig.learner,
        [
            NewEvent(
                type="preference.set",
                payload={"key": "canvas_quiet", "value": True},
                actor="learner",
                idempotency_key="quiet-on",
            )
        ],
    )
    async with AsgiWebSocket(
        rig.app, f"/v1/canvases/{rig.canvas_id}/ws", cookies=rig.cookies
    ) as ws:
        await _write_line(ws, 1, 20.0)
        await _write_line(ws, 2, 40.0)
        try:
            silent = await asyncio.wait_for(ws.receive_json(), GRACE_MS / 1000 * 4)
        except TimeoutError:
            silent = None
        assert silent is None
        assert len(await events(pg_db, rig.learner, "observation.diagnosed")) == 1

        await ws.send_json({"type": "check_request"})
        mark = await asyncio.wait_for(ws.receive_json(), 5)
    assert mark == {"type": "mark", "mark_id": "mark:l2", "line_id": "l2", "kind": "error"}
    assert len(await events(pg_db, rig.learner, "observation.diagnosed")) == 1
