"""WP22.3: the photo pipeline of a canvas (ТЗ Ф4 WP22.3; Т-§22.6; D-C-134)."""

from __future__ import annotations

import base64
import json
from typing import Any
from uuid import UUID, uuid4

from quack.canvas.boards import get_canvas, open_canvas
from quack.canvas.photo_pipeline import align_lines, normalized_levenshtein, run_photo_pipeline
from quack.compute_client.contracts import AlignResponse, ImageResponse
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import PhotoUploaded
from quack.events.log import NewEvent, append_events
from quack.jobs.runner import JOB_HANDLERS
from quack.llm.media import BoxPrompt
from quack.llm.provider import ImageInput
from tests.db.fixtures import create_learner
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.images import decode_png, webp_vp8x
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources
from tests.support.storage import InMemoryObjectStore

ALIGN = "/v1/image/align"
CLEAN = "/v1/image/clean-drawing"
ALIGNED = b"RIFF\x00\x00\x00\x00WEBPVP8 aligned"
CLEANED = b"RIFF\x00\x00\x00\x00WEBPVP8 cleaned"


def _line(text: str, top: float, *, crossed: bool = False, kind: str = "latex") -> dict[str, Any]:
    return {
        "kind": kind,
        "text": text,
        "sympy": None,
        "bbox": {"left": 0.1, "top": top, "right": 0.9, "bottom": round(top + 0.05, 2)},
        "confidence": 0.8,
        "crossed_out": crossed,
    }


def test_normalized_levenshtein() -> None:
    assert normalized_levenshtein("", "") == 0.0
    assert normalized_levenshtein("2x+4=10", "2x+4=10") == 0.0
    assert normalized_levenshtein("2x+4=10", "2x+4=12") == 1 / 7
    assert normalized_levenshtein("abc", "") == 1.0


def test_alignment_keeps_order_and_finds_inserted_lines() -> None:
    """Т-§22.6 п.3: a line written between two old ones is new; a slightly changed line matches
    its old self; a line far from every old one matches nothing."""
    previous = ["2x+4=10", "2x=6", "x=3"]
    current = ["2x+4=10", "2x=10-4", "2x=6", "x=3.0", "answer: three"]
    assert align_lines(previous, current, 0.2) == {0: 0, 2: 1}
    assert align_lines(previous, current, 0.5) == {0: 0, 2: 1, 3: 2}
    assert align_lines([], current, 0.2) == {}


async def _photo(db: Db, storage: InMemoryObjectStore, learner: UUID, canvas_id: UUID) -> UUID:
    photo_id = uuid4()
    key = f"learners/{learner}/photos/{photo_id}/original.webp"
    await storage.put(key, b"RIFF\x00\x00\x00\x00WEBPVP8 photo", "image/webp")
    uploaded = NewEvent(
        type="photo.uploaded",
        payload=PhotoUploaded(canvas_id=canvas_id, photo_id=photo_id, object_key=key),
        actor="learner",
        idempotency_key=f"photo.uploaded:{photo_id}",
    )
    await append_events(db, learner, [uploaded])
    return photo_id


async def _log(db: Db, learner: UUID) -> list[tuple[str, str, Any]]:
    rows = await db.fetch_all(
        "SELECT type, payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type IN ('canvas.line_committed', 'canvas.line_erased') ORDER BY seq",
        {"learner_id": learner},
    )
    return [(r["type"], r["payload"]["line_id"], r["payload"]["text"]) for r in rows]


