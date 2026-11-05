"""Routes of stream A on `Db` (ТЗ Ф4 WP1.3, §6.4; D-A-207): domains, preferences, the map of a
domain with the codes of `TopicDisplay` (T-TH5), the card of a topic, `DELETE /v1/learner`."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.redis import presence_key
from quack.events.log import NewEvent, append_events
from quack.graph.projection.runner import project_learner
from quack.llm.tracing import learner_pseudonym
from tests.db.fixtures import create_learner
from tests.support.auth import session_cookie, session_token
from tests.support.tracer import RecordingTracer


async def _events(db: Db, learner_id: UUID, event_type: str) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT seq, actor, idempotency_key, domain_id, payload FROM core.events "
        "WHERE learner_id = %(l)s AND type = %(t)s ORDER BY seq",
        {"l": learner_id, "t": event_type},
    )


async def test_create_and_list_domains(
    api_client: httpx.AsyncClient, pg_db: Db, seeded_learner: tuple[UUID, str], settings: Settings
) -> None:
    """`POST /v1/domains` writes one `domain.created`; a retry gives the same domain and no new
    event; the same key with another name is 409; the list is the learner's own (law 1)."""
    learner_id, token = seeded_learner
    headers = session_cookie(token)
    body = {"name": "Physics", "idempotency_key": "d-1"}

    first = await api_client.post("/v1/domains", headers=headers, json=body)
    assert first.status_code == 201, first.text
    again = await api_client.post("/v1/domains", headers=headers, json=body)
    assert again.status_code == 201 and again.json() == first.json()
    other_name = await api_client.post(
        "/v1/domains", headers=headers, json={**body, "name": "Chemistry"}
    )
    assert other_name.status_code == 409
    assert other_name.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    [event] = await _events(pg_db, learner_id, "domain.created")
    domain_id = first.json()["domain_id"]
    assert event["payload"] == {"domain_id": domain_id, "name": "Physics"}
    assert (event["actor"], str(event["domain_id"])) == ("learner", domain_id)

    listed = await api_client.get("/v1/domains", headers=headers)
    assert [(d["domain_id"], d["name"]) for d in listed.json()["domains"]] == [
        (domain_id, "Physics")
    ]
    stranger = await session_token(pg_db, await create_learner(pg_db), settings)
    assert (await api_client.get("/v1/domains", headers=session_cookie(stranger))).json() == {
        "domains": []
    }


async def test_set_preference(
    api_client: httpx.AsyncClient, pg_db: Db, seeded_learner: tuple[UUID, str]
) -> None:
    """`PATCH /v1/learner/preferences {key, value}` is one `preference.set`; a retry returns the
    same event, the same key with another value is 409, a key that is no name is 422."""
    learner_id, token = seeded_learner
    headers = session_cookie(token)
    body = {"key": "language", "value": "de", "idempotency_key": "p-1"}

    first = await api_client.patch("/v1/learner/preferences", headers=headers, json=body)
    assert first.status_code == 200, first.text
    assert first.json() == {"key": "language", "value": "de", "seq": 1}
    again = await api_client.patch("/v1/learner/preferences", headers=headers, json=body)
    assert again.json() == first.json()
    changed = await api_client.patch(
        "/v1/learner/preferences", headers=headers, json={**body, "value": "fr"}
    )
    assert changed.status_code == 409
    bad = await api_client.patch(
        "/v1/learner/preferences", headers=headers, json={**body, "key": "Bad Key"}
    )
    assert bad.status_code == 422

    [event] = await _events(pg_db, learner_id, "preference.set")
    assert event["payload"] == {"key": "language", "value": "de"}


def _structure(domain: UUID, topic: UUID, skill: UUID) -> list[NewEvent]:
    d = str(domain)
    return [
        NewEvent(
            type="domain.created",
            payload={"domain_id": d, "name": "Algebra"},
            actor="learner",
            idempotency_key="d",
            domain_id=domain,
        ),
        NewEvent(
            type="topic.created",
            payload={
                "topic_id": str(topic),
                "domain_id": d,
                "name": "Fractions",
                "origin": "session",
            },
            actor="code",
            idempotency_key="t",
            domain_id=domain,
        ),
        NewEvent(
            type="skill.created",
            payload={
                "skill_id": str(skill),
                "topic_id": str(topic),
                "domain_id": d,
                "name": "add fractions",
                "summary": "s",
                "status": "active",
                "origin": "session",
            },
            actor="code",
            idempotency_key="s",
            domain_id=domain,
        ),
    ]


