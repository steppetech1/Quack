"""Deleting a chapter from the left panel (`DELETE /v1/hubs/{id}`, `hub.deleted`): the hub and its
edges leave the graph, the topics stay «without a chapter», every read skips the hub, a rebuild
from the journal gives the same graph."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID, uuid4

import httpx
from neo4j import AsyncDriver

from quack.artifacts.documents import ArtifactNotFound, get_artifact
from quack.config import Settings
from quack.db import Db
from quack.db.neo4j import literal
from quack.events.log import NewEvent, append_events
from quack.graph.ids import deterministic_id
from quack.graph.projection.rebuild import rebuild_learner
from quack.graph.projection.runner import project_learner
from quack.graph.read.cheatsheet_offer import cheatsheet_offer
from quack.session.prep_mode import prep_enabled
from tests.db.fixtures import create_learner
from tests.support.auth import session_cookie, session_token

_HUBS = (
    "MATCH (h:Hub {learnerId: $l}) OPTIONAL MATCH (h)-[i:INCLUDES]->(t:Topic) "
    "RETURN h.id AS hub, h.kind AS kind, collect(t.id) AS topics"
)
_TOPICS = "MATCH (d:Domain {learnerId: $l})-[:HAS_TOPIC]->(t:Topic) RETURN t.id AS id ORDER BY t.id"
_ALL = "MATCH (n {learnerId: $l}) RETURN labels(n)[0] AS label, n.id AS id, properties(n) AS props"
_RELS = "MATCH (a {learnerId: $l})-[r]->(b) RETURN a.id AS a, type(r) AS t, b.id AS b"


def _new(event_type: str, payload: dict[str, Any], key: str, domain: UUID) -> NewEvent:
    return NewEvent(
        type=event_type, payload=payload, actor="code", idempotency_key=key, domain_id=domain
    )


async def _project(db: Db, neo4j: AsyncDriver, settings: Settings, learner: UUID) -> None:
    while (await project_learner(db, neo4j, settings, learner)).status == "applied":
        pass


async def _rows(neo4j: AsyncDriver, query: str, learner: UUID) -> list[dict[str, Any]]:
    async with neo4j.session() as session:
        result = await session.run(literal(query), l=str(learner))
        return [dict(r) async for r in result]


async def _snapshot(neo4j: AsyncDriver, learner: UUID) -> tuple[list[str], list[str]]:
    nodes = [
        f"{r['label']}:{r['id']}:{sorted(r['props'].items())!r}"
        for r in await _rows(neo4j, _ALL, learner)
    ]
    rels = [f"{r['a']}-{r['t']}-{r['b']}" for r in await _rows(neo4j, _RELS, learner)]
    return sorted(nodes), sorted(rels)


async def test_delete_hub_keeps_the_topics_and_every_read_skips_it(
    api_client: httpx.AsyncClient,
    pg_db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    seeded_learner: tuple[UUID, str],
) -> None:
    learner, token = seeded_learner
    cookie = session_cookie(token)
    domain, topic, topic_b, hub = uuid4(), uuid4(), uuid4(), uuid4()
    d = str(domain)
    stored = await append_events(
        pg_db,
        learner,
        [
            _new("domain.created", {"domain_id": d, "name": "Algebra"}, "d", domain),
            _new("topic.created", {"topic_id": str(topic), "domain_id": d, "name": "A",
                 "origin": "session"}, "ta", domain),
            _new("topic.created", {"topic_id": str(topic_b), "domain_id": d, "name": "B",
                 "origin": "session"}, "tb", domain),
            _new("hub.created", {"hub_id": str(hub), "domain_id": d, "name": "Lecture 1",
                 "kind": "material", "origin": "material", "control_type": "no_goal"}, "h", domain),
            _new("hub.topics_changed", {"hub_id": str(hub), "topics": [
                {"topic_id": str(topic), "required_levels": [3]},
                {"topic_id": str(topic_b), "required_levels": [3]}]}, "ht", domain),
            _new("hub.deadline_set", {"hub_id": str(hub), "deadline_at_ms": int(time.time() * 1000)
                 + 86_400_000, "source": "learner"}, "hd", domain),
            _new("prep_mode.enabled", {"hub_id": str(hub)}, "prep", domain),
        ],
    )  # fmt: skip
    all_hub = UUID(deterministic_id(learner, stored[0].seq, 0))
    await _project(pg_db, neo4j, settings, learner)
    kinds = {r["hub"]: r["kind"] for r in await _rows(neo4j, _HUBS, learner)}
    assert kinds == {str(hub): "material", str(all_hub): "domain_all"}
    assert (await api_client.get(f"/v1/hubs/{hub}", headers=cookie)).status_code == 200
    now_ms = int(time.time() * 1000)
    assert await cheatsheet_offer(pg_db, learner, hub, settings, now_ms=now_ms)
    assert await prep_enabled(pg_db, learner_id=learner, hub_id=hub)

    # not found: a foreign hub, a hub that does not exist, the hub «весь домен»
    for hub_id in (uuid4(), all_hub):
        gone = await api_client.delete(f"/v1/hubs/{hub_id}", headers=cookie)
        assert gone.status_code == 404, gone.text
    stranger = await create_learner(pg_db)  # a foreign hub is "not found"
    theirs = session_cookie(await session_token(pg_db, stranger, settings))
    assert (await api_client.delete(f"/v1/hubs/{hub}", headers=theirs)).status_code == 404
    first = await api_client.delete(f"/v1/hubs/{hub}", headers=cookie)
    assert first.status_code == 202, first.text
    again = await api_client.delete(f"/v1/hubs/{hub}", headers=cookie)  # before the projector
    assert again.status_code == 202 and again.json() == first.json()
    events = await pg_db.fetch_all(
        "SELECT payload, domain_id FROM core.events WHERE learner_id = %(l)s "
        "AND type = 'hub.deleted'",
        {"l": learner},
    )
    assert len(events) == 1 and events[0]["payload"] == {"hub_id": str(hub)}
    assert events[0]["domain_id"] == domain

    await _project(pg_db, neo4j, settings, learner)
    kinds = {r["hub"]: r["kind"] for r in await _rows(neo4j, _HUBS, learner)}
    assert kinds == {str(all_hub): "domain_all"}  # the chapter is gone, the domain's hub stays
    assert [r["id"] for r in await _rows(neo4j, _TOPICS, learner)] == sorted(
        [str(topic), str(topic_b)]
    )

    # reads: the page, the list of chapters, the second delete, the artifact, the offer, prep mode
    assert (await api_client.get(f"/v1/hubs/{hub}", headers=cookie)).status_code == 404
    assert (await api_client.delete(f"/v1/hubs/{hub}", headers=cookie)).status_code == 404
    listed = await api_client.get(f"/v1/domains/{domain}/hubs", headers=cookie)
    assert str(hub) not in {h["hub_id"] for h in listed.json()}
    assert [h["created_seq"] for h in listed.json()] == [stored[0].seq]  # the domain's own hub
    try:
        await get_artifact(pg_db, learner_id=learner, kind="cheatsheet", owner_id=hub)
    except ArtifactNotFound:
        pass
    else:
        raise AssertionError("the cheat sheet of a deleted hub must not open")
    assert not await cheatsheet_offer(pg_db, learner, hub, settings, now_ms=now_ms)
    assert not await prep_enabled(pg_db, learner_id=learner, hub_id=hub)

    # late events about the deleted hub change nothing, and a rebuild gives the same graph
    await append_events(
        pg_db,
        learner,
        [
            _new("hub.deadline_set", {"hub_id": str(hub), "deadline_at_ms": None,
                 "source": "learner"}, "late-d", domain),
            _new("hub.topics_changed", {"hub_id": str(hub), "topics": [
                {"topic_id": str(topic), "required_levels": [3]}]}, "late-t", domain),
        ],
    )  # fmt: skip
    await _project(pg_db, neo4j, settings, learner)
    projected = await _snapshot(neo4j, learner)
    assert not [n for n in projected[0] if n.startswith(f"Hub:{hub}")]
    outcome = await rebuild_learner(pg_db, neo4j, settings, learner)
    assert outcome.status == "rebuilt"
    assert await _snapshot(neo4j, learner) == projected
