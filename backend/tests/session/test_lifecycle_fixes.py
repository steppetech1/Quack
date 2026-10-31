"""Lifecycle fixes: idempotent start checks, turns_count, guarded presence release (S-2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
from redis.asyncio import Redis
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import presence_key
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.jobs.queue import ClaimedJob
from quack.session import lifecycle
from tests.session.fixtures import SeededDomain
from tests.support.resources import build_job_context

pytest_plugins = ["tests.session.fixtures"]


async def _start(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    seed: SeededDomain,
    key: str,
    **body: Any,
) -> httpx.Response:
    payload = {"domain_id": str(seed.domain_id), "idempotency_key": key, **body}
    return await client.post("/v1/sessions", json=payload, headers=headers)


async def _finish(
    client: httpx.AsyncClient, headers: dict[str, str], session_id: UUID, key: str
) -> httpx.Response:
    return await client.post(
        f"/v1/sessions/{session_id}/finish", json={"idempotency_key": key}, headers=headers
    )


def _sweep_job() -> ClaimedJob:
    return ClaimedJob(
        id=uuid4(),
        kind="session.sweep_inactive",
        learner_id=None,
        lock_key="global:sweep_inactive",
        coalesce_key="global:sweep_inactive",
        priority=90,
        payload={},
        attempt=0,
        max_attempts=5,
    )


async def _make_stale(db: SessionFactory, session_id: UUID) -> None:
    async with db() as session, session.begin():
        await session.execute(
            text(
                "UPDATE core.sessions SET last_activity_at = now() - interval '2 hours' "
                "WHERE id = :id"
            ),
            {"id": session_id},
        )


async def test_repeated_start_with_other_parameters_conflicts(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
) -> None:
    seed = seeded_domain
    first = await _start(client, auth_headers, seed, "k-1", mode="learn", goal="g")
    assert first.status_code == 201, first.text

    same = await _start(client, auth_headers, seed, "k-1", mode="learn", goal="g")
    assert same.status_code == 201, same.text
    assert same.json()["session_id"] == first.json()["session_id"]

    for changed in ({"mode": "practice"}, {"goal": "other"}, {"topic_id": str(uuid4())}):
        body = {"mode": "learn", "goal": "g", **changed}
        resp = await _start(client, auth_headers, seed, "k-1", **body)
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    other_domain = await client.post(
        "/v1/sessions",
        json={"domain_id": str(uuid4()), "idempotency_key": "k-1", "mode": "learn", "goal": "g"},
        headers=auth_headers,
    )
    assert other_domain.status_code == 409, other_domain.text
    assert other_domain.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_repeated_start_of_finished_session_returns_real_status_without_presence(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    redis: Redis,
) -> None:
    seed = seeded_domain
    first = await _start(client, auth_headers, seed, "k-fin")
    assert first.status_code == 201, first.text
    session_id = UUID(first.json()["session_id"])
    assert first.json()["status"] == "active"
    fin = await _finish(client, auth_headers, session_id, "fin-1")
    assert fin.status_code == 200, fin.text
    assert await redis.get(presence_key(seed.learner_id)) is None

    again = await _start(client, auth_headers, seed, "k-fin")
    assert again.status_code == 201, again.text
    assert again.json()["session_id"] == str(session_id)
    assert again.json()["status"] == "completed"
    assert await redis.get(presence_key(seed.learner_id)) is None


async def test_finish_counts_turns_from_student_replies_only(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
) -> None:
    seed = seeded_domain
    started = await _start(client, auth_headers, seed, "k-turns")
    session_id = UUID(started.json()["session_id"])

    events: list[NewEvent] = []
    for i in range(3):
        base = {"session_id": str(session_id), "turn_id": str(uuid4())}
        events.append(
            NewEvent(
                type="turn.student_replied",
                payload={**base, "turn_index": 2 * i, "text": f"q{i}"},
                actor="learner",
                idempotency_key=f"s-{i}",
            )
        )
        events.append(
            NewEvent(
                type="turn.teacher_replied",
                payload={
                    **base,
                    "turn_index": 2 * i + 1,
                    "text": f"a{i}",
                    "pedagogical_move": "explain",
                },
                actor="teacher@1",
                idempotency_key=f"t-{i}",
            )
        )
    async with db() as session, session.begin():
        await append_events(session, seed.learner_id, events, publish_wakeup=False)

    fin = await _finish(client, auth_headers, session_id, "fin-turns")
    assert fin.status_code == 200, fin.text
    assert fin.json()["summary_stats"]["turns_count"] == 3


async def test_finish_keeps_presence_of_another_session(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    redis: Redis,
) -> None:
    seed = seeded_domain
    started = await _start(client, auth_headers, seed, "k-pres-fin")
    session_id = UUID(started.json()["session_id"])
    other = uuid4()
    await redis.set(presence_key(seed.learner_id), str(other))

    fin = await _finish(client, auth_headers, session_id, "fin-pres")
    assert fin.status_code == 200, fin.text
    assert await redis.get(presence_key(seed.learner_id)) == str(other)


async def test_finish_deletes_own_presence(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    redis: Redis,
) -> None:
    seed = seeded_domain
    started = await _start(client, auth_headers, seed, "k-pres-own")
    session_id = UUID(started.json()["session_id"])
    assert await redis.get(presence_key(seed.learner_id)) == str(session_id)

    fin = await _finish(client, auth_headers, session_id, "fin-own")
    assert fin.status_code == 200, fin.text
    assert await redis.get(presence_key(seed.learner_id)) is None


async def test_sweep_keeps_presence_of_another_session(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    redis: Redis,
    settings: Settings,
) -> None:
    seed = seeded_domain
    started = await _start(client, auth_headers, seed, "k-sweep-other")
    session_id = UUID(started.json()["session_id"])
    other = uuid4()
    await redis.set(presence_key(seed.learner_id), str(other))
    await _make_stale(db, session_id)

    await lifecycle.sweep_inactive_sessions(
        _sweep_job(), build_job_context(settings, db, None, None)
    )

    async with db() as session:
        status = (
            await session.execute(
                text("SELECT status FROM core.sessions WHERE id = :id"), {"id": session_id}
            )
        ).scalar_one()
    assert status == "abandoned"
    assert await redis.get(presence_key(seed.learner_id)) == str(other)


async def test_sweep_deletes_presence_of_swept_session(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    redis: Redis,
    settings: Settings,
) -> None:
    seed = seeded_domain
    started = await _start(client, auth_headers, seed, "k-sweep-own")
    session_id = UUID(started.json()["session_id"])
    assert await redis.get(presence_key(seed.learner_id)) == str(session_id)
    await _make_stale(db, session_id)

    await lifecycle.sweep_inactive_sessions(
        _sweep_job(), build_job_context(settings, db, None, None)
    )

    assert await redis.get(presence_key(seed.learner_id)) is None
