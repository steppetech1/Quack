"""ТЗ Ф4 §4.8, WP7.4 (`Т-§13.8`): `GET /v1/sessions/{id}/jobs` — the plaque shows the visible
jobs of the learner's session in `queued`/`running`; another learner's or no session is a 404."""

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
from quack.jobs.queue_p4 import enqueue
from quack.jobs.rules_p4 import JobPlan
from quack.session.sessions import StartSession, start_session

pytest_plugins = ["tests.session.fixtures"]


@pytest.fixture
async def app(
    settings: Settings,
    neo4j: AsyncDriver,
    redis: Redis,
    pg_db: Db,
) -> FastAPI:
    from tests.support.resources import build_app_resources

    resources = build_app_resources(settings, neo4j, redis, db=pg_db)
    return create_app(settings, resources=resources)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _learner(pg_db: Db, settings: Settings) -> tuple[UUID, UUID, dict[str, str]]:
    learner_id, domain_id = uuid4(), uuid4()
    await pg_db.execute(
        "INSERT INTO core.learners (learner_id, email) VALUES (%(l)s, %(e)s)",
        {"l": learner_id, "e": f"{learner_id}@test.invalid"},
    )
    created = NewEvent(
        type="domain.created",
        payload={"domain_id": str(domain_id), "name": "Calculus"},
        actor="learner",
        idempotency_key=f"domain:{domain_id}",
    )
    await append_events(pg_db, learner_id, [created])
    started = await start_session(
        pg_db,
        learner_id=learner_id,
        body=StartSession(domain_id=domain_id, intent="learn", idempotency_key=uuid4()),
    )
    token = await open_session(pg_db, settings, learner_id=learner_id, now=datetime.now(UTC))
    return learner_id, started.session_id, {SESSION_COOKIE: token}


async def test_plaque_shows_visible_jobs_of_own_session(
    client: httpx.AsyncClient, pg_db: Db, settings: Settings
) -> None:
    learner, session, cookies = await _learner(pg_db, settings)
    _, other_session, other_cookies = await _learner(pg_db, settings)

    def plan(key: str, kind: str, *, visible: bool) -> JobPlan:
        return JobPlan(
            kind=kind,
            job_key=key,
            priority_class="P1",
            learner_id=learner,
            session_id=session,
            visible=visible,
        )

    await enqueue(pg_db, plan("viz", "agent.viz", visible=True))
    await enqueue(pg_db, plan("hid", "embedding.compute", visible=False))

    seen = await client.get(f"/v1/sessions/{session}/jobs", cookies=cookies)
    assert seen.status_code == 200, seen.text
    assert [(j["job_key"], j["kind"], j["status"]) for j in seen.json()["jobs"]] == [
        ("viz", "agent.viz", "queued")
    ]
    # Another learner's session and a session that is not there — the same 404.
    foreign = await client.get(f"/v1/sessions/{session}/jobs", cookies=other_cookies)
    assert foreign.status_code == 404
    missing = await client.get(f"/v1/sessions/{uuid4()}/jobs", cookies=cookies)
    assert missing.status_code == 404
    other = await client.get(f"/v1/sessions/{other_session}/jobs", cookies=cookies)
    assert other.status_code == 404


async def test_llm_access_and_judges_only_gate(
    client: httpx.AsyncClient, pg_db: Db, settings: Settings, app: FastAPI
) -> None:
    """D-B-211: `GET /v1/llm/access` tells the client the pool mode; in `judges_only` a learner
    without `meta_judge` gets 403 on an action, the preference lets them through."""
    from quack.llm.access import reset_pool_cache

    learner, session, cookies = await _learner(pg_db, settings)
    normal = await client.get("/v1/llm/access", cookies=cookies)
    assert normal.status_code == 200, normal.text
    assert normal.json()["blocked"] is False

    saved = getattr(app.state, "settings", None)
    app.state.settings = settings.model_copy(
        update={"llm_pool_budget_usd": 10.0, "llm_pool_cache_s": 0.0}
    )
    reset_pool_cache()
    try:
        tight = await client.get("/v1/llm/access", cookies=cookies)
        assert tight.json() == {
            "mode": "judges_only",
            "judge": False,
            "blocked": True,
            "preference_key": "meta_judge",
        }
        refused = await client.post(
            f"/v1/sessions/{session}/actions",
            json={"kind": "message", "text": "hi", "idempotency_key": str(uuid4())},
            cookies=cookies,
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["details"]["reason"] == "judges_only"
        await append_events(
            pg_db,
            learner,
            [
                NewEvent(
                    type="preference.set",
                    payload={"key": "meta_judge", "value": True},
                    actor="learner",
                    idempotency_key=f"preference:{uuid4()}",
                )
            ],
        )
        judge = await client.get("/v1/llm/access", cookies=cookies)
        assert judge.json()["blocked"] is False
    finally:
        app.state.settings = saved
        reset_pool_cache()
