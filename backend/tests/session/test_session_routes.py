"""ТЗ Ф4 WP13.10 (D-B-210): the session routes over `Db` — start, list, active, feed, heartbeat,
end — and the sweep of inactive sessions. The library is checked in `test_sessions_db.py`; here
the HTTP codes, the isolation of learners and the presence in Redis."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import httpx
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.redis import presence_key
from quack.jobs.types import ClaimedJob
from quack.session.lifecycle import sweep_inactive_sessions
from tests.session.helpers import Learner, make_learner
from tests.support.resources import build_job_context

pytest_plugins = ["tests.session.fixtures"]


def _body(who: Learner, key: str, **extra: object) -> dict[str, object]:
    return {"domain_id": str(who.domain_id), "intent": "learn", "idempotency_key": key, **extra}


async def _start(client: httpx.AsyncClient, who: Learner, **extra: object) -> str:
    response = await client.post(
        "/v1/sessions", json=_body(who, str(uuid4()), **extra), cookies=who.cookies
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


async def test_start_is_idempotent_and_checks_the_domain(
    client: httpx.AsyncClient, learner: Learner, pg_db: Db, settings: Settings
) -> None:
    key = str(uuid4())
    first = await client.post("/v1/sessions", json=_body(learner, key), cookies=learner.cookies)
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "active"
    again = await client.post("/v1/sessions", json=_body(learner, key), cookies=learner.cookies)
    assert again.json()["session_id"] == first.json()["session_id"]
    other = await client.post(
        "/v1/sessions", json=_body(learner, key, intent="practice"), cookies=learner.cookies
    )
    assert other.status_code == 409
    assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    stranger = await make_learner(pg_db, settings)  # a domain of another learner is not found
    foreign = await client.post(
        "/v1/sessions", json=_body(stranger, str(uuid4())), cookies=learner.cookies
    )
    assert foreign.status_code == 404


async def test_list_active_and_feed_of_own_sessions(
    client: httpx.AsyncClient, learner: Learner, pg_db: Db, settings: Settings
) -> None:
    first, second = await _start(client, learner), await _start(client, learner)
    stranger = await make_learner(pg_db, settings)
    theirs = await _start(client, stranger)

    listed = await client.get("/v1/sessions", cookies=learner.cookies)
    assert [s["session_id"] for s in listed.json()["sessions"]] == [second, first]
    limited = await client.get("/v1/sessions?limit=1", cookies=learner.cookies)
    assert [s["session_id"] for s in limited.json()["sessions"]] == [second]
    assert (await client.get("/v1/sessions?limit=0", cookies=learner.cookies)).status_code == 422
    other_domain = await client.get(f"/v1/sessions?domain_id={uuid4()}", cookies=learner.cookies)
    assert other_domain.json()["sessions"] == []

    active = await client.get("/v1/sessions/active", cookies=learner.cookies)
    assert active.json()["session_id"] == second
    detail = await client.get(f"/v1/sessions/{first}", cookies=learner.cookies)
    assert [e["type"] for e in detail.json()["events"]] == ["session.started"]
    assert (await client.get(f"/v1/sessions/{theirs}", cookies=learner.cookies)).status_code == 404


async def test_heartbeat_sets_presence_and_end_releases_it(
    client: httpx.AsyncClient, learner: Learner, redis: Redis, settings: Settings
) -> None:
    session_id = await _start(client, learner)
    await redis.delete(presence_key(learner.learner_id))
    beat = await client.post(f"/v1/sessions/{session_id}/heartbeat", cookies=learner.cookies)
    assert beat.json() == {"status": "ok", "ttl_sec": settings.heartbeat_ttl_sec}
    assert await redis.get(presence_key(learner.learner_id)) == session_id

    ended = await client.post(f"/v1/sessions/{session_id}/end", cookies=learner.cookies)
    assert ended.json()["status"] == "ended"
    repeat = await client.post(f"/v1/sessions/{session_id}/end", cookies=learner.cookies)
    assert repeat.status_code == 200
    assert await redis.get(presence_key(learner.learner_id)) is None
    closed = await client.post(f"/v1/sessions/{session_id}/heartbeat", cookies=learner.cookies)
    assert closed.status_code == 404
    assert (await client.get("/v1/sessions/active", cookies=learner.cookies)).json() is None
    types = [
        e["type"]
        for e in (await client.get(f"/v1/sessions/{session_id}", cookies=learner.cookies)).json()[
            "events"
        ]
    ]
    assert types == ["session.started", "session.ended"]


async def test_sweep_ends_only_the_inactive_sessions(
    learner: Learner,
    client: httpx.AsyncClient,
    pg_db: Db,
    settings: Settings,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    stale, fresh = await _start(client, learner), await _start(client, learner)
    await pg_db.execute(
        "UPDATE content.sessions SET last_activity_at = now() - interval '1 day' "
        "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s",
        {"learner_id": learner.learner_id, "session_id": stale},
    )
    ctx = build_job_context(settings, neo4j, redis, db=pg_db)
    await sweep_inactive_sessions(cast(ClaimedJob, None), ctx)
    await sweep_inactive_sessions(cast(ClaimedJob, None), ctx)  # a repeat changes nothing

    statuses = {
        s["session_id"]: s["status"]
        for s in (await client.get("/v1/sessions", cookies=learner.cookies)).json()["sessions"]
    }
    assert statuses == {stale: "ended", fresh: "active"}
    ended = await pg_db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'session.ended'",
        {"learner_id": learner.learner_id},
    )
    assert [e["payload"]["reason"] for e in ended] == ["inactive"]
