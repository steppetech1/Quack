"""WP21.6: replay of a canvas (ТЗ Ф4 WP21.6; Т-§15.7; D-C-135)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from quack.api.app import create_app
from quack.canvas.boards import open_canvas
from quack.canvas.strokes import Point, Stroke, StrokeBatch, StrokeJournal
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.storage.keys import canvas_chunk_prefix
from tests.db.fixtures import create_learner
from tests.support.auth import login
from tests.support.resources import build_app_resources
from tests.support.storage import InMemoryObjectStore


async def _session(db: Db, learner: UUID) -> UUID:
    session_id = uuid4()
    started = NewEvent(
        type="session.started",
        payload={"session_id": str(session_id), "domain_id": str(uuid4()), "intent": "learn"},
        actor="learner",
        idempotency_key=str(uuid4()),
    )
    await append_events(db, learner, [started])
    return session_id


def _batch(batch_no: int) -> StrokeBatch:
    return StrokeBatch(batch_no, (Stroke(f"s{batch_no}", (Point(1.0, 2.0, 0, 0.5),)),))


async def test_replay_links_the_chunks_of_the_canvas_in_written_order(
    pg_db: Db, settings: Settings
) -> None:
    """Т-§15.7: signed links to the stroke chunks of this canvas only, oldest first by the ULID
    (a new socket starts the chunk number again); another learner's canvas is 404."""
    learner, stranger = await create_learner(pg_db), await create_learner(pg_db)
    session_id = await _session(pg_db, learner)
    canvas = await open_canvas(pg_db, learner_id=learner, session_id=session_id)
    other = await open_canvas(pg_db, learner_id=learner, session_id=session_id)
    storage = InMemoryObjectStore()

    def journal(canvas_id: UUID) -> StrokeJournal:
        return StrokeJournal(
            storage, learner_id=learner, session_id=session_id, canvas_id=canvas_id,
            chunk_seconds=10, max_batches=100,
        )  # fmt: skip

    now = datetime.now(UTC)
    for socket_no in range(2):  # two sockets: chunk numbers 0, 1, then 0 again
        await asyncio.sleep(0.002)  # the sockets are apart in time
        first = journal(canvas.canvas_id)
        for n in range(2 - socket_no):
            first.add(_batch(10 * socket_no + n), now=now)
            await first.flush()
    second = journal(other.canvas_id)
    second.add(_batch(99), now=now)
    await second.flush()

    prefix = canvas_chunk_prefix(learner, session_id, canvas.canvas_id)
    written = [key for key in storage.objects if key.startswith(prefix)]
    assert [key.removeprefix(prefix).split("-")[0] for key in written] == ["0", "1", "0"]

    resources = build_app_resources(settings, None, None, db=pg_db, storage=storage)
    app = create_app(settings, resources=resources)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await login(client, pg_db, learner, settings)
        response = await client.get(f"/v1/canvases/{canvas.canvas_id}/replay")
        assert response.status_code == 200
        body = response.json()
        assert body["expires_in_s"] == settings.object_url_ttl_s
        assert [chunk["url"] for chunk in body["chunks"]] == [
            f"memory://quack-learners/{key}?ttl={settings.object_url_ttl_s}" for key in written
        ]
        assert all(chunk["size"] > 0 for chunk in body["chunks"])

        await login(client, pg_db, stranger, settings)
        response = await client.get(f"/v1/canvases/{canvas.canvas_id}/replay")
        assert response.status_code == 404
