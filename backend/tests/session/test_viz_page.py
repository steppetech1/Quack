"""WP16.5, `T-AG6` (the CSP part): `GET /v1/viz/{id}` returns the stored page with the CSP of
D-B-178; a failed build, an unknown id and another learner's id are the same 404."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.api.app import create_app
from quack.api.auth import SESSION_COOKIE
from quack.auth.sessions import open_session
from quack.config import Settings
from quack.db.db import Db
from quack.events.log import NewEvent, append_events
from quack.storage.keys import viz_key
from tests.db.fixtures import create_learner
from tests.support.resources import build_app_resources
from tests.support.storage import InMemoryObjectStore

pytest_plugins = ["tests.session.fixtures"]

PAGE = b"<!doctype html><script>document.title='x'</script>"


@pytest.fixture
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
async def client(
    settings: Settings,
    neo4j: AsyncDriver,
    redis: Redis,
    pg_db: Db,
    store: InMemoryObjectStore,
) -> AsyncIterator[httpx.AsyncClient]:
    resources = build_app_resources(settings, neo4j, redis, db=pg_db, storage=store)
    app: FastAPI = create_app(settings, resources=resources)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def _learner(pg_db: Db, settings: Settings) -> tuple[UUID, dict[str, str]]:
    learner_id = await create_learner(pg_db)
    token = await open_session(pg_db, settings, learner_id=learner_id, now=datetime.now(UTC))
    return learner_id, {SESSION_COOKIE: token}


async def _completed(pg_db: Db, learner_id: UUID, request_id: UUID, **fields: object) -> None:
    payload = {"request_id": str(request_id), "kind": "viz", **fields}
    await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="agent.completed",
                payload=payload,
                actor="agent:viz_builder",
                idempotency_key=f"agent.completed:{request_id}",
            )
        ],
    )


async def test_page_with_csp_and_404s(
    client: httpx.AsyncClient, pg_db: Db, settings: Settings, store: InMemoryObjectStore
) -> None:
    learner, cookies = await _learner(pg_db, settings)
    _, other_cookies = await _learner(pg_db, settings)
    good, failed = uuid4(), uuid4()
    key = viz_key(learner, good)
    await store.put(key, PAGE, "text/html")
    await _completed(pg_db, learner, good, ok=True, result_ref=key)
    await _completed(pg_db, learner, failed, ok=False, error="viz_check_failed")

    seen = await client.get(f"/v1/viz/{good}", cookies=cookies)
    assert seen.status_code == 200, seen.text
    assert seen.content == PAGE
    csp = seen.headers["content-security-policy"]
    assert "connect-src 'none'" in csp
    assert "script-src" in csp and "'unsafe-inline'" in csp
    assert "sandbox allow-scripts" in csp and "allow-same-origin" not in csp
    assert seen.headers["content-type"].startswith("text/html")

    for url, who in (
        (f"/v1/viz/{failed}", cookies),
        (f"/v1/viz/{uuid4()}", cookies),
        (f"/v1/viz/{good}", other_cookies),
    ):
        assert (await client.get(url, cookies=who)).status_code == 404
    assert (await client.get(f"/v1/viz/{good}")).status_code == 401
