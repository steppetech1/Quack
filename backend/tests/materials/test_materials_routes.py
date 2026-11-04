"""Routes of WP20.1 and WP20.5 on Phase 4 sessions (ТЗ Ф4 §6.4 «Материалы и поиск», §4.5,
WP18.3): `POST/GET /v1/domains/{id}/materials`, `GET /v1/materials/{id}`, `GET /v1/search`.
Integration tests on the Phase 4 database (`pg_db`)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from quack.api.app import create_app
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.storage.objects import ObjectStore
from tests.db.fixtures import create_learner
from tests.support.auth import login
from tests.support.resources import build_app_resources
from tests.support.storage import BrokenObjectStore, InMemoryObjectStore

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"
EXE = b"MZ\x90\x00\x03\x00\x00\x00"

type ClientFor = Callable[..., Awaitable[httpx.AsyncClient]]


@pytest.fixture
async def client_for(pg_db: Db, settings: Settings) -> AsyncIterator[ClientFor]:
    """A client of an app over `pg_db`, signed in as `learner_id` (or anonymous)."""
    async with AsyncExitStack() as stack:

        async def make(
            learner_id: UUID | None, storage: ObjectStore | None = None
        ) -> httpx.AsyncClient:
            resources = build_app_resources(
                settings, None, None, db=pg_db, storage=storage or InMemoryObjectStore()
            )
            app = create_app(settings, resources=resources)
            client = await stack.enter_async_context(
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
            )
            if learner_id is not None:
                await login(client, pg_db, learner_id, settings)
            return client

        yield make


async def _event(db: Db, learner: UUID, type_: str, payload: dict[str, Any], **kw: Any) -> None:
    event = NewEvent(
        type=type_, payload=payload, actor="learner", idempotency_key=str(uuid4()), **kw
    )
    await append_events(db, learner, [event])


async def _domain(db: Db, learner: UUID) -> UUID:
    domain_id = uuid4()
    await _event(db, learner, "domain.created", {"domain_id": str(domain_id), "name": "Calculus"})
    return domain_id


def _pdf(data: bytes = PDF) -> dict[str, Any]:
    return {"file": ("notes.pdf", data, "application/octet-stream")}


async def test_upload_list_and_read(pg_db: Db, client_for: ClientFor, settings: Settings) -> None:
    """WP20.1: multipart upload with `session_id` → 201 and `material.uploaded`; the list of
    the domain and the material with a signed link."""
    learner = await create_learner(pg_db)
    domain = await _domain(pg_db, learner)
    storage = InMemoryObjectStore()
    client = await client_for(learner, storage)
    session_id = uuid4()

    uploaded = await client.post(
        f"/v1/domains/{domain}/materials", files=_pdf(), data={"session_id": str(session_id)}
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    material_id = UUID(body["material_id"])
    assert body["domain_id"] == str(domain) and body["session_id"] == str(session_id)
    assert body["mime"] == "application/pdf" and body["status"] == "uploaded"
    key = f"learners/{learner}/materials/{material_id}/original.pdf"
    assert storage.objects[key][0] == PDF
    event = await pg_db.fetch_one(
        "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'material.uploaded'",
        {"learner_id": learner},
    )
    assert event is not None and event["payload"]["session_id"] == str(session_id)

    listed = await client.get(f"/v1/domains/{domain}/materials")
    assert listed.status_code == 200
    assert [m["material_id"] for m in listed.json()["materials"]] == [str(material_id)]

    detail = await client.get(f"/v1/materials/{material_id}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["material"]["material_id"] == str(material_id) and data["pages"] == []
    assert key in data["file_url"] and f"ttl={settings.object_url_ttl_s}" in data["file_url"]


async def test_upload_errors(pg_db: Db, client_for: ClientFor) -> None:
    """§4.5: not PDF or an image by content — 415 `UPLOAD_REJECTED`; unknown domain — 404;
    storage down — 503 `SERVICE_UNAVAILABLE`, nothing written."""
    learner = await create_learner(pg_db)
    domain = await _domain(pg_db, learner)
    client = await client_for(learner)

    bad = await client.post(f"/v1/domains/{domain}/materials", files=_pdf(EXE))
    assert bad.status_code == 415 and bad.json()["error"]["code"] == "UPLOAD_REJECTED"
    unknown = await client.post(f"/v1/domains/{uuid4()}/materials", files=_pdf())
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "NOT_FOUND"
    down = await (await client_for(learner, BrokenObjectStore())).post(
        f"/v1/domains/{domain}/materials", files=_pdf()
    )
    assert down.status_code == 503 and down.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    rows = await pg_db.fetch_all(
        "SELECT 1 AS found FROM content.materials WHERE learner_id = %(learner_id)s",
        {"learner_id": learner},
    )
    assert rows == []


async def test_isolation_and_no_session(pg_db: Db, client_for: ClientFor) -> None:
    """WP18.3, T-E3: another learner with the ids of the first gets 404 on every route with an
    id; without a session every route is 401 `UNAUTHENTICATED`."""
    owner, other = await create_learner(pg_db), await create_learner(pg_db)
    domain = await _domain(pg_db, owner)
    client = await client_for(owner)
    uploaded = await client.post(f"/v1/domains/{domain}/materials", files=_pdf())
    material_id = uploaded.json()["material_id"]

    stranger = await client_for(other)
    for response in (
        await stranger.post(f"/v1/domains/{domain}/materials", files=_pdf()),
        await stranger.get(f"/v1/domains/{domain}/materials"),
        await stranger.get(f"/v1/materials/{material_id}"),
    ):
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "NOT_FOUND"

    anonymous = await client_for(None)
    for response in (
        await anonymous.post(f"/v1/domains/{domain}/materials", files=_pdf()),
        await anonymous.get(f"/v1/domains/{domain}/materials"),
        await anonymous.get(f"/v1/materials/{material_id}"),
        await anonymous.get("/v1/search", params={"q": "integral"}),
    ):
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_search(pg_db: Db, client_for: ClientFor, settings: Settings) -> None:
    """WP20.5: session, `seq`, snippet and type of the learner's events; another learner
    finds nothing; the query length and `limit` are checked like the Phase 3 search
    (`search_min_query_len`, `search_max_limit`)."""
    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    session_id = uuid4()
    await _event(
        pg_db,
        learner,
        "message.sent",
        {"text": "How do I solve this integral by parts?"},
        session_id=session_id,
    )
    client = await client_for(learner)

    found = await client.get("/v1/search", params={"q": "integrals"})
    assert found.status_code == 200, found.text
    [hit] = found.json()["hits"]
    assert hit["session_id"] == str(session_id) and hit["type"] == "message.sent"
    assert isinstance(hit["seq"], int) and "integral" in hit["snippet"]

    nothing = await (await client_for(other)).get("/v1/search", params={"q": "integral"})
    assert nothing.status_code == 200 and nothing.json()["hits"] == []

    short = await client.get("/v1/search", params={"q": "i" * (settings.search_min_query_len - 1)})
    assert short.status_code == 422 and short.json()["error"]["code"] == "VALIDATION_ERROR"
    too_many = await client.get(
        "/v1/search", params={"q": "integral", "limit": settings.search_max_limit + 1}
    )
    assert too_many.status_code == 422
