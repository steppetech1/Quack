"""The canvas socket across a reconnect and under repeats (ТЗ Ф4 WP21.2, WP21.3, WP22.3; Т-§15.1,
Т-§15.2): a line erased while the model read it, lines and strokes of an earlier socket, a
batch sent again, the lines of a photo reaching the check of the notebook. Real Postgres and
Redis; compute and the models are doubles."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import orjson
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis

from quack.api.app import create_app
from quack.api.auth import SESSION_COOKIE
from quack.auth.sessions import open_session
from quack.canvas import socket as socket_module
from quack.canvas.photo_pipeline import run_photo_pipeline
from quack.compute_client.contracts import AlignResponse, ImageResponse
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import PhotoUploaded
from quack.events.log import NewEvent, append_events
from tests.canvas.steps_support import TRANSITION, events, follows, make_canvas
from tests.db.fixtures import create_learner
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources
from tests.support.storage import InMemoryObjectStore
from tests.support.ws import AsgiWebSocket

RENDER = "/v1/image/render-strokes"
ALIGN = "/v1/image/align"
WEBP = base64.b64encode(b"RIFF\x00\x00\x00\x00WEBPVP8 line").decode()
GRACE_MS = 150


def _reading(text: str) -> dict[str, Any]:
    return {"kind": "latex", "text": text, "sympy": None, "confidence": 0.9, "crossed_out": False}


def _stroke(stroke_id: str, y: float = 20.0) -> dict[str, Any]:
    return {"stroke_id": stroke_id, "points": [[10.0, y, 0, 0.5], [14.0, y + 2, 16, 0.6]]}


def _batch(batch_no: int, *strokes: str, erase: list[str] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": "stroke_batch",
        "batch_no": batch_no,
        "strokes": [_stroke(s) for s in strokes],
    }
    if erase:
        message["erasures"] = [{"stroke_ids": erase, "at_ms": 99}]
    return message


def _line_ready(line_id: str, *stroke_ids: str) -> dict[str, Any]:
    return {
        "type": "line_ready",
        "line_id": line_id,
        "stroke_ids": list(stroke_ids),
        "bbox": [10, 20, 14, 22],
        "started_at": 0,
        "ended_at": 16,
    }


class Rig:
    """One API process: the app, the session cookie, the canvas and the doubles."""

    def __init__(
        self,
        app: FastAPI,
        cookies: dict[str, str],
        learner: UUID,
        canvas_id: UUID,
        scenario: ComputeScenario,
        storage: InMemoryObjectStore,
    ) -> None:
        self.app, self.cookies, self.learner, self.canvas_id = app, cookies, learner, canvas_id
        self.scenario, self.storage = scenario, storage

    def socket(self) -> AsgiWebSocket:
        return AsgiWebSocket(self.app, f"/v1/canvases/{self.canvas_id}/ws", cookies=self.cookies)

    def drawn(self) -> list[list[Any]]:
        """The strokes of every render request compute received."""
        return [
            orjson.loads(r.content)["strokes"]
            for r in self.scenario.requests
            if r.url.path == RENDER
        ]


async def _rig(
    pg_db: Db,
    settings: Settings,
    readings: Iterator[dict[str, Any]],
    *,
    task: bool = False,
    chunk_seconds: int = 0,
    redis: Redis | None = None,
    extra: ComputeScenario | None = None,
) -> Rig:
    learner = await create_learner(pg_db)
    _, canvas = await make_canvas(pg_db, learner, task=task)
    scenario = extra or ComputeScenario()
    scenario.push_for(RENDER, *[ImageResponse(image_webp_b64=WEBP)] * 6)
    provider = ScriptedLLMProvider()
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
            "stroke_chunk_seconds": chunk_seconds,
            "error_grace_ms": GRACE_MS,
            "min_intervention_gap_s": 0,
        }
    )
    storage = InMemoryObjectStore()
    resources = build_app_resources(
        cfg, None, redis, db=pg_db, provider=provider,
        compute=scripted_compute(scenario), storage=storage,
    )  # fmt: skip
    token = await open_session(pg_db, cfg, learner_id=learner, now=datetime.now(UTC))
    app = create_app(cfg, resources=resources)
    return Rig(app, {SESSION_COOKIE: token}, learner, canvas.canvas_id, scenario, storage)


async def _until(condition: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(5):
        while True:
            if await condition():
                return
            await asyncio.sleep(0.02)


async def _subscribed(redis: Redis, canvas_id: UUID) -> None:
    """The socket of the canvas listens to its channel."""
    async with asyncio.timeout(5):
        while True:
            [(_, count)] = await redis.pubsub_numsub(f"canvas:{canvas_id}")
            if count:
                return
            await asyncio.sleep(0.02)


async def test_a_line_erased_while_the_model_reads_it_is_erased_in_the_log(
    pg_db: Db, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Т-§15.1, WP21.5: the learner erased every stroke of the line after `line_ready` and before
    the reading came back. The line is in the log, so `canvas.line_erased` follows it; the
    notebook is not told the erased line was read."""
    rig = await _rig(pg_db, settings, iter([_reading("x^2")]))
    real = socket_module.recognize_line
    release = asyncio.Event()

    async def slow(*args: Any, **kwargs: Any) -> Any:
        committed = await real(*args, **kwargs)  # the reading is written
        await release.wait()  # the learner erases meanwhile
        return committed

    monkeypatch.setattr(socket_module, "recognize_line", slow)

    async def count(type_: str) -> int:
        return len(await events(pg_db, rig.learner, type_))

    async with rig.socket() as ws:
        await ws.send_json(_batch(1, "s1"))
        assert await ws.receive_json() == {"type": "ack", "batch_no": 1}
        await ws.send_json(_line_ready("l1", "s1"))
        await _until(lambda: _is(count("canvas.line_committed"), 1))
        await ws.send_json(_batch(2, erase=["s1"]))
        assert await ws.receive_json() == {"type": "ack", "batch_no": 2}
        release.set()
        await _until(lambda: _is(count("canvas.line_erased"), 1))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.receive_json(), 0.3)
    [erased] = await events(pg_db, rig.learner, "canvas.line_erased")
    assert erased["payload"]["line_id"] == "l1" and erased["payload"]["text"] == "x^2"


