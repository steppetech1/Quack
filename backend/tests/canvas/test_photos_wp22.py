"""WP22.2: the photo of the sheet (ТЗ Ф4 WP22.2; Т-§22.6; `T-CP1` — upload into its canvas)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from quack.api.app import create_app
from quack.api.auth import SESSION_COOKIE
from quack.auth.sessions import open_session
from quack.canvas.boards import open_canvas
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from tests.db.fixtures import create_learner
from tests.support.resources import build_app_resources
from tests.support.storage import InMemoryObjectStore

WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 sheet"
PNG = b"\x89PNG\r\n\x1a\nnot a webp"


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


async def _cookie(db: Db, settings: Settings, learner: UUID, **scope: object) -> dict[str, str]:
    token = await open_session(
        db,
        settings,
        learner_id=learner,
        now=datetime.now(UTC),
        **scope,  # type: ignore[arg-type]
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


@pytest.fixture
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
async def client(
    pg_db: Db, settings: Settings, store: InMemoryObjectStore
) -> AsyncIterator[httpx.AsyncClient]:
    resources = build_app_resources(settings, None, None, db=pg_db, storage=store)
    app = create_app(settings, resources=resources)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client


async def test_photo_goes_to_the_store_then_the_event(
    pg_db: Db, settings: Settings, client: httpx.AsyncClient, store: InMemoryObjectStore
) -> None:
    """WP22.2: the WebP of the sheet is stored as `photos/{photo_id}/original-{ulid}.webp` and
    `photo.uploaded {canvas_id, photo_id, object_key}` is written with the canvas session; not
    WebP by content — 415, nothing stored."""
    learner = await create_learner(pg_db)
    session_id, canvas_id = await _canvas(pg_db, learner)
    laptop = await _cookie(pg_db, settings, learner)
    path = f"/v1/canvases/{canvas_id}/photos"

    answer = await client.post(path, files={"file": ("x.png", WEBP, "image/png")}, headers=laptop)
    assert answer.status_code == 201, answer.text
    photo_id = answer.json()["photo_id"]
    (key,) = store.objects
    assert key.startswith(f"learners/{learner}/photos/{photo_id}/original-")
    assert key.endswith(".webp") and store.objects[key][:2] == (WEBP, "image/webp")
    rows = await pg_db.fetch_all(
        "SELECT payload, session_id FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'photo.uploaded'",
        {"learner_id": learner},
    )
    assert [(r["payload"], r["session_id"]) for r in rows] == [
        ({"canvas_id": str(canvas_id), "photo_id": photo_id, "object_key": key}, session_id)
    ]

    rejected = await client.post(
        path, files={"file": ("x.webp", PNG, "image/webp")}, headers=laptop
    )
    assert rejected.status_code == 415 and len(store.objects) == 1


async def test_who_may_upload(pg_db: Db, settings: Settings, client: httpx.AsyncClient) -> None:
    """T-CP1, WP22.1: the phone paired to this canvas uploads; a companion of another canvas —
    403; another learner — 404; no session — 401."""
    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    _, canvas_id = await _canvas(pg_db, learner)
    _, another = await _canvas(pg_db, learner)
    path = f"/v1/canvases/{canvas_id}/photos"
    files = {"file": ("sheet.webp", WEBP, "image/webp")}

    phone = await _cookie(pg_db, settings, learner, scope="companion", canvas_id=canvas_id)
    assert (await client.post(path, files=files, headers=phone)).status_code == 201
    stranger = await _cookie(pg_db, settings, learner, scope="companion", canvas_id=another)
    denied = await client.post(path, files=files, headers=stranger)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "UNAUTHORIZED"
    foreign = await _cookie(pg_db, settings, other)
    assert (await client.post(path, files=files, headers=foreign)).status_code == 404
    assert (await client.post(path, files=files)).status_code == 401
