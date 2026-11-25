"""Deleting a session from the left panel (`DELETE /v1/sessions/{id}`, `session.deleted`): the
session leaves the list, the page, the stream, the actions and the summary; its events and its row
stay (the evidence and the model are not touched; the domain deletion finds the conversation by
the row); a repeat and a foreign session are `404`; the sweep and the jobs go on."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import httpx
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.deletion.domain import delete_domain
from quack.events.log import NewEvent, append_events
from quack.graph.projection.runner import project_learner
from quack.jobs.types import ClaimedJob
from quack.session.lifecycle import sweep_inactive_sessions
from quack.session.sessions import end_if_inactive
from tests.session.helpers import Learner, make_learner
from tests.support.deletions import FakeDeletionJournal
from tests.support.resources import build_job_context
from tests.support.storage import InMemoryObjectStore

pytest_plugins = ["tests.session.fixtures"]


async def _start(client: httpx.AsyncClient, who: Learner) -> str:
    body = {"domain_id": str(who.domain_id), "intent": "learn", "idempotency_key": str(uuid4())}
    response = await client.post("/v1/sessions", json=body, cookies=who.cookies)
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _message(who: Learner, session_id: str, key: str, *, domain: bool = True) -> NewEvent:
    return NewEvent(
        type="message.sent",
        payload={"text": "hello"},
        actor="learner",
        idempotency_key=key,
        session_id=UUID(session_id),
        domain_id=who.domain_id if domain else None,
    )


async def test_delete_removes_the_session_from_every_read_and_keeps_its_events(
    client: httpx.AsyncClient, learner: Learner, pg_db: Db, settings: Settings
) -> None:
    keep, gone = await _start(client, learner), await _start(client, learner)
    await append_events(pg_db, learner.learner_id, [_message(learner, gone, "m1")])
    stranger = await make_learner(pg_db, settings)
    foreign = await client.delete(f"/v1/sessions/{gone}", cookies=stranger.cookies)
    assert foreign.status_code == 404  # a foreign session is "not found"
    missing = await client.delete(f"/v1/sessions/{uuid4()}", cookies=learner.cookies)
    assert missing.status_code == 404

    done = await client.delete(f"/v1/sessions/{gone}", cookies=learner.cookies)
    assert done.status_code == 200, done.text
    assert isinstance(done.json()["seq"], int)

    assert (await client.get(f"/v1/sessions/{gone}", cookies=learner.cookies)).status_code == 404
    for path in ("jobs", "plan", "summary", "events"):
        answer = await client.get(f"/v1/sessions/{gone}/{path}", cookies=learner.cookies)
        assert answer.status_code == 404, (path, answer.text)
    beat = await client.post(f"/v1/sessions/{gone}/heartbeat", cookies=learner.cookies)
    assert beat.status_code == 404
    action = {"kind": "confidence", "target_seq": 1, "value": 3, "idempotency_key": str(uuid4())}
    posted = await client.post(f"/v1/sessions/{gone}/actions", json=action, cookies=learner.cookies)
    assert posted.status_code == 404, posted.text
    canvas = await client.post(f"/v1/sessions/{gone}/canvases", json={}, cookies=learner.cookies)
    assert canvas.status_code == 404, canvas.text  # the journal-keyed ownership check too
    listed = await client.get("/v1/sessions", cookies=learner.cookies)
    assert [s["session_id"] for s in listed.json()["sessions"]] == [keep]
    active = await client.get("/v1/sessions/active", cookies=learner.cookies)
    assert active.json()["session_id"] == keep
    again = await client.delete(f"/v1/sessions/{gone}", cookies=learner.cookies)
    assert again.status_code == 404  # deleted already
    assert (await client.get(f"/v1/sessions/{keep}", cookies=learner.cookies)).status_code == 200

    # the events and the row stay: nothing of the journal is erased
    rows = await pg_db.fetch_all(
        "SELECT type FROM core.events WHERE learner_id = %(l)s AND session_id = %(s)s ORDER BY seq",
        {"l": learner.learner_id, "s": UUID(gone)},
    )
    assert [r["type"] for r in rows] == ["session.started", "message.sent", "session.deleted"]
    row = await pg_db.fetch_one(
        "SELECT 1 AS found FROM content.sessions WHERE session_id = %(s)s", {"s": UUID(gone)}
    )
    assert row is not None


async def test_sweep_and_late_events_of_a_deleted_session_do_not_fail(
    client: httpx.AsyncClient,
    learner: Learner,
    pg_db: Db,
    neo4j: AsyncDriver,
    redis: Redis,
    settings: Settings,
) -> None:
    gone = await _start(client, learner)
    assert (await client.delete(f"/v1/sessions/{gone}", cookies=learner.cookies)).status_code == 200
    late = datetime.now(UTC) + timedelta(minutes=settings.session_inactivity_timeout_min + 1)
    ended = await end_if_inactive(
        pg_db, learner_id=learner.learner_id, session_id=UUID(gone), settings=settings, now=late
    )
    assert ended is False  # the session is gone for the sweep too
    await pg_db.execute(
        "UPDATE content.sessions SET last_activity_at = now() - interval '1 day' "
        "WHERE session_id = %(s)s",
        {"s": UUID(gone)},
    )
    ctx = build_job_context(settings, neo4j, redis, db=pg_db)
    await sweep_inactive_sessions(cast(ClaimedJob, None), ctx)
    count = await pg_db.fetch_one(
        "SELECT count(*) AS n FROM core.events WHERE session_id = %(s)s AND type = 'session.ended'",
        {"s": UUID(gone)},
    )
    assert count is not None and count["n"] == 0
    # a job or a turn of the session still writes to its journal: no read of the row is needed
    await append_events(pg_db, learner.learner_id, [_message(learner, gone, "late")])


async def test_domain_deletion_still_finds_the_conversation_of_a_deleted_session(
    client: httpx.AsyncClient,
    learner: Learner,
    pg_db: Db,
    neo4j: AsyncDriver,
    redis: Redis,
    settings: Settings,
) -> None:
    gone = await _start(client, learner)
    # no `domain_id` on the event: only the session row leads the domain deletion to it
    await append_events(pg_db, learner.learner_id, [_message(learner, gone, "m", domain=False)])
    await client.delete(f"/v1/sessions/{gone}", cookies=learner.cookies)
    while (await project_learner(pg_db, neo4j, settings, learner.learner_id)).status == "applied":
        pass
    done = await delete_domain(
        pg_db, neo4j, settings, FakeDeletionJournal(), InMemoryObjectStore(),
        learner.learner_id, learner.domain_id, datetime.now(UTC), redis=redis,
    )  # fmt: skip
    assert done is not None
    left = await pg_db.fetch_one(
        "SELECT count(*) AS n FROM core.events WHERE session_id = %(s)s", {"s": UUID(gone)}
    )
    assert left is not None and left["n"] == 0
