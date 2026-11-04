"""Shared Neo4j error handler of the API (ТЗ Фазы 3 WP6.1, §4.5, seam S7; on the `Db` of Phase 4,
D-A-230): an endpoint that needs Neo4j for its answer returns `503 SERVICE_UNAVAILABLE`, never an
unhandled 500."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from neo4j.exceptions import ServiceUnavailable

from quack.api.app import create_app
from quack.api.errors import ErrorResponse
from quack.config import Settings
from quack.db import Db
from tests.db.fixtures import create_learner
from tests.support.auth import learner_headers, login_hook
from tests.support.broken import BrokenNeo4jDriver
from tests.support.resources import build_app_resources


@pytest.fixture
async def api(settings: Settings, pg_db: Db, redis: object) -> AsyncIterator[httpx.AsyncClient]:
    resources = build_app_resources(settings, BrokenNeo4jDriver(), redis, db=pg_db)
    app = create_app(settings, resources=resources)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    hooks = {"request": [login_hook(pg_db, settings)]}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", event_hooks=hooks
    ) as client:
        yield client


async def _learner_with_domain(api: httpx.AsyncClient, db: Db) -> tuple[dict[str, str], str]:
    headers = learner_headers(await create_learner(db))
    created = await api.post(
        "/v1/domains", json={"name": "Algebra", "idempotency_key": uuid4().hex}, headers=headers
    )
    assert created.status_code == 201, created.text
    return headers, created.json()["domain_id"]


def _assert_service_unavailable(response: httpx.Response, what: str) -> None:
    assert response.status_code == 503, f"{what}: {response.status_code} {response.text}"
    body = ErrorResponse.model_validate(response.json())
    assert body.error.code == "SERVICE_UNAVAILABLE", what


async def test_neo4j_outage_is_service_unavailable(api: httpx.AsyncClient, pg_db: Db) -> None:
    """Graph and topic card with Neo4j down → 503 (T-H1, the part of stream C)."""
    headers, domain_id = await _learner_with_domain(api, pg_db)
    graph = await api.get(f"/v1/domains/{domain_id}/graph", headers=headers)
    _assert_service_unavailable(graph, "graph")
    topic = await api.get(f"/v1/topics/{uuid4()}", headers=headers)
    _assert_service_unavailable(topic, "topic")


def test_neo4j_outage_errors_are_handled() -> None:
    """The handler covers the driver's outage errors, not query errors."""
    from quack.api.errors import NEO4J_OUTAGE_ERRORS

    assert ServiceUnavailable in NEO4J_OUTAGE_ERRORS
