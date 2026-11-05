"""WP21.3: correction of how a line was read (ТЗ Ф4 WP21.3; Т-§2.8, П-9.7; D-C-137)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx

from quack.api.app import create_app
from quack.canvas.boards import open_canvas
from quack.canvas.recognition import commit_typed
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from tests.db.fixtures import create_learner
from tests.support.auth import login
from tests.support.resources import build_app_resources


async def _canvas_with_line(db: Db, learner: UUID) -> UUID:
    session_id = uuid4()
    started = NewEvent(
        type="session.started",
        payload={"session_id": str(session_id), "domain_id": str(uuid4()), "intent": "learn"},
        actor="learner",
        idempotency_key=str(uuid4()),
    )
    await append_events(db, learner, [started])
    canvas = await open_canvas(db, learner_id=learner, session_id=session_id)
    await commit_typed(
        db, learner_id=learner, session_id=session_id, canvas_id=canvas.canvas_id,
        block_id="l1", latex="x^2", at_ms=0,
    )  # fmt: skip
    return canvas.canvas_id


async def _corrections(db: Db, learner: UUID) -> list[Any]:
    rows = await db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'handwriting.corrected' ORDER BY seq",
        {"learner_id": learner},
    )
    return [r["payload"] for r in rows]


async def test_correction_writes_handwriting_corrected(pg_db: Db, settings: Settings) -> None:
    """Т-§2.8: the learner's reading goes to the log as `handwriting.corrected`; a repeat with
    the key writes nothing, another text with it is 409; an unknown line or another learner's
    canvas is 404."""
    learner, stranger = await create_learner(pg_db), await create_learner(pg_db)
    canvas_id = await _canvas_with_line(pg_db, learner)
    url = f"/v1/canvases/{canvas_id}/lines/l1/correct"
    body = {"idempotency_key": "fix-1", "text": "x^3", "sympy": "x**3"}
    resources = build_app_resources(settings, None, None, db=pg_db)
    app = create_app(settings, resources=resources)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await login(client, pg_db, learner, settings)
        first = await client.post(url, json=body)
        assert first.status_code == 200, first.text
        assert first.json() == {
            "canvas_id": str(canvas_id), "line_id": "l1", "text": "x^3", "sympy": "x**3"
        }  # fmt: skip
        again = await client.post(url, json=body)
        assert again.status_code == 200 and again.json() == first.json()
        other = await client.post(url, json=body | {"text": "x^4"})
        assert other.status_code == 409
        missing = await client.post(
            f"/v1/canvases/{canvas_id}/lines/nope/correct", json=body | {"idempotency_key": "2"}
        )
        assert missing.status_code == 404

        await login(client, pg_db, stranger, settings)
        foreign = await client.post(url, json=body | {"idempotency_key": "3"})
        assert foreign.status_code == 404

    assert await _corrections(pg_db, learner) == [
        {"canvas_id": str(canvas_id), "line_id": "l1", "text": "x^3", "sympy": "x**3"}
    ]
