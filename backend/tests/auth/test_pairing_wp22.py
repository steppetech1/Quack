"""WP22.1: pairing of the phone, the companion session (ТЗ Ф4 §8.11 `T-CP1`; Т-§16.1; D-C-128).

A token of the learner's canvas is one-time and expires after `pairing_ttl_s`; the companion
session it gives opens the socket of its canvas only for the status, and every other route
answers it 403."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from quack.api.app import create_app
from quack.api.auth import SESSION_COOKIE
from quack.auth.pairing import CanvasNotPairable, issue_pairing_token, redeem_pairing_token
from quack.auth.sessions import open_session, resolve_session
from quack.canvas.boards import open_canvas
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from tests.db.fixtures import create_learner
from tests.support.resources import build_app_resources
from tests.support.ws import AsgiWebSocket, WebSocketClosed

BASE = "https://api.qvack.online"


async def _canvas(db: Db, learner: UUID, task_id: UUID | None = None) -> tuple[UUID, UUID]:
    session_id = uuid4()
    started = NewEvent(
        type="session.started",
        payload={"session_id": str(session_id), "domain_id": str(uuid4()), "intent": "learn"},
        actor="learner",
        idempotency_key=str(uuid4()),
    )
    await append_events(db, learner, [started])
    canvas = await open_canvas(db, learner_id=learner, session_id=session_id, task_id=task_id)
    return session_id, canvas.canvas_id


@pytest.fixture
async def client(pg_db: Db, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    resources = build_app_resources(settings, None, None, db=pg_db)
    app = create_app(settings, resources=resources)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE
    ) as test_client:
        test_client.app = app  # type: ignore[attr-defined]
        yield test_client


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


async def test_token_is_one_time_and_expires(pg_db: Db, settings: Settings) -> None:
    """Т-§16.1: only the hash is stored; the second exchange and an expired token give
    nothing; the companion session is bound to the canvas."""
    learner = await create_learner(pg_db)
    session_id, canvas_id = await _canvas(pg_db, learner)
    now = datetime.now(UTC)
    issued = await issue_pairing_token(
        pg_db, settings, learner_id=learner, canvas_id=canvas_id, now=now
    )
    assert issued.expires_at == now + timedelta(seconds=settings.pairing_ttl_s)
    row = await pg_db.fetch_one(
        "SELECT token_hash, session_id, canvas_id, used_at FROM core.pairing_tokens "
        "WHERE learner_id = %(learner_id)s",
        {"learner_id": learner},
    )
    assert row is not None and row["token_hash"] != issued.token
    assert (row["session_id"], row["canvas_id"], row["used_at"]) == (session_id, canvas_id, None)

    paired = await redeem_pairing_token(pg_db, settings, token=issued.token, now=now)
    assert paired is not None and (paired.learner_id, paired.canvas_id) == (learner, canvas_id)
    session = await resolve_session(pg_db, token=paired.token, now=now)
    assert session is not None and (session.scope, session.canvas_id) == ("companion", canvas_id)
    assert await redeem_pairing_token(pg_db, settings, token=issued.token, now=now) is None

    late = await issue_pairing_token(
        pg_db, settings, learner_id=learner, canvas_id=canvas_id, now=now
    )
    after = now + timedelta(seconds=settings.pairing_ttl_s)
    assert await redeem_pairing_token(pg_db, settings, token=late.token, now=after) is None
    assert await redeem_pairing_token(pg_db, settings, token="unknown", now=now) is None

    with pytest.raises(CanvasNotPairable):  # another learner's canvas
        await issue_pairing_token(
            pg_db, settings, learner_id=await create_learner(pg_db), canvas_id=canvas_id, now=now
        )


async def test_companion_scope(pg_db: Db, settings: Settings, client: httpx.AsyncClient) -> None:
    """T-CP1 over HTTP: the laptop asks for a token, the phone exchanges it once for a
    companion cookie; with it every other route is 403, the socket of its canvas opens and of
    another canvas does not."""
    learner = await create_learner(pg_db)
    _, canvas_id = await _canvas(pg_db, learner)
    _, other_canvas = await _canvas(pg_db, learner)
    laptop = _cookie(await open_session(pg_db, settings, learner_id=learner, now=datetime.now(UTC)))

    issued = await client.post(f"/v1/canvases/{canvas_id}/pairing", headers=laptop)
    assert issued.status_code == 201
    token = issued.json()["token"]
    assert (await client.post(f"/v1/canvases/{uuid4()}/pairing", headers=laptop)).status_code == 404
    assert (await client.post(f"/v1/canvases/{canvas_id}/pairing")).status_code == 401

    paired = await client.post("/companion/pair", json={"token": token})
    assert paired.status_code == 200 and paired.json() == {"canvas_id": str(canvas_id)}
    phone_token = paired.cookies[SESSION_COOKIE]
    again = await client.post("/companion/pair", json={"token": token})
    assert again.status_code == 401 and again.json()["error"]["code"] == "UNAUTHENTICATED"

    phone = _cookie(phone_token)
    for method, path in (
        ("GET", "/v1/me"),
        ("GET", f"/v1/canvases/{canvas_id}"),
        ("POST", f"/v1/canvases/{canvas_id}/pairing"),
        ("GET", f"/v1/domains/{uuid4()}/canvases"),
    ):
        answer = await client.request(method, path, headers=phone)
        assert answer.status_code == 403, (method, path, answer.text)
        assert answer.json()["error"]["code"] == "UNAUTHORIZED"
    assert (await client.get("/v1/me", headers=laptop)).status_code == 200

    app = client.app  # type: ignore[attr-defined]
    async with AsgiWebSocket(
        app, f"/v1/canvases/{canvas_id}/ws", cookies={SESSION_COOKIE: phone_token}
    ):
        pass  # the status of its canvas: the socket opens
    with pytest.raises(WebSocketClosed):
        async with AsgiWebSocket(
            app, f"/v1/canvases/{other_canvas}/ws", cookies={SESSION_COOKIE: phone_token}
        ):
            pass
