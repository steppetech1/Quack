"""E2E: the canvas journey (ТЗ Ф4 §8.14, T-E6; WP21, Т-§15).

A canvas on a task of the session → strokes → lines (read by the recognizer) → a transition with
an error → `observation.diagnosed` at once, the `mark` only after `error_grace_ms` → the
correction of the line → a hint → the final answer from the canvas → the verdict, which counts
the hint as help."""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

from quack.api.auth import SESSION_COOKIE
from quack.compute_client.contracts import ImageResponse
from tests.canvas.steps_support import SPACE, TRANSITION, follows, make_task
from tests.e2e.conftest import StandFactory
from tests.e2e.journey import ok, onboard
from tests.e2e.system import System
from tests.e2e.text_journey import act, event_types, script_teacher, start_session
from tests.support.ws import AsgiWebSocket

RENDER = "/v1/image/render-strokes"
WEBP = base64.b64encode(b"RIFF\x00\x00\x00\x00WEBPVP8 line").decode()
GRACE_MS = 150


def reading(text: str, sympy: str) -> dict[str, Any]:
    return {"kind": "latex", "text": text, "sympy": sympy, "confidence": 0.9, "crossed_out": False}


def readings() -> Iterator[dict[str, Any]]:
    yield reading("2x=4", "Eq(2*x, 4)")
    yield reading("x=3", "Eq(x, 3)")


def stroke(stroke_id: str, y: float) -> dict[str, Any]:
    return {"stroke_id": stroke_id, "points": [[10.0, y, 0, 0.5], [14.0, y + 2, 16, 0.6]]}


async def write_line(ws: AsgiWebSocket, number: int, y: float) -> None:
    """Strokes of a line, then `line_ready`; waits for the acknowledgement and the reading."""
    await ws.send_json(
        {"type": "stroke_batch", "batch_no": number, "strokes": [stroke(f"s{number}", y)]}
    )
    assert await ws.receive_json() == {"type": "ack", "batch_no": number}
    await ws.send_json(
        {"type": "line_ready", "line_id": f"l{number}", "stroke_ids": [f"s{number}"],
         "bbox": [10, y, 14, y + 2], "started_at": 0, "ended_at": 16}
    )  # fmt: skip
    answer = await ws.receive_json()
    assert answer["type"] == "line_recognized" and answer["recognized"] is True, answer


async def test_canvas_journey(stand: StandFactory) -> None:
    """T-E6."""
    update = {"stroke_chunk_seconds": 0, "error_grace_ms": GRACE_MS, "min_intervention_gap_s": 0}
    async with stand(settings_update=update) as system:
        await run_canvas_journey(system)


async def run_canvas_journey(system: System) -> None:
    system.compute.push_for(RENDER, *[ImageResponse(image_webp_b64=WEBP)] * 4)
    system.compute.push_for(TRANSITION, follows(False), follows(False))
    lines = readings()
    system.llm.respond("RecognizerOutput", lambda _r: next(lines))
    system.llm.respond(
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
    script_teacher(system, ())

    learner = await system.sign_in_by_link()
    domain_id, topic_id = await onboard(system, learner, "te6")
    session_id = await start_session(system, learner, domain_id, topic_id)
    task_id = await make_task(system.resources.db, learner.learner_id, space=SPACE)
    canvas = ok(
        await system.client.post(
            f"/v1/sessions/{session_id}/canvases",
            json={"task_id": str(task_id)},
            headers=learner.headers,
        ),
        201,
    )
    canvas_id = UUID(canvas["canvas_id"])
    found = re.search(rf"{SESSION_COOKIE}=([^;]+)", learner.headers["Cookie"])
    assert found is not None

    async with AsgiWebSocket(
        system.app, f"/v1/canvases/{canvas_id}/ws", cookies={SESSION_COOKIE: found.group(1)}
    ) as ws:
        await write_line(ws, 1, 20.0)
        await write_line(ws, 2, 40.0)
        started = asyncio.get_running_loop().time()
        mark = await asyncio.wait_for(ws.receive_json(), 5)
        waited = asyncio.get_running_loop().time() - started
    assert mark == {"type": "mark", "mark_id": "mark:l2", "line_id": "l2", "kind": "error"}
    assert waited < GRACE_MS / 1000 + 1
    types = [t for _, t in await event_types(system, learner)]
    assert types.count("observation.diagnosed") == 1, types

    # The learner corrects the misread line, then asks for a hint.
    fixed = await system.client.post(
        f"/v1/canvases/{canvas_id}/lines/l2/correct",
        json={"text": "x=2", "sympy": "Eq(x, 2)", "idempotency_key": str(uuid4())},
        headers=learner.headers,
    )
    assert fixed.status_code == 200, fixed.text
    hint = await act(system, learner, session_id, {"kind": "hint_request", "task_id": str(task_id)})
    assert hint.status_code == 200, hint.text
    await system.settle()
    types = [t for _, t in await event_types(system, learner)]
    assert "handwriting.corrected" in types and "hint.shown" in types, types

    # The final answer from the canvas: the verdict counts the hint as help.
    final = await act(
        system,
        learner,
        session_id,
        {
            "kind": "answer",
            "task_id": str(task_id),
            "answer": {"kind": "number", "value": "2"},
            "source": "canvas",
        },
    )
    assert final.status_code == 200, final.text
    row = await system.resources.db.fetch_one(
        "SELECT payload FROM core.events WHERE learner_id = %(l)s AND type = 'verdict.recorded' "
        "ORDER BY seq DESC LIMIT 1",
        {"l": learner.learner_id},
    )
    assert row is not None
    assert row["payload"]["outcome"] == "correct" and row["payload"]["help_step"] == 1, row
