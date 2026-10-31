"""`GET /v1/learner/stream` — the learner change channel (ТЗ Фазы 3 WP7, `Т-§1.6`, T-H4)."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.app import create_app
from quack.api.auth import issue_access_token
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import projector_applied_channel
from quack.events import append_events
from quack.graph.worker import ProjectorWorker
from tests.factories import new_event
from tests.support.auth import ensure_user
from tests.support.broken import BrokenRedis
from tests.support.resources import build_app_resources
from tests.support.sse import KEEPALIVE, StreamingASGITransport, next_sse_block, next_sse_event


@pytest.fixture
def stream_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"learner_stream_poll_ms": 50, "sse_keepalive_s": 15.0})


async def _learner(db: SessionFactory, settings: Settings) -> tuple[UUID, dict[str, str]]:
    learner = uuid4()
    await ensure_user(db, learner)
    return learner, {"Authorization": f"Bearer {issue_access_token(learner, settings)}"}


async def _append(db: SessionFactory, learner: UUID, n: int) -> None:
    async with db() as session, session.begin():
        await append_events(
            session,
            learner,
            [new_event("learner.preferences_updated", {"preferences": {"i": i}}) for i in range(n)],
            publish_wakeup=False,
        )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=StreamingASGITransport(app), base_url="http://test")


async def _subscribers(redis: Redis, channel: str) -> int:
    return int(dict(await redis.pubsub_numsub(channel)).get(channel, 0))


async def _wait_subscribers(redis: Redis, channel: str, expected: int) -> None:
    async with asyncio.timeout(3.0):
        while True:
            if await _subscribers(redis, channel) == expected:
                return
            await asyncio.sleep(0.01)


async def test_learner_stream(
    stream_settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    """T-H4 (Redis): first `projection_advanced` with current `applied_seq` and `last_seq`;
    an event projected by the projector → `projection_advanced` with the new `applied_seq`;
    after the client disconnects there are no subscriptions on `projector:applied:{id}`."""
    learner, headers = await _learner(db, stream_settings)
    await _append(db, learner, 2)
    worker = ProjectorWorker(stream_settings, pg_engine, db, neo4j, redis)
    await worker.run_once()
    await _append(db, learner, 1)  # last_seq 3, applied 2

    resources = build_app_resources(stream_settings, pg_engine, neo4j, redis, session_factory=db)
    app = create_app(stream_settings, resources=resources)
    channel = projector_applied_channel(learner)
    async with (
        _client(app) as client,
        client.stream("GET", "/v1/learner/stream", headers=headers) as r,
    ):
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = r.aiter_lines()
        assert await next_sse_event(lines) == (
            "projection_advanced",
            {"applied_seq": 2, "last_seq": 3},
        )
        await _wait_subscribers(redis, channel, 1)
        await worker.run_once()
        assert await next_sse_event(lines) == ("projection_advanced", {"applied_seq": 3})
    await _wait_subscribers(redis, channel, 0)


async def test_learner_stream_polls_without_redis(
    stream_settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    """T-H4 (broken Redis): the same events arrive by polling `ops.projector_offsets`, and only
    when `applied_seq` grows."""
    learner, headers = await _learner(db, stream_settings)
    resources = build_app_resources(
        stream_settings, pg_engine, neo4j, BrokenRedis(), session_factory=db
    )
    app = create_app(stream_settings, resources=resources)
    worker = ProjectorWorker(stream_settings, pg_engine, db, neo4j, redis)
    async with (
        _client(app) as client,
        client.stream("GET", "/v1/learner/stream", headers=headers) as r,
    ):
        lines = r.aiter_lines()
        assert await next_sse_event(lines) == (
            "projection_advanced",
            {"applied_seq": 0, "last_seq": 0},
        )
        await _append(db, learner, 2)
        await worker.run_once()
        assert await next_sse_event(lines) == ("projection_advanced", {"applied_seq": 2})
        with pytest.raises(TimeoutError):  # no growth → no event
            await next_sse_event(lines, timeout_s=0.3)


async def test_learner_stream_keepalive(
    stream_settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    """WP6.3 for this stream: a silent channel sends `: ping` comments every `sse_keepalive_s`."""
    cfg = stream_settings.model_copy(update={"sse_keepalive_s": 0.2})
    learner, headers = await _learner(db, cfg)
    resources = build_app_resources(cfg, pg_engine, neo4j, redis, session_factory=db)
    async with (
        _client(create_app(cfg, resources=resources)) as client,
        client.stream("GET", "/v1/learner/stream", headers=headers) as r,
    ):
        lines = r.aiter_lines()
        assert (await next_sse_block(lines))[0] == "projection_advanced"
        assert await next_sse_block(lines) == KEEPALIVE
        assert await next_sse_block(lines) == KEEPALIVE
    del learner


async def test_learner_stream_requires_token(
    stream_settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    resources = build_app_resources(stream_settings, pg_engine, neo4j, redis, session_factory=db)
    async with _client(create_app(stream_settings, resources=resources)) as client:
        response = await client.get("/v1/learner/stream")
    assert response.status_code == 401