async def test_photos_of_one_sheet_write_only_new_lines(pg_db: Db, settings: Settings) -> None:
    """WP22.3 п.1–4: the aligned photo is read in one call; the second photo of the sheet writes
    its new and changed lines and erases the newly crossed-out one; a repeated job writes
    nothing; a photo where little of the last one matches opens a new sheet."""
    learner = await create_learner(pg_db)
    session_id = uuid4()
    started = NewEvent(
        type="session.started",
        payload={"session_id": str(session_id), "domain_id": str(uuid4()), "intent": "learn"},
        actor="learner",
        idempotency_key=str(uuid4()),
    )
    await append_events(pg_db, learner, [started])
    canvas = await open_canvas(pg_db, learner_id=learner, session_id=session_id)
    storage = InMemoryObjectStore()
    scenario = ComputeScenario()
    answer = AlignResponse(aligned=True, image_webp_b64=base64.b64encode(ALIGNED).decode())
    scenario.push_for(ALIGN, *[answer] * 5)
    sheet: dict[str, list[dict[str, Any]]] = {"lines": []}
    provider = ScriptedLLMProvider()
    provider.respond("RecognizerSheetOutput", lambda _request: dict(sheet))
    resources = build_app_resources(
        settings, None, None, db=pg_db, provider=provider,
        compute=scripted_compute(scenario), storage=storage,
    )  # fmt: skip

    async def run(lines: list[dict[str, Any]]) -> Any:
        photo_id = await _photo(pg_db, storage, learner, canvas.canvas_id)
        sheet["lines"] = lines
        result = await run_photo_pipeline(
            resources.db, resources.llm, resources.compute, storage, settings,
            learner_id=learner, photo_id=photo_id,
        )  # fmt: skip
        return photo_id, result

    first, result = await run([_line("2x+4=10", 0.1), _line("2x=6", 0.2)])
    assert result.committed == [f"{first}:{first}:0", f"{first}:{first}:1"]
    assert not result.new_sheet
    (call,) = provider.calls
    assert call.schema_name == "RecognizerSheetOutput"
    assert call.images == (ImageInput("image/webp", ALIGNED),)

    second, result = await run(
        [
            _line("2x+4=10", 0.1),
            _line("2x=10-4", 0.15),
            _line("2x=6", 0.2, crossed=True),
            _line("", 0.3, kind="drawing"),
            _line("x+y=133", 0.4),
        ]
    )
    assert result.drawings == 1 and not result.new_sheet
    # The aligned photo of this test is not a WebP of a known size, so the drawing has no box in
    # pixels: it is counted and skipped without a call of the model (D-C-153).
    assert result.drawings_placed == [] and provider.segment_calls == []
    assert result.committed == [f"{first}:{second}:1", f"{first}:{second}:3"]
    assert result.erased == [f"{first}:{first}:1"]
    log = await _log(pg_db, learner)
    assert log[2:] == [
        ("canvas.line_committed", f"{first}:{second}:1", "2x=10-4"),
        ("canvas.line_erased", f"{first}:{first}:1", "2x=6"),
        ("canvas.line_committed", f"{first}:{second}:3", "x+y=133"),
    ]
    rows = await pg_db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'canvas.line_committed' ORDER BY seq DESC LIMIT 1",
        {"learner_id": learner},
    )
    payload = rows[0]["payload"]
    assert payload["source"] == "photo" and payload["photo_id"] == str(second)
    assert payload["bbox"] == [0.1, 0.4, 0.9, 0.45] and payload["stroke_ids"] == []

    # A changed line keeps its id (`x+y=133` → `x+y=134`).
    third, result = await run(
        [
            _line("2x+4=10", 0.1),
            _line("2x=10-4", 0.15),
            _line("2x=6", 0.2, crossed=True),
            _line("x+y=134", 0.4),
        ]
    )
    assert result.committed == [f"{first}:{second}:3"] and result.erased == []

    # The repeated job of the third photo: the same reading, nothing new.
    before = await _log(pg_db, learner)
    again = await run_photo_pipeline(
        resources.db, resources.llm, resources.compute, storage, settings,
        learner_id=learner, photo_id=third,
    )  # fmt: skip
    assert again is not None and again.committed == [] and again.erased == []
    assert await _log(pg_db, learner) == before

    fourth, result = await run([_line("\\int_0^1 x\\,dx", 0.1), _line("2x=6", 0.2)])
    assert result.new_sheet
    assert result.committed == [f"{fourth}:{fourth}:0", f"{fourth}:{fourth}:1"]


