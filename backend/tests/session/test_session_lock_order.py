"""Lock order of `finish_session` against background writes (deadlock found by stream C on the
e2e stand, D-B-028): `core.learner_seq` first, then the session row — the order of
`append_events` → relational projection."""

from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.db.postgres import SessionFactory
from tests.session.fixtures import SeededDomain

pytest_plugins = ["tests.session.fixtures"]


async def _waits_on_lock(engine: AsyncEngine, timeout_s: float = 5.0) -> None:
    """Until some backend waits on a lock (the finish request queued behind `learner_seq`)."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        async with engine.connect() as conn:
            waiting = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND datname = current_database()"
                )
            )
        if waiting:
            return
        assert asyncio.get_running_loop().time() < deadline, "finish never waited on a lock"
        await asyncio.sleep(0.02)


async def test_finish_session_takes_learner_seq_before_the_session_row(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    pg_engine: AsyncEngine,
) -> None:
    """A background write holds `core.learner_seq` and then updates the session row (as the
    projection of a turn event does). `finish_session` running meanwhile must not hold the
    session row: the write goes through, then finish completes."""
    start = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "lock-start",
        },
        headers=auth_headers,
    )
    assert start.status_code == 201, start.text
    session_id = UUID(start.json()["session_id"])
    learner = seeded_domain.learner_id

    async with pg_engine.connect() as background:
        tx = await background.begin()
        await background.execute(
            text("SELECT last_seq FROM core.learner_seq WHERE learner_id = :l FOR UPDATE"),
            {"l": learner},
        )
        finish = asyncio.create_task(
            client.post(
                f"/v1/sessions/{session_id}/finish",
                json={"idempotency_key": "lock-finish"},
                headers=auth_headers,
            )
        )
        await _waits_on_lock(pg_engine)
        await background.execute(text("SET LOCAL lock_timeout = '2s'"))
        await background.execute(
            text(
                "UPDATE core.sessions SET last_activity_at = now() "
                "WHERE id = :s AND learner_id = :l"
            ),
            {"s": session_id, "l": learner},
        )
        await tx.commit()

    response = await asyncio.wait_for(finish, timeout=10)
    assert response.status_code == 200, response.text
    async with db() as session:
        status = await session.scalar(
            text("SELECT status FROM core.sessions WHERE id = :s"), {"s": session_id}
        )
    assert status == "completed"