async def _is(count: Awaitable[int], value: int) -> bool:
    return await count == value


async def test_a_reconnect_keeps_lines_strokes_and_the_first_reading(
    pg_db: Db, settings: Settings
) -> None:
    """WP21.2-21.3: a new socket starts with an empty memory. (a) Erasing a line read by the
    earlier socket is `canvas.line_erased`; (b) a line begun before the reconnect is drawn with
    its strokes from the chunks, not with the part sent after it; (c) `line_ready` sent again,
    read differently, is answered with the line written first, not dropped as a conflict."""
    rig = await _rig(pg_db, settings, iter([_reading("x^2"), _reading("y"), _reading("z")]))
    async with rig.socket() as ws:
        await ws.send_json(_batch(1, "s1", "s2"))
        assert await ws.receive_json() == {"type": "ack", "batch_no": 1}
        await ws.send_json(_line_ready("l1", "s1", "s2"))
        assert (await ws.receive_json())["text"] == "x^2"
        await ws.send_json(_batch(2, "s3"))
        assert await ws.receive_json() == {"type": "ack", "batch_no": 2}

    async with rig.socket() as ws:
        await ws.send_json(_batch(3, erase=["s1", "s2"]))
        assert await ws.receive_json() == {"type": "ack", "batch_no": 3}

        async def erased_lines() -> bool:
            return len(await events(pg_db, rig.learner, "canvas.line_erased")) == 1

        await _until(erased_lines)  # the erasure is written after the acknowledgement
        [erased] = await events(pg_db, rig.learner, "canvas.line_erased")
        assert erased["payload"]["line_id"] == "l1"

        await ws.send_json(_batch(4, "s4"))
        assert await ws.receive_json() == {"type": "ack", "batch_no": 4}
        await ws.send_json(_line_ready("l2", "s3", "s4"))
        first = await ws.receive_json()
        assert first["recognized"] is True and first["text"] == "y"
        assert [len(strokes) for strokes in rig.drawn()] == [2, 2]  # l1: s1 s2; l2: s3 s4

        await ws.send_json(_line_ready("l2", "s4"))  # again, with other strokes
        again = await ws.receive_json()
        assert again["recognized"] is True and again["text"] == "y"
    committed = await events(pg_db, rig.learner, "canvas.line_committed")
    assert [row["payload"]["line_id"] for row in committed] == ["l1", "l2"]