async def test_a_line_struck_out_written_again_and_struck_out_again_is_erased_again(
    pg_db: Db, settings: Settings
) -> None:
    """WP22.3 п.3: the crossed-out state is that of the last writing of the line. A line struck
    out in one photo, written again in the next and struck out in a third is erased twice (the
    old erasure does not stand for the new writing)."""
    learner = await create_learner(pg_db)
    session_id = uuid4()
    started = NewEvent(
        type="session.started",
        payload={"session_id": str(session_id), "domain_id": str(uuid4()), "intent": "learn"},
        actor="learner",
        idempotency_key=str(uuid4()),
    )
    await append_events(pg_db, learner, [started])
    canvas = await open_canvas(pg_db, learner_id=learner, session_id=session_id)
    storage = InMemoryObjectStore()
    scenario = ComputeScenario()
    answer = AlignResponse(aligned=True, image_webp_b64=base64.b64encode(ALIGNED).decode())
    scenario.push_for(ALIGN, *[answer] * 4)
    sheet: dict[str, list[dict[str, Any]]] = {"lines": []}
    provider = ScriptedLLMProvider()
    provider.respond("RecognizerSheetOutput", lambda _request: dict(sheet))
    resources = build_app_resources(
        settings, None, None, db=pg_db, provider=provider,
        compute=scripted_compute(scenario), storage=storage,
    )  # fmt: skip

    async def run(line: dict[str, Any]) -> Any:
        photo_id = await _photo(pg_db, storage, learner, canvas.canvas_id)
        sheet["lines"] = [line]
        await run_photo_pipeline(
            resources.db, resources.llm, resources.compute, storage, settings,
            learner_id=learner, photo_id=photo_id,
        )  # fmt: skip
        return photo_id

    first = await run(_line("2x+4=10", 0.1))
    await run(_line("2x+4=10", 0.1, crossed=True))  # struck out
    await run(_line("2x+4=12", 0.1))  # written again, a little changed
    await run(_line("2x+4=12", 0.1, crossed=True))  # struck out again
    log = await _log(pg_db, learner)
    line_id = f"{first}:{first}:0"
    assert [kind for kind, _, _ in log] == [
        "canvas.line_committed",
        "canvas.line_erased",
        "canvas.line_committed",
        "canvas.line_erased",
    ]
    assert {line for _, line, _ in log} == {line_id}


def test_pipeline_job_is_registered() -> None:
    """`photo.pipeline` (P1, stream A's dispatcher row) has its handler in `quack.canvas`."""
    assert JOB_HANDLERS["photo.pipeline"].__module__ == "quack.canvas.photo_pipeline"


async def _canvas_with_pipeline(
    pg_db: Db, settings: Settings
) -> tuple[UUID, Any, InMemoryObjectStore, ComputeScenario, ScriptedLLMProvider, Any]:
    learner = await create_learner(pg_db)
    session_id = uuid4()
    started = NewEvent(
        type="session.started",
        payload={"session_id": str(session_id), "domain_id": str(uuid4()), "intent": "learn"},
        actor="learner",
        idempotency_key=str(uuid4()),
    )
    await append_events(pg_db, learner, [started])
    canvas = await open_canvas(pg_db, learner_id=learner, session_id=session_id)
    storage = InMemoryObjectStore()
    scenario = ComputeScenario()
    provider = ScriptedLLMProvider()
    resources = build_app_resources(
        settings, None, None, db=pg_db, provider=provider,
        compute=scripted_compute(scenario), storage=storage,
    )  # fmt: skip
    return learner, canvas, storage, scenario, provider, resources


def _sam(box: tuple[int, int, int, int]) -> str:
    """The answer of `sam-3.1` for one object whose mask is filled with ones."""
    x1, y1, x2, y2 = box
    height, width = y2 - y1, x2 - x1
    data = base64.b85encode(bytes([1]) * (height * width)).decode()
    return (
        f"<0f><|box;x1={x1};y1={y1};x2={x2};y2={y2};w=200;h=100;c=0.9|>"
        f"<|mask;x={x1};y={y1};c=0.9;data={height},{width},~{data}|>"
    )


def _drawing(left: float, top: float, right: float, bottom: float) -> dict[str, Any]:
    return {
        "kind": "drawing", "text": "", "sympy": None, "confidence": 0.7, "crossed_out": False,
        "bbox": {"left": left, "top": top, "right": right, "bottom": bottom},
    }  # fmt: skip


