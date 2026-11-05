"""A request without a session and SSE stream resilience (S-5.4, S-5.5).
Sign-in is a Phase 4 session (ТЗ Ф4 WP18.3, D-C-112): 401 is `UNAUTHENTICATED` (§4.5)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from redis.exceptions import RedisError

from quack.api.resources import AppResources
from quack.config import Settings
from quack.db import Db
from quack.session import stream
from tests.session.helpers import Learner, make_learner
from tests.support.auth import learner_headers

pytest_plugins = ["tests.session.fixtures"]


async def test_request_without_session_is_rejected(
    session_app: FastAPI, settings: Settings
) -> None:
    """ТЗ Ф4 WP18.3: without a session cookie — 401. A plain client is used: the `client`
    fixture signs marked requests in, the API itself never accepts the test mark."""
    headers = learner_headers(uuid4())
    transport = httpx.ASGITransport(app=session_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as plain:
        resp = await plain.get("/v1/sessions/active", headers=headers)
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


async def _start_session(client: httpx.AsyncClient, who: Learner) -> UUID:
    resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(who.domain_id),
            "intent": "learn",
            "idempotency_key": str(uuid4()),
        },
        cookies=who.cookies,
    )
    assert resp.status_code == 201, resp.text
    return UUID(resp.json()["session_id"])


async def test_events_stream_without_redis_is_empty_sse(
    session_app: FastAPI,
    client: httpx.AsyncClient,
    learner: Learner,
    pg_db: Db,
    settings: Settings,
) -> None:
    session_id = await _start_session(client, learner)
    res: AppResources = session_app.state.resources
    session_app.state.resources = replace(res, redis=None)
    resp = await client.get(f"/v1/sessions/{session_id}/events", cookies=learner.cookies)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    # ownership is still checked first
    foreign = await make_learner(pg_db, settings)
    resp = await client.get(f"/v1/sessions/{session_id}/events", cookies=foreign.cookies)
    assert resp.status_code == 404


def res_settings(app: FastAPI) -> Settings:
    return app.state.settings


class _BrokenPubSub:
    closed = False

    async def subscribe(self, *channels: Any) -> None:
        raise RedisError("boom")

    async def get_message(self, **kwargs: Any) -> Any:
        raise RedisError("boom")

    async def aclose(self) -> None:
        self.closed = True


class _BrokenRedis:
    def __init__(self) -> None:
        self.pubsub_obj = _BrokenPubSub()

    def pubsub(self) -> _BrokenPubSub:
        return self.pubsub_obj


async def test_relay_ends_cleanly_on_redis_error(session_app: FastAPI) -> None:
    res: AppResources = session_app.state.resources
    broken = _BrokenRedis()
    resources = replace(res, redis=broken)  # type: ignore[arg-type]
    items = [item async for item in stream._relay(resources, uuid4())]
    assert items == []
    assert broken.pubsub_obj.closed
