"""Short topic routes `GET /v1/topics/{id}` and `PATCH /v1/topics/{id}/status` return the same
status and body as the long `/v1/domains/{domain_id}/topics/{id}` routes (Спец §9.2, ТЗ-П1 §2.2.3).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import httpx
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.postgres import SessionFactory
from tests.domains.test_diagnostic_and_graph_view import _auth_headers, _seed_linear_dag_domain


async def test_short_get_matches_long_get(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    learner_id, token = seeded_learner
    domain_id, (t1_id, _, _), _ = await _seed_linear_dag_domain(
        db, settings, pg_engine, neo4j, redis, learner_id
    )
    headers = _auth_headers(token)

    long_resp = await api_client.get(f"/v1/domains/{domain_id}/topics/{t1_id}", headers=headers)
    short_resp = await api_client.get(f"/v1/topics/{t1_id}", headers=headers)

    assert long_resp.status_code == 200
    assert short_resp.status_code == long_resp.status_code
    assert short_resp.json() == long_resp.json()


async def test_short_patch_matches_long_patch(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    learner_id, token = seeded_learner
    domain_id, (t1_id, _, _), _ = await _seed_linear_dag_domain(
        db, settings, pg_engine, neo4j, redis, learner_id
    )
    headers = _auth_headers(token)
    body = {"status": "skipped", "reason": "known", "idempotency_key": "alias:status"}

    long_resp = await api_client.patch(
        f"/v1/domains/{domain_id}/topics/{t1_id}/status", headers=headers, json=body
    )
    short_resp = await api_client.patch(f"/v1/topics/{t1_id}/status", headers=headers, json=body)

    assert long_resp.status_code == 200
    assert short_resp.status_code == long_resp.status_code
    # Same idempotency key -> the retry replays the same stored event.
    assert short_resp.json() == long_resp.json()


async def test_short_routes_404_for_unknown_topic(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
) -> None:
    _, token = seeded_learner
    headers = _auth_headers(token)
    topic_id = uuid4()

    get_resp = await api_client.get(f"/v1/topics/{topic_id}", headers=headers)
    patch_resp = await api_client.patch(
        f"/v1/topics/{topic_id}/status",
        headers=headers,
        json={"status": "skipped", "idempotency_key": "alias:unknown"},
    )

    assert get_resp.status_code == 404
    assert get_resp.json()["error"]["code"] == "NOT_FOUND"
    assert patch_resp.status_code == 404
    assert patch_resp.json()["error"]["code"] == "NOT_FOUND"


async def test_short_routes_404_for_foreign_topic(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    _, token = seeded_learner
    foreign_learner_id = uuid4()
    _, (foreign_topic_id, _, _), _ = await _seed_linear_dag_domain(
        db, settings, pg_engine, neo4j, redis, foreign_learner_id
    )
    headers = _auth_headers(token)

    get_resp = await api_client.get(f"/v1/topics/{foreign_topic_id}", headers=headers)
    patch_resp = await api_client.patch(
        f"/v1/topics/{foreign_topic_id}/status",
        headers=headers,
        json={"status": "skipped", "idempotency_key": "alias:foreign"},
    )

    assert get_resp.status_code == 404
    assert patch_resp.status_code == 404
