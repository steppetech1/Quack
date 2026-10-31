"""Auth revocation by `auth.users.deleted_at` and SSE stream resilience (S-5.4, S-5.5)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from redis.exceptions import RedisError
from sqlalchemy import text

from quack.api.resources import AppResources
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.session import stream
from tests.session.helpers import make_token

if TYPE_CHECKING:
    from tests.session.fixtures import SeededDomain

pytest_plugins = ["tests.session.fixtures"]


async def test_deleted_auth_user_gets_401_without_tombstone(
    client: httpx.AsyncClient, db: SessionFactory, settings: Settings
) -> None:
    learner = uuid4()
    async with db() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO auth.users (id, email, password_hash, display_name, deleted_at) "
                "VALUES (:id, :email, 'x', 'Gone', now())"
            ),
            {"id": learner, "email": f"{learner}@example.com"},
        )
    headers = {"Authorization": f"Bearer {make_token(settings, learner)}"}
    resp = await client.get("/v1/sessions/active", headers=headers)
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


async def test_learner_without_auth_user_row_is_rejected(
    session_app: FastAPI, settings: Settings
) -> None:
    """ТЗ Фазы 3 WP1.2, D-C-004: a valid token of a learner without an `auth.users` row is
    401. A plain client is used: the `client` fixture creates user rows for its tokens."""
    headers = {"Authorization": f"Bearer {make_token(settings, uuid4())}"}
    transport = httpx.ASGITransport(app=session_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as plain:
        resp = await plain.get("/v1/sessions/active", headers=headers)
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


async def _start_session(
    client: httpx.AsyncClient, headers: dict[str, str], seeded: SeededDomain, key: str
) -> UUID:
    resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded.domain_id),
            "topic_id": str(seeded.topic_id),
            "idempotency_key": key,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return UUID(resp.json()["session_id"])


async def test_events_stream_without_redis_is_empty_sse(
    session_app: FastAPI,
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
) -> None:
    session_id = await _start_session(client, auth_headers, seeded_domain, "resil-no-redis")
    res: AppResources = session_app.state.resources
    session_app.state.resources = replace(res, redis=None)  # type: ignore[arg-type]
    resp = await client.get(f"/v1/sessions/{session_id}/events", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    # ownership is still checked first
    foreign = {"Authorization": f"Bearer {make_token(res_settings(session_app), uuid4())}"}
    resp = await client.get(f"/v1/sessions/{session_id}/events", headers=foreign)
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


async def test_relay_ends_cleanly_on_redis_error(
    session_app: FastAPI, seeded_domain: SeededDomain
) -> None:
    res: AppResources = session_app.state.resources
    broken = _BrokenRedis()
    resources = replace(res, redis=broken)  # type: ignore[arg-type]
    items = [item async for item in stream._relay(resources, uuid4())]
    assert items == []
    assert broken.pubsub_obj.closed