async def test_domain_graph_and_topic_card_give_codes(
    api_client: httpx.AsyncClient,
    domains_app: FastAPI,
    pg_db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    seeded_learner: tuple[UUID, str],
) -> None:
    """T-TH5: the map of a domain gives the state of a topic as a code with its parameters, the
    OpenAPI has `TopicDisplay`; a domain or a topic of another learner is 404."""
    learner_id, token = seeded_learner
    headers = session_cookie(token)
    domain, topic, skill = uuid4(), uuid4(), uuid4()
    await append_events(pg_db, learner_id, _structure(domain, topic, skill))
    await project_learner(pg_db, neo4j, settings, learner_id)

    graph = await api_client.get(f"/v1/domains/{domain}/graph", headers=headers)
    assert graph.status_code == 200, graph.text
    body = graph.json()
    assert (body["projected_seq"], body["lag_degraded"], body["requires"]) == (3, False, [])
    [node] = body["topics"]
    assert (node["topic_id"], node["name"], node["skill_count"]) == (str(topic), "Fractions", 1)
    assert node["display"] == {
        "display_state": "not_tested",
        "display_reason": "",
        "display_level": 0,
        "display_domain_id": "",
    }
    assert "TopicDisplay" in domains_app.openapi()["components"]["schemas"]

    card = await api_client.get(f"/v1/topics/{topic}", headers=headers)
    assert card.status_code == 200 and card.json()["display"] == node["display"]

    stranger = session_cookie(await session_token(pg_db, await create_learner(pg_db), settings))
    for url in (f"/v1/domains/{domain}/graph", f"/v1/topics/{topic}"):
        response = await api_client.get(url, headers=stranger)
        assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


async def test_delete_learner_removes_the_account(
    api_client: httpx.AsyncClient,
    domains_app: FastAPI,
    pg_db: Db,
    neo4j: AsyncDriver,
    redis: Redis,
    seeded_learner: tuple[UUID, str],
    settings: Settings,
    tracer: RecordingTracer,
) -> None:
    """T-E10 (WP25 п.3, `Т-§19.5`, D-A-205…207): `DELETE /v1/learner` is `delete_account` — at
    once, not by the scheduler: the journal record first, the rows of Phase 4, the subgraph and
    the presence key go, the log row stays, the traces are asked for deletion by the pseudonym,
    the answer names the term of the copies, the session is dead; another learner is untouched."""
    learner_id, token = seeded_learner
    other = await create_learner(pg_db)
    headers = session_cookie(token)
    for who in (learner_id, other):
        await append_events(pg_db, who, _structure(uuid4(), uuid4(), uuid4()))
        await project_learner(pg_db, neo4j, settings, who)
    await redis.set(presence_key(learner_id), "1")

    deleted = await api_client.delete("/v1/learner", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["copies_days"] == 15 and UUID(deleted.json()["deletion_id"])
    assert tracer.deleted == [learner_pseudonym(settings.langsmith_pseudonym_ns, learner_id)]
    journal = domains_app.state.resources.deletions
    assert [r.kind for r in journal.records] == ["account"]
    left = await pg_db.fetch_one(
        "SELECT (SELECT count(*) FROM core.learners WHERE learner_id = %(l)s) AS learners, "
        "(SELECT count(*) FROM core.events WHERE learner_id = %(l)s) AS events, "
        "(SELECT count(*) FROM ops.deletion_log WHERE learner_id = %(l)s) AS logged",
        {"l": learner_id},
    )
    assert left == {"learners": 0, "events": 0, "logged": 1}
    counts = {}
    for who in (learner_id, other):
        async with neo4j.session() as session:
            result = await session.run(
                "MATCH (n) WHERE n.learnerId = $l RETURN count(n) AS n", l=str(who)
            )
            record = await result.single()
        assert record is not None
        counts[who] = record["n"]
    assert counts[learner_id] == 0 and counts[other] > 0
    assert await redis.get(presence_key(learner_id)) is None
    after = await api_client.get("/v1/domains", headers=headers)
    assert after.status_code == 401
