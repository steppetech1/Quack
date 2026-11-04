"""WP21.2: `WS /v1/canvases/{id}/ws` — sign-in by the cookie, stroke batches acknowledged only
after their chunk is written, repeats by `batch_no`, the store down, `canvas:{id}` between the
processes of the API (ТЗ Ф4 WP21.2; Т-§1.6, Т-§15.1, Т-§15.2, Т-§20.7). Integration tests on the
Phase 4 database (`pg_db`) and Redis."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
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
from quack.canvas.boards import open_canvas
from quack.canvas.socket import publish_canvas
from quack.canvas.strokes import decode_chunk
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.storage.objects import ObjectStore
from tests.db.fixtures import create_learner
from tests.support.resources import build_app_resources
from tests.support.storage import BrokenObjectStore, InMemoryObjectStore
from tests.support.ws import AsgiWebSocket, WebSocketClosed

type AppFor = Callable[..., FastAPI]

STROKE = {"stroke_id": "s1", "points": [[10.0, 20.0, 0, 0.5], [12.0, 21.0, 16, 0.6]]}
CLOSE_POLICY = 1008
CLOSE_INVALID = 1007


@pytest.fixture
async def app_for(pg_db: Db, redis: Redis, settings: Settings) -> AsyncIterator[AppFor]:
    def make(
        *, storage: ObjectStore | None = None, chunk_seconds: int = 0, with_redis: bool = True
    ) -> FastAPI:
        cfg = settings.model_copy(update={"stroke_chunk_seconds": chunk_seconds})
        resources = build_app_resources(
            cfg, None, redis if with_redis else None, db=pg_db, storage=storage
        )
        return create_app(cfg, resources=resources)

    yield make


async def _canvas(db: Db, learner: UUID) -> tuple[UUID, UUID]:
    """A session of the learner and a canvas opened in it: `(session_id, canvas_id)`."""
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


async def _cookie(db: Db, settings: Settings, learner: UUID, **scope: Any) -> dict[str, str]:
    token = await open_session(db, settings, learner_id=learner, now=datetime.now(UTC), **scope)
    return {SESSION_COOKIE: token}


async def _wait_subscribers(redis: Redis, channel: str, count: int) -> None:
    async with asyncio.timeout(5):
        while True:
            [(_, subscribed)] = await redis.pubsub_numsub(channel)
            if subscribed == count:
                return
            await asyncio.sleep(0.02)


def _batch(batch_no: int, **extra: Any) -> dict[str, Any]:
    return {"type": "stroke_batch", "batch_no": batch_no, "strokes": [STROKE], **extra}


def _chunks(store: InMemoryObjectStore, learner: UUID, session_id: UUID) -> list[bytes]:
    prefix = f"learners/{learner}/canvas/{session_id}/"
    return [data for key, (data, _, _) in sorted(store.objects.items()) if key.startswith(prefix)]


async def test_batch_is_acknowledged_after_its_chunk_is_written(
    pg_db: Db, settings: Settings, app_for: AppFor
) -> None:
    """WP21.2, Т-§15.2: `ack {batch_no}` only after the chunk with the batch is in the store;
    a repeated `batch_no` writes nothing new and is acknowledged again (the first `ack` may be
    lost); an erasure is an operation of the journal."""
    learner = await create_learner(pg_db)
    session_id, canvas_id = await _canvas(pg_db, learner)
    store = InMemoryObjectStore()
    app = app_for(storage=store)
    cookies = await _cookie(pg_db, settings, learner)

    async with AsgiWebSocket(app, f"/v1/canvases/{canvas_id}/ws", cookies=cookies) as ws:
        await ws.send_json(_batch(1))
        assert await ws.receive_json() == {"type": "ack", "batch_no": 1}
        [chunk] = _chunks(store, learner, session_id)
        [batch] = decode_chunk(chunk)
        assert batch.batch_no == 1 and batch.strokes[0].stroke_id == "s1"
        assert batch.strokes[0].points[1].t_ms == 16

        await ws.send_json(_batch(1))
        assert await ws.receive_json() == {"type": "ack", "batch_no": 1}
        assert len(_chunks(store, learner, session_id)) == 1

        erasure = {"stroke_ids": ["s1"], "at_ms": 40}
        erase = {"type": "stroke_batch", "batch_no": 2, "erasures": [erasure]}
        await ws.send_json(erase)
        assert await ws.receive_json() == {"type": "ack", "batch_no": 2}
    chunks = _chunks(store, learner, session_id)
    assert len(chunks) == 2
    [erased] = decode_chunk(chunks[1])
    assert erased.erasures[0].stroke_ids == ("s1",)


async def test_pending_batches_are_written_when_the_socket_closes(
    pg_db: Db, settings: Settings, app_for: AppFor
) -> None:
    """Т-§15.2: the server keeps batches up to `stroke_chunk_seconds`; on close it writes them."""
    learner = await create_learner(pg_db)
    session_id, canvas_id = await _canvas(pg_db, learner)
    store = InMemoryObjectStore()
    app = app_for(storage=store, chunk_seconds=3600)
    cookies = await _cookie(pg_db, settings, learner)

    async with AsgiWebSocket(app, f"/v1/canvases/{canvas_id}/ws", cookies=cookies) as ws:
        await ws.send_json(_batch(1))
        await ws.send_json(_batch(2))
        with pytest.raises(TimeoutError):
            await ws.receive_json(timeout_s=0.3)
        assert _chunks(store, learner, session_id) == []
    [chunk] = _chunks(store, learner, session_id)
    assert [b.batch_no for b in decode_chunk(chunk)] == [1, 2]


async def test_store_down_gives_no_ack(pg_db: Db, settings: Settings, app_for: AppFor) -> None:
    """Т-§20.7: with the store down nothing is acknowledged, the socket stays open."""
    learner = await create_learner(pg_db)
    _, canvas_id = await _canvas(pg_db, learner)
    app = app_for(storage=BrokenObjectStore())
    cookies = await _cookie(pg_db, settings, learner)

    async with AsgiWebSocket(app, f"/v1/canvases/{canvas_id}/ws", cookies=cookies) as ws:
        await ws.send_json(_batch(1))
        with pytest.raises(TimeoutError):
            await ws.receive_json(timeout_s=0.5)
        await ws.send_json(_batch(1))  # the client repeats
        with pytest.raises(TimeoutError):
            await ws.receive_json(timeout_s=0.5)


async def test_sign_in_and_scope(pg_db: Db, settings: Settings, app_for: AppFor) -> None:
    """WP21.2, WP18.3, WP22.1: without a session, with another learner's session or with a
    companion session of another canvas the socket is refused; a companion session of this
    canvas gets the status only — any client message, even a malformed one, closes it with
    1008 (D-C-124)."""
    owner, other = await create_learner(pg_db), await create_learner(pg_db)
    _, canvas_id = await _canvas(pg_db, owner)
    _, another_canvas = await _canvas(pg_db, owner)
    app = app_for(storage=InMemoryObjectStore())
    path = f"/v1/canvases/{canvas_id}/ws"

    refused = [
        {},
        await _cookie(pg_db, settings, other),
        await _cookie(pg_db, settings, owner, scope="companion", canvas_id=another_canvas),
        {SESSION_COOKIE: "not-a-session"},
    ]
    for cookies in refused:
        with pytest.raises(WebSocketClosed) as closed:
            async with AsgiWebSocket(app, path, cookies=cookies):
                pass
        assert closed.value.code == CLOSE_POLICY
    with pytest.raises(WebSocketClosed) as unknown:
        async with AsgiWebSocket(
            app, f"/v1/canvases/{uuid4()}/ws", cookies=await _cookie(pg_db, settings, owner)
        ):
            pass
    assert unknown.value.code == CLOSE_POLICY

    companion = await _cookie(pg_db, settings, owner, scope="companion", canvas_id=canvas_id)
    for message in (orjson.dumps(_batch(1)).decode(), "not json"):
        async with AsgiWebSocket(app, path, cookies=companion) as ws:
            await ws.send_text(message)
            with pytest.raises(WebSocketClosed) as closed:
                await ws.receive_json()
            assert closed.value.code == CLOSE_POLICY, message


async def test_malformed_message_closes_the_socket(
    pg_db: Db, settings: Settings, app_for: AppFor
) -> None:
    learner = await create_learner(pg_db)
    _, canvas_id = await _canvas(pg_db, learner)
    app = app_for(storage=InMemoryObjectStore())
    cookies = await _cookie(pg_db, settings, learner)
    for bad in ("not json", '{"type": "unknown"}', '{"type": "stroke_batch"}'):
        async with AsgiWebSocket(app, f"/v1/canvases/{canvas_id}/ws", cookies=cookies) as ws:
            await ws.send_text(bad)
            with pytest.raises(WebSocketClosed) as closed:
                await ws.receive_json()
            assert closed.value.code == CLOSE_INVALID, bad


async def test_canvas_channel_reaches_every_socket(
    pg_db: Db, settings: Settings, redis: Redis, app_for: AppFor
) -> None:
    """Т-§15.2, Т-§1.6: devices of one canvas — the laptop and the companion, in any API
    process — get its changes through Redis pub/sub `canvas:{id}`; the companion only the
    status (WP22.1, D-C-124)."""
    learner = await create_learner(pg_db)
    _, canvas_id = await _canvas(pg_db, learner)
    laptop_app, phone_app = app_for(), app_for()  # two API processes over one Redis
    path = f"/v1/canvases/{canvas_id}/ws"
    full = await _cookie(pg_db, settings, learner)
    companion = await _cookie(pg_db, settings, learner, scope="companion", canvas_id=canvas_id)

    async with AsyncExitStack() as stack:
        laptop = await stack.enter_async_context(AsgiWebSocket(laptop_app, path, cookies=full))
        phone = await stack.enter_async_context(AsgiWebSocket(phone_app, path, cookies=companion))
        update = {"type": "layer_update", "version": 2}
        closed = {"type": "task_closed", "task_id": str(uuid4())}
        await _wait_subscribers(redis, f"canvas:{canvas_id}", 2)
        await publish_canvas(redis, canvas_id, update)
        await publish_canvas(redis, canvas_id, closed)
        assert await laptop.receive_json() == update
        assert await laptop.receive_json() == closed
        assert await phone.receive_json() == closed


async def test_strokes_work_without_redis(pg_db: Db, settings: Settings, app_for: AppFor) -> None:
    """§4.2: `redis = None` — no relay between processes, the strokes still go to the store."""
    learner = await create_learner(pg_db)
    session_id, canvas_id = await _canvas(pg_db, learner)
    store = InMemoryObjectStore()
    app = app_for(storage=store, with_redis=False)
    cookies = await _cookie(pg_db, settings, learner)
    async with AsgiWebSocket(app, f"/v1/canvases/{canvas_id}/ws", cookies=cookies) as ws:
        await ws.send_json(_batch(7))
        assert await ws.receive_json() == {"type": "ack", "batch_no": 7}
    assert len(_chunks(store, learner, session_id)) == 1
