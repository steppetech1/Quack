"""ТЗ Ф4 §8.9 T-TT1 (consent part), WP13.1, WP18.4: a session does not start without
`consent.given {kind: terms}` of the current `terms_version` — `403 CONSENT_REQUIRED`
(Т-§19.4). The gate is `quack.api.auth.require_terms` (stream C, D-C-112 п.3); the learner
signs in with the Phase 4 cookie session only (WP18.3, D-C-119)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.api.app import create_app
from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import ConsentGiven
from quack.events.log import NewEvent, append_events
from tests.session.helpers import Learner, make_learner
from tests.support.resources import build_app_resources

pytest_plugins = ["tests.session.fixtures"]

TERMS = "2026-11"


@pytest.fixture
def terms_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"terms_version": TERMS})


@pytest.fixture
async def gated(
    terms_settings: Settings,
    neo4j: AsyncDriver,
    redis: Redis,
    pg_db: Db,
) -> AsyncIterator[httpx.AsyncClient]:
    """The app with the consent gate in place (no override of `require_terms`)."""
    resources = build_app_resources(terms_settings, neo4j, redis, db=pg_db)
    app = create_app(terms_settings, resources=resources)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _consent(pg_db: Db, learner_id: UUID, version: str) -> None:
    await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="consent.given",
                payload=ConsentGiven(kind="terms", version=version),
                actor="learner",
                idempotency_key=f"consent:{uuid4()}",
            )
        ],
    )


def _start_body(who: Learner) -> dict[str, str]:
    return {"domain_id": str(who.domain_id), "intent": "learn", "idempotency_key": str(uuid4())}


async def test_session_start_needs_current_terms(
    gated: httpx.AsyncClient, pg_db: Db, terms_settings: Settings
) -> None:
    who = await make_learner(pg_db, terms_settings)

    refused = await gated.post("/v1/sessions", json=_start_body(who), cookies=who.cookies)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "CONSENT_REQUIRED"

    await _consent(pg_db, who.learner_id, "2025-01")  # an old version is not the current one
    stale = await gated.post("/v1/sessions", json=_start_body(who), cookies=who.cookies)
    assert stale.status_code == 403

    await _consent(pg_db, who.learner_id, TERMS)
    started = await gated.post("/v1/sessions", json=_start_body(who), cookies=who.cookies)
    assert started.status_code == 201, started.text


async def test_session_start_without_cookie_is_unauthenticated(
    gated: httpx.AsyncClient, pg_db: Db, terms_settings: Settings
) -> None:
    who = await make_learner(pg_db, terms_settings)
    response = await gated.post("/v1/sessions", json=_start_body(who))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