async def test_a_batch_sent_again_before_its_chunk_counts_once(
    pg_db: Db, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP21.2, WP21.5: a batch the client sends again while its chunk is not yet written is not
    new strokes: the erasure in it is one erasure of the place, not three (a false "stuck")."""
    rig = await _rig(pg_db, settings, iter([]), task=True, chunk_seconds=3600)
    watched = 0
    real = socket_module._watch_strokes  # pyright: ignore[reportPrivateUsage]

    def counting(*args: Any) -> None:
        nonlocal watched
        watched += 1
        real(*args)

    monkeypatch.setattr(socket_module, "_watch_strokes", counting)
    async with rig.socket() as ws:
        for _ in range(3):
            await ws.send_json(_batch(1, "s1", erase=["s1"]))
        await asyncio.sleep(0.2)
    assert watched == 1


async def test_lines_of_a_photo_reach_the_notebook_and_its_check(
    pg_db: Db, settings: Settings, redis: Redis
) -> None:
    """WP22.3 «дальше - проверка шагов и вмешательства, как для пера»: the photo job of `workers`
    tells the canvas of every new line; the notebook gets `line_recognized {source: photo}` and
    its socket checks the lines, so the wrong second line gets its mark after the grace."""
    scenario = ComputeScenario()
    scenario.push_for(TRANSITION, follows(False), follows(False))
    scenario.push_for(ALIGN, AlignResponse(aligned=True, image_webp_b64=WEBP))
    rig = await _rig(pg_db, settings, iter([]), task=True, redis=redis, extra=scenario)
    sheet = [
        {"kind": "latex", "text": "2x=4", "sympy": None, "confidence": 0.9, "crossed_out": False,
         "bbox": {"left": 0.1, "top": 0.1, "right": 0.9, "bottom": 0.15}},
        {"kind": "latex", "text": "x=3", "sympy": None, "confidence": 0.9, "crossed_out": False,
         "bbox": {"left": 0.1, "top": 0.2, "right": 0.9, "bottom": 0.25}},
    ]  # fmt: skip
    provider = ScriptedLLMProvider()
    provider.respond("RecognizerSheetOutput", lambda _request: {"lines": sheet})
    photo_id = uuid4()
    key = f"learners/{rig.learner}/photos/{photo_id}/original.webp"
    await rig.storage.put(key, b"RIFF\x00\x00\x00\x00WEBPVP8 photo", "image/webp")
    await append_events(
        pg_db,
        rig.learner,
        [
            NewEvent(
                type="photo.uploaded",
                payload=PhotoUploaded(canvas_id=rig.canvas_id, photo_id=photo_id, object_key=key),
                actor="learner",
                idempotency_key=f"photo.uploaded:{photo_id}",
            )
        ],
    )
    job_resources = build_app_resources(
        settings, None, redis, db=pg_db, provider=provider, compute=scripted_compute(scenario)
    )
    async with rig.socket() as ws:
        await _subscribed(redis, rig.canvas_id)
        await run_photo_pipeline(
            job_resources.db, job_resources.llm, job_resources.compute, rig.storage, settings,
            learner_id=rig.learner, photo_id=photo_id, redis=redis,
        )  # fmt: skip
        read = [await asyncio.wait_for(ws.receive_json(), 5) for _ in range(2)]
        assert [(m["type"], m["source"], m["recognized"], m["text"]) for m in read] == [
            ("line_recognized", "photo", True, "2x=4"),
            ("line_recognized", "photo", True, "x=3"),
        ]
        mark = await asyncio.wait_for(ws.receive_json(), 5)
    assert mark["type"] == "mark" and mark["kind"] == "error"
    assert mark["line_id"] == read[1]["line_id"]
