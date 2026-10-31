"""Session lifecycle over HTTP (ТЗ §2.2, §3, §5 `test_session_lifecycle.py`)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import orjson
from redis.asyncio import Redis
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import presence_key
from tests.session.fixtures import SeededDomain
from tests.session.helpers import make_token

pytest_plugins = ["tests.session.fixtures"]

STARTED_FIELDS = {"session_id", "domain_id", "topic_id", "mode", "status", "plan", "start_seq"}


async def _start(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    seed: SeededDomain,
    key: str,
    **body: Any,
) -> httpx.Response:
    payload = {"domain_id": str(seed.domain_id), "idempotency_key": key, **body}
    return await client.post("/v1/sessions", json=payload, headers=headers)


async def _events(db: SessionFactory, learner: UUID) -> list[dict[str, Any]]:
    async with db() as session:
        rows = await session.execute(
            text(
                "SELECT seq, type, session_id, payload FROM core.events "
                "WHERE learner_id = :l ORDER BY seq"
            ),
            {"l": learner},
        )
        return [dict(r._mapping) for r in rows]


async def _sessions(db: SessionFactory, learner: UUID) -> dict[UUID, dict[str, Any]]:
    async with db() as session:
        rows = await session.execute(
            text(
                "SELECT id, domain_id, topic_id, mode, status, plan, start_seq, end_seq "
                "FROM core.sessions WHERE learner_id = :l"
            ),
            {"l": learner},
        )
        return {r.id: dict(r._mapping) for r in rows}


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    p = event["payload"]
    return orjson.loads(p) if isinstance(p, str | bytes) else p


def _assert_plan_from_topic_skills(plan: Any, seed: SeededDomain) -> None:
    assert isinstance(plan, list)
    assert 3 <= len(plan) <= 5
    assert all(isinstance(step, dict) for step in plan)
    dumped = orjson.dumps(plan).decode()
    known = [str(s) for s in seed.skill_ids] + ["Power rule", "Chain rule"]
    assert any(k in dumped for k in known), "plan is not built from the topic skills"


async def test_start_writes_session_started_and_presence_and_heartbeat_extends_ttl(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    redis: Redis,
    settings: Settings,
) -> None:
    seed = seeded_domain
    learner = seed.learner_id

    resp = await _start(
        client, auth_headers, seed, "start-1", topic_id=str(seed.topic_id), goal="derivatives"
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == STARTED_FIELDS
    session_id = UUID(body["session_id"])
    assert body["domain_id"] == str(seed.domain_id)
    assert body["topic_id"] == str(seed.topic_id)
    assert body["mode"] == "learn"
    assert body["status"] == "active"
    _assert_plan_from_topic_skills(body["plan"], seed)

    # Exactly one `session.started`, appended after the seeded events; start_seq is its seq.
    events = await _events(db, learner)
    started = [e for e in events if e["type"] == "session.started"]
    assert len(started) == 1
    assert started[0]["session_id"] == session_id
    assert started[0]["seq"] == body["start_seq"] > seed.applied_seq
    assert not [e for e in events if e["type"] == "session.abandoned"]
    payload = _payload(started[0])
    assert payload["session_id"] == str(session_id)
    assert payload["domain_id"] == str(seed.domain_id)
    assert payload["topic_id"] == str(seed.topic_id)
    assert payload["mode"] == "learn"
    assert payload["goal"] == "derivatives"
    assert payload["plan"] == body["plan"]

    rows = await _sessions(db, learner)
    assert list(rows) == [session_id]
    assert rows[session_id]["status"] == "active"
    assert rows[session_id]["start_seq"] == body["start_seq"]

    # Presence key holds the session id with EX = heartbeat_ttl_sec.
    key = presence_key(learner)
    assert await redis.get(key) == str(session_id)
    assert 0 < await redis.ttl(key) <= settings.heartbeat_ttl_sec

    # Idempotent repeat with the same key: same session, nothing new written.
    again = await _start(
        client, auth_headers, seed, "start-1", topic_id=str(seed.topic_id), goal="derivatives"
    )
    assert again.status_code in (200, 201), again.text
    assert again.json()["session_id"] == str(session_id)
    assert again.json()["start_seq"] == body["start_seq"]
    assert len(await _events(db, learner)) == len(events)
    assert list(await _sessions(db, learner)) == [session_id]

    # GET /active and GET /{id} read core.sessions.
    active = await client.get("/v1/sessions/active", headers=auth_headers)
    assert active.status_code == 200, active.text
    assert active.json()["session_id"] == str(session_id)
    assert active.json()["status"] == "active"
    assert active.json()["domain_id"] == str(seed.domain_id)
    detail = await client.get(f"/v1/sessions/{session_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["session_id"] == str(session_id)
    assert detail.json()["status"] == "active"
    assert detail.json()["turns"] == []

    # Heartbeat: presence TTL is extended and last_activity_at moves to now().
    await redis.expire(key, 5)
    async with db() as session, session.begin():
        await session.execute(
            text(
                "UPDATE core.sessions SET last_activity_at = now() - interval '10 minutes' "
                "WHERE id = :id"
            ),
            {"id": session_id},
        )
    hb = await client.post(f"/v1/sessions/{session_id}/heartbeat", headers=auth_headers)
    assert hb.status_code == 200, hb.text
    assert hb.json() == {"status": "ok", "ttl_sec": settings.heartbeat_ttl_sec}
    assert await redis.get(key) == str(session_id)
    assert 5 < await redis.ttl(key) <= settings.heartbeat_ttl_sec
    async with db() as session:
        fresh = (
            await session.execute(
                text(
                    "SELECT last_activity_at > now() - interval '1 minute' "
                    "FROM core.sessions WHERE id = :id"
                ),
                {"id": session_id},
            )
        ).scalar_one()
    assert fresh is True

    # The heartbeat restores an expired presence key too.
    await redis.delete(key)
    hb = await client.post(f"/v1/sessions/{session_id}/heartbeat", headers=auth_headers)
    assert hb.status_code == 200, hb.text
    assert await redis.get(key) == str(session_id)
    assert 0 < await redis.ttl(key) <= settings.heartbeat_ttl_sec


async def test_second_start_with_new_key_abandons_old_session(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    redis: Redis,
) -> None:
    seed = seeded_domain
    learner = seed.learner_id

    first = await _start(client, auth_headers, seed, "start-a", topic_id=str(seed.topic_id))
    assert first.status_code == 201, first.text
    old_id = UUID(first.json()["session_id"])

    # No topic_id: the default plan comes from the first available topic of the domain.
    second = await _start(client, auth_headers, seed, "start-b", mode="practice")
    assert second.status_code == 201, second.text
    body = second.json()
    assert set(body) == STARTED_FIELDS
    new_id = UUID(body["session_id"])
    assert new_id != old_id
    assert body["status"] == "active"
    assert body["mode"] == "practice"
    assert body["topic_id"] == str(seed.topic_id)
    _assert_plan_from_topic_skills(body["plan"], seed)

    # `session.abandoned` of the old session directly precedes `session.started` of the new.
    events = await _events(db, learner)
    abandoned = [e for e in events if e["type"] == "session.abandoned"]
    assert len(abandoned) == 1
    assert abandoned[0]["session_id"] == old_id
    assert _payload(abandoned[0]) == {
        "session_id": str(old_id),
        "domain_id": str(seed.domain_id),
        "reason": "superseded_by_new_session",
    }
    started = [e for e in events if e["type"] == "session.started"]
    assert [e["session_id"] for e in started] == [old_id, new_id]
    assert started[1]["seq"] == body["start_seq"]
    assert abandoned[0]["seq"] == body["start_seq"] - 1
    assert events[-1]["seq"] == body["start_seq"]

    # Exactly one active row; the old one is abandoned at the seq of its abandon event.
    rows = await _sessions(db, learner)
    assert set(rows) == {old_id, new_id}
    assert [sid for sid, r in rows.items() if r["status"] == "active"] == [new_id]
    assert rows[old_id]["status"] == "abandoned"
    assert rows[old_id]["end_seq"] == abandoned[0]["seq"]
    assert rows[new_id]["start_seq"] == body["start_seq"]

    assert await redis.get(presence_key(learner)) == str(new_id)
    active = await client.get("/v1/sessions/active", headers=auth_headers)
    assert active.status_code == 200, active.text
    assert active.json()["session_id"] == str(new_id)

    # The superseded session no longer accepts heartbeats.
    hb = await client.post(f"/v1/sessions/{old_id}/heartbeat", headers=auth_headers)
    assert hb.status_code == 404, hb.text
    assert hb.json()["error"]["code"] == "NOT_FOUND"
    assert await redis.get(presence_key(learner)) == str(new_id)

    # Replaying the second start key returns the same session and writes nothing.
    replay = await _start(client, auth_headers, seed, "start-b", mode="practice")
    assert replay.json()["session_id"] == str(new_id)
    assert len(await _events(db, learner)) == len(events)


async def test_foreign_or_unknown_session_is_not_found(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    redis: Redis,
    settings: Settings,
) -> None:
    seed = seeded_domain
    none_yet = await client.get("/v1/sessions/active", headers=auth_headers)
    assert none_yet.status_code == 200, none_yet.text
    assert none_yet.json() is None

    resp = await _start(client, auth_headers, seed, "start-own", topic_id=str(seed.topic_id))
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["session_id"]

    stranger = uuid4()
    stranger_headers = {"Authorization": f"Bearer {make_token(settings, stranger)}"}
    for headers, sid in ((stranger_headers, session_id), (auth_headers, str(uuid4()))):
        got = await client.get(f"/v1/sessions/{sid}", headers=headers)
        assert got.status_code == 404, got.text
        assert got.json()["error"]["code"] == "NOT_FOUND"

    for headers, sid in ((stranger_headers, session_id), (auth_headers, str(uuid4()))):
        hb = await client.post(f"/v1/sessions/{sid}/heartbeat", headers=headers)
        assert hb.status_code == 404, hb.text
        assert hb.json()["error"]["code"] == "NOT_FOUND"
    assert await redis.get(presence_key(stranger)) is None

    stranger_active = await client.get("/v1/sessions/active", headers=stranger_headers)
    assert stranger_active.status_code == 200, stranger_active.text
    assert stranger_active.json() is None