async def test_sam_only_for_drawings(pg_db: Db, settings: Settings) -> None:
    """T-CP3 (WP22.3 step 5): `segmenter` is called once per area the recognizer marked as a
    drawing, by the box of the area in pixels; the drawing is cleaned by compute with the mask
    of the model, stored, and laid into the learner layer of the board; a repeated job and a
    photo without drawings call nothing."""
    learner, canvas, storage, scenario, provider, resources = await _canvas_with_pipeline(
        pg_db, settings
    )
    aligned = webp_vp8x(200, 100)
    answer = AlignResponse(aligned=True, image_webp_b64=base64.b64encode(aligned).decode())
    scenario.push_for(ALIGN, *[answer] * 4)
    cleaned = ImageResponse(image_webp_b64=base64.b64encode(CLEANED).decode())
    scenario.push_for(CLEAN, *[cleaned] * 4)
    sheet: dict[str, list[dict[str, Any]]] = {"lines": []}
    provider.respond("RecognizerSheetOutput", lambda _request: dict(sheet))
    # `sam-3.1` finds the first drawing with a mask and nothing for the second.
    provider.segment_text = lambda request: (
        _sam((20, 10, 60, 30)) if request.box and request.box.x1 == 20 else ""
    )

    async def run(lines: list[dict[str, Any]], photo_id: UUID | None = None) -> Any:
        photo_id = photo_id or await _photo(pg_db, storage, learner, canvas.canvas_id)
        sheet["lines"] = lines
        result = await run_photo_pipeline(
            resources.db, resources.llm, resources.compute, storage, settings,
            learner_id=learner, photo_id=photo_id,
        )  # fmt: skip
        return photo_id, result

    # No drawing on the sheet: `sam-3.1` is not called.
    _, result = await run([_line("2x=6", 0.1)])
    assert result.drawings == 0 and provider.segment_calls == []

    sheet_lines = [_line("2x=6", 0.1), _drawing(0.1, 0.1, 0.3, 0.3), _drawing(0.5, 0.5, 0.9, 0.9)]
    photo, result = await run(sheet_lines)
    assert result.drawings == 2 and result.drawings_placed == [f"{photo}:0", f"{photo}:1"]
    first, second = provider.segment_calls
    assert first.model == "sam-3.1" and first.concept is None
    assert first.box == BoxPrompt(20, 10, 60, 30, 200, 100)
    assert second.box == BoxPrompt(100, 50, 180, 90, 200, 100)
    assert first.image == ImageInput("image/webp", aligned)

    bodies = [json.loads(r.content) for r in scenario.requests if r.url.path == CLEAN]
    assert len(bodies) == 2
    assert all(base64.b64decode(b["image_b64"]) == aligned for b in bodies)
    width, height, rows = decode_png(base64.b64decode(bodies[0]["mask_b64"]))
    assert (width, height) == (200, 100)
    assert rows[10][20:60] == bytes([255]) * 40
    assert rows[9] == bytes(200) and rows[30] == bytes(200)
    # `sam-3.1` found nothing for the second area: the area of the recognizer is the mask.
    _, _, rows = decode_png(base64.b64decode(bodies[1]["mask_b64"]))
    assert rows[50][100:180] == bytes([255]) * 80 and rows[49] == bytes(200)

    doc = (await get_canvas(pg_db, learner_id=learner, canvas_id=canvas.canvas_id)).doc
    placed = doc.learner.drawings
    assert [d.drawing_id for d in placed] == [f"{photo}:0", f"{photo}:1"]
    assert placed[0].photo_id == photo and placed[0].bbox == (0.1, 0.1, 0.3, 0.3)
    for drawing in placed:
        assert drawing.object_key.startswith(f"learners/{learner}/photos/{photo}/drawing")
        assert storage.objects[drawing.object_key][:2] == (CLEANED, "image/webp")

    # The repeated job: the drawings are on the board, nothing is called, nothing is added.
    _, again = await run(sheet_lines, photo)
    assert again.drawings_placed == [] and again.drawings == 2
    assert len(provider.segment_calls) == 2
    doc = (await get_canvas(pg_db, learner_id=learner, canvas_id=canvas.canvas_id)).doc
    assert len(doc.learner.drawings) == 2
