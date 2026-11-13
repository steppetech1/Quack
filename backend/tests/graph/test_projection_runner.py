"""ТЗ Ф4 WP3.3, §8.4: the batch loop of `rules@3` on Postgres and Neo4j (D-A-181).

`test_unknown_type_stops_stream` — T-P6; materials and vectors reach Neo4j end to end (the
search of stream C reads `Chunk`, #135); batches of any size give one graph (part of T-P1).
"""

from __future__ import annotations

import base64
import struct
from typing import Any
from uuid import UUID, uuid4

import orjson
import pytest
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.redis import projector_applied_channel
from quack.events.log import NewEvent, append_events
from quack.graph.projection.runner import RULES_VERSION, BatchOutcome, project_learner, resume
from tests.db.fixtures import create_learner


def _new(event_type: str, payload: dict[str, Any], key: str, **envelope: Any) -> NewEvent:
    return NewEvent(type=event_type, payload=payload, actor="code", idempotency_key=key, **envelope)


def _vector(values: list[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode()


def _materials(
    domain: UUID, topic: UUID, skill: UUID, material: UUID, chunk: UUID
) -> list[NewEvent]:
    d = str(domain)
    return [
        _new("domain.created", {"domain_id": d, "name": "Algebra"}, "d", domain_id=domain),
        _new(
            "topic.created",
            {"topic_id": str(topic), "domain_id": d, "name": "Fractions", "origin": "session"},
            "t",
            domain_id=domain,
        ),
        _new(
            "skill.created",
            {
                "skill_id": str(skill),
                "topic_id": str(topic),
                "domain_id": d,
                "name": "add fractions",
                "summary": "s",
                "status": "active",
                "origin": "session",
            },
            "s",
            domain_id=domain,
        ),
        _new(
            "material.uploaded",
            {"material_id": str(material), "object_key": "k", "mime": "application/pdf"},
            "m",
            domain_id=domain,
        ),
        _new(
            "chunk.indexed",
            {
                "chunk_id": str(chunk),
                "material_id": str(material),
                "page": 4,
                "text": "a/b + c/d",
                "notation": ["a/b"],
                "teaches": [str(skill)],
                "uses": [],
            },
            "c",
            domain_id=domain,
        ),
        _new(
            "embedding.ready",
            {
                "node_kind": "chunk",
                "node_id": str(chunk),
                "model": "m",
                "dim": 2,
                "vector": _vector([0.5, -1.0]),
            },
            "e",
            domain_id=domain,
        ),
    ]


async def _project_all(db: Db, neo4j: AsyncDriver, settings: Settings, learner: UUID) -> list[str]:
    statuses: list[str] = []
    for _ in range(10):  # a watermark that does not move would loop forever
        outcome: BatchOutcome = await project_learner(db, neo4j, settings, learner)
        statuses.append(outcome.status)
        if outcome.status != "applied":
            break
    return statuses


async def _graph(neo4j: AsyncDriver, learner: UUID) -> tuple[set[Any], set[Any]]:
    async with neo4j.session() as session:
        nodes = await session.run(
            "MATCH (n {learnerId: $l}) RETURN labels(n)[0] AS label, n.id AS id, "
            "properties(n) AS props",
            l=str(learner),
        )
        found = {(r["label"], r["id"], repr(sorted(r["props"].items()))) async for r in nodes}
        rels = await session.run(
            "MATCH (a {learnerId: $l})-[r]->(b) RETURN a.id AS a, type(r) AS t, b.id AS b, "
            "properties(r) AS props",
            l=str(learner),
        )
        links = {(r["a"], r["t"], r["b"], repr(sorted(r["props"].items()))) async for r in rels}
    return found, links


async def test_materials_and_vectors_reach_neo4j(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    learner = await create_learner(pg_db)
    domain, topic, skill, material, chunk = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    await append_events(pg_db, learner, _materials(domain, topic, skill, material, chunk))

    outcome = await project_learner(pg_db, neo4j, settings, learner)
    assert (outcome.status, outcome.applied_seq) == ("applied", 6)
    async with neo4j.session() as session:
        found = await session.run(
            "MATCH (:Material {id: $m})-[:HAS_CHUNK]->(c:Chunk {id: $c})-[:TEACHES]->"
            "(:Skill {id: $s})<-[:COVERS]-(:Topic {id: $t})<-[:HAS_TOPIC]-(:Domain {id: $d}) "
            "MATCH (l:Learner {id: $l}) RETURN properties(c) AS c, properties(l) AS l",
            m=str(material),
            c=str(chunk),
            s=str(skill),
            t=str(topic),
            d=str(domain),
            l=str(learner),
        )
        row = await found.single()
    assert row is not None
    c = row["c"]
    assert (c["learnerId"], c["domainId"], c["searchable"]) == (str(learner), str(domain), True)
    assert c["embedding"] == [0.5, -1.0]
    assert (row["l"]["projectedSeq"], row["l"]["projectorVersion"]) == (6, RULES_VERSION)
    offset = await pg_db.fetch_one(
        "SELECT seq FROM ops.projector_offsets WHERE learner_id = %(learner_id)s",
        {"learner_id": learner},
    )
    assert offset is not None and offset["seq"] == 6
    async with neo4j.session() as session:
        found = await session.run(
            "MATCH (s:Skill {id: $s}) RETURN properties(s) AS s", s=str(skill)
        )
        state = (await found.single() or {"s": {}})["s"]
    # Phase B: a new skill gets every state property explicit (Т-§3.4.4, law 8).
    assert (state["l1Status"], state["l6Basis"], state["retState"], state["expCount"]) == (
        "unknown",
        "none",
        "none",
        0,
    )
    assert state["asOfSeq"] == 0  # no evidence or exposure rests on it yet
    async with neo4j.session() as session:
        found = await session.run(
            "MATCH (t:Topic {id: $t}) RETURN properties(t) AS t", t=str(topic)
        )
        shown = (await found.single() or {"t": {}})["t"]
    # Phase C: an active topic with nothing tested (Т-§8.6 rule 8), shown (Т-§4.7).
    assert (shown["displayState"], shown["visibility"], shown["nearestDueAtMs"]) == (
        "not_tested",
        "shown",
        0,
    )
    again = await project_learner(pg_db, neo4j, settings, learner)
    assert (again.status, again.applied_seq) == ("empty", 6)


async def test_batches_of_any_size_give_one_graph(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """Т-§2.5: applying in batches equals applying at once — the next batch reads what the
    earlier one wrote (the material of a chunk, the chunk of a vector)."""
    whole, small = await create_learner(pg_db), await create_learner(pg_db)
    await append_events(pg_db, whole, _materials(*[uuid4() for _ in range(5)]))
    await append_events(pg_db, small, _materials(*[uuid4() for _ in range(5)]))
    assert await _project_all(pg_db, neo4j, settings, whole) == ["applied", "empty"]
    two = settings.model_copy(update={"projector_batch_max_events": 2})
    assert await _project_all(pg_db, neo4j, two, small) == ["applied"] * 3 + ["empty"]
    whole_nodes, whole_rels = await _graph(neo4j, whole)
    small_nodes, small_rels = await _graph(neo4j, small)
    assert sorted(label for label, _, _ in whole_nodes) == sorted(
        label for label, _, _ in small_nodes
    )
    rel_types = sorted(t for _, t, _, _ in whole_rels)
    assert rel_types == sorted(t for _, t, _, _ in small_rels)
    assert rel_types == ["COVERS", "HAS_CHUNK", "HAS_HUB", "HAS_TOPIC", "OWNS", "TEACHES"]


async def test_unknown_type_stops_stream(pg_db: Db, neo4j: AsyncDriver, settings: Settings) -> None:
    """T-P6 (Т-§2.4): an unknown type — a row in `ops.projector_failures`, the learner's stream
    stands, other learners go on; `ops resume` lets it go on (after the code knows the type)."""
    broken, other = await create_learner(pg_db), await create_learner(pg_db)
    domain = uuid4()
    created = _new("domain.created", {"domain_id": str(domain), "name": "A"}, "d")
    renamed = _new("domain.created", {"domain_id": str(uuid4()), "name": "B"}, "x")
    await append_events(pg_db, broken, [created, renamed])
    await append_events(pg_db, other, [_new("domain.created", {"domain_id": str(uuid4()),
                                                               "name": "C"}, "d")])  # fmt: skip
    await pg_db.execute(
        "UPDATE core.events SET type = 'comet.sighted' "
        "WHERE learner_id = %(learner_id)s AND seq = 2",
        {"learner_id": broken},
    )

    failed = await project_learner(pg_db, neo4j, settings, broken)
    assert (failed.status, failed.applied_seq, failed.failed_seq) == ("failed", 0, 2)
    row = await pg_db.fetch_one(
        "SELECT seq, error FROM ops.projector_failures WHERE learner_id = %(learner_id)s",
        {"learner_id": broken},
    )
    assert row is not None and row["seq"] == 2 and "comet.sighted" in row["error"]
    stopped = await project_learner(pg_db, neo4j, settings, broken)
    assert (stopped.status, stopped.failed_seq) == ("stopped", 2)
    assert (await project_learner(pg_db, neo4j, settings, other)).status == "applied"
    nodes, _ = await _graph(neo4j, broken)
    assert nodes == set()  # the batch is applied whole or not at all

    await pg_db.execute(
        "UPDATE core.events SET type = 'domain.created' "
        "WHERE learner_id = %(learner_id)s AND seq = 2",
        {"learner_id": broken},
    )
    await resume(pg_db, broken)
    resumed = await project_learner(pg_db, neo4j, settings, broken)
    assert (resumed.status, resumed.applied_seq) == ("applied", 2)


async def test_rules3_stale_rebuild(pg_db: Db, neo4j: AsyncDriver, settings: Settings) -> None:
    """T-P8 (WP3.5, Д-§6.3): a learner with `projectorVersion = rules@2` is found by
    `--stale-rules`; the rebuild puts `rules@3` and the graph of the batch loop."""
    from quack.graph.projection.rebuild import rebuild_learner
    from quack.graph.projection.store import stale_learners

    learner = await create_learner(pg_db)
    await append_events(pg_db, learner, _materials(*[uuid4() for _ in range(5)]))
    await _project_all(pg_db, neo4j, settings, learner)
    projected = await _graph(neo4j, learner)
    async with neo4j.session() as session:
        await session.run(
            "MATCH (l:Learner {id: $l}) SET l.projectorVersion = 'rules@2' "
            "CREATE (:Skill {id: $junk, learnerId: $l})",
            l=str(learner),
            junk=str(uuid4()),
        )
    assert str(learner) in await stale_learners(neo4j, RULES_VERSION)
    outcome = await rebuild_learner(pg_db, neo4j, settings, learner)
    assert (outcome.status, outcome.applied_seq) == ("rebuilt", 6)
    assert str(learner) not in await stale_learners(neo4j, RULES_VERSION)
    rebuilt = await _graph(neo4j, learner)
    assert rebuilt == projected  # the junk is gone, the rest is the same graph


async def test_chunked_rebuild_respects_presence(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings, redis: Redis
) -> None:
    """WP3.5 (Д-§6.3, replaces the test of the rebuild of Phase 3): a learner over
    `rebuild_max_tx_ops` is written by parts, and only without an active session."""
    from quack.db.redis import presence_key
    from quack.graph.projection.rebuild import rebuild_learner

    learner = await create_learner(pg_db)
    await append_events(pg_db, learner, _materials(*[uuid4() for _ in range(5)]))
    tiny = settings.model_copy(update={"rebuild_max_tx_ops": 2})
    await redis.set(presence_key(learner), str(uuid4()), ex=90)
    busy = await rebuild_learner(pg_db, neo4j, tiny, learner, redis=redis)
    assert busy.status == "active_session"
    nodes, _ = await _graph(neo4j, learner)
    assert nodes == set()
    await redis.delete(presence_key(learner))
    done = await rebuild_learner(pg_db, neo4j, tiny, learner, redis=redis)
    assert done.status == "rebuilt" and done.operations > 2
    nodes, _ = await _graph(neo4j, learner)
    assert nodes != set()


async def test_changed_topics_are_published_after_commit(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings, redis: Redis
) -> None:
    """WP3.3 (`Т-§2.4`, ТЗ Ф4 §4.8): after the commit the batch publishes `applied_seq` and the
    topics it changed on `projector:applied:{learner}` — `projection_advanced` of the stream."""
    learner = await create_learner(pg_db)
    domain, topic, skill, material, chunk = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    await append_events(pg_db, learner, _materials(domain, topic, skill, material, chunk))
    pubsub = redis.pubsub()
    await pubsub.subscribe(projector_applied_channel(learner))
    try:
        outcome = await project_learner(pg_db, neo4j, settings, learner, redis=redis)
        assert outcome.status == "applied", outcome
        message = None
        for _ in range(3):  # the first read may return the confirmation of the subscription
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if message is not None:
                break
        assert message is not None
        assert orjson.loads(message["data"]) == {"applied_seq": 6, "topics": [str(topic)]}
    finally:
        await pubsub.aclose()


async def test_state_at_replays_the_log_with_rules3(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """WP8.3 (`Т-§2.6`, D-A-191): `state_at` replays the learner's events up to N with the
    handlers and folds of `rules@3` — before the verdict level 3 is not tested, after it is
    proven by a T1 of code."""
    from quack.graph.read.neo4j_reader import Neo4jGraphReader
    from tests.graph.test_projection_batches import S1, SCRIPT

    learner = await create_learner(pg_db)

    def new(e: Any, seq: int, **payload: Any) -> NewEvent:
        return _new(e.type, e.payload | payload, f"k{seq}", domain_id=e.domain_id,
                    session_id=e.session_id)  # fmt: skip

    answer, verdict = SCRIPT[11], SCRIPT[12]  # seq 12 and 13 of the script
    events = [new(e, e.seq) for e in SCRIPT[:10]]
    events += [new(answer, 11), new(verdict, 12, answer_seq=11)]
    await append_events(pg_db, learner, events)
    reader = Neo4jGraphReader(neo4j, settings, learner_id=learner, db=pg_db)
    before = await reader.state_at(learner, 11, [UUID(S1)])
    after = await reader.state_at(learner, 12, [UUID(S1)])
    assert before.skills[0].levels[2].status == "not_tested"
    assert after.skills[0].levels[2].status == "proven"


async def test_disputes_check_the_target_and_append_one_event(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """WP8.1 (`Т-§2.8`, D-A-192): a dispute of the learner's evidence or verdict and a typo are
    one event each; another learner's target is «not found»; a typo is keyed by its verdict."""
    from quack.domains.disputes import DisputeRequest, declare_typo, file_dispute

    mine, other = await create_learner(pg_db), await create_learner(pg_db)
    evidence = str(uuid4())
    async with neo4j.session() as session:
        await session.run("CREATE (:Evidence {id: $e, learnerId: $l})", e=evidence, l=str(mine))
    answer = _new("answer.submitted", {"task_id": str(uuid4()), "source": "chat",
                  "answer": {"kind": "number", "value": "2"}}, "a")  # fmt: skip
    await append_events(pg_db, mine, [answer])

    def ask(kind: str, target: str) -> DisputeRequest:
        body = {"target_kind": kind, "target_id": target, "idempotency_key": target}
        return DisputeRequest.model_validate(body)

    assert await file_dispute(pg_db, neo4j, None, mine, ask("evidence", evidence)) == 2
    assert await file_dispute(pg_db, neo4j, None, other, ask("evidence", evidence)) is None
    assert await file_dispute(pg_db, neo4j, None, mine, ask("verdict", "1:main")) == 3
    assert await file_dispute(pg_db, neo4j, None, other, ask("verdict", "1:main")) is None
    assert await declare_typo(pg_db, None, mine, 1, "main") == 4
    assert await declare_typo(pg_db, None, mine, 1, "main") == 4  # the same verdict, one event
    assert await declare_typo(pg_db, None, other, 1, "main") is None


async def test_topic_edits_check_the_topic_and_append_one_event(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """WP8.2 (`Т-§4.11`, D-A-193): each edit of the learner's topic is one event; a topic of
    another learner or of another domain is «not found»; a retry gives the same event."""
    from quack.domains import topic_edits as edits

    mine, other = await create_learner(pg_db), await create_learner(pg_db)
    t1, t2, far = uuid4(), uuid4(), uuid4()
    async with neo4j.session() as session:
        await session.run(
            "UNWIND $rows AS r CREATE (:Topic {id: r.id, learnerId: $l, name: r.name, "
            "domainId: r.d, knownClaimedAtMs: 0})",
            l=str(mine),
            rows=[
                {"id": str(t1), "name": "a", "d": "d"},
                {"id": str(t2), "name": "b", "d": "d"},
                {"id": str(far), "name": "c", "d": "e"},
            ],
        )
    assert await edits.rename(pg_db, neo4j, None, mine, t1, "A") == 1
    assert await edits.rename(pg_db, neo4j, None, mine, t1, "A") == 1  # a retry
    assert await edits.rename(pg_db, neo4j, None, other, t1, "A") is None
    assert await edits.merge(pg_db, neo4j, None, mine, t1, far) is None  # another domain
    assert await edits.merge(pg_db, neo4j, None, mine, t1, t2) == 2
    assert await edits.hide(pg_db, neo4j, None, mine, t2) == 3
    assert await edits.claim_known(pg_db, neo4j, None, mine, t2) == 4
    assert await edits.reassign(pg_db, neo4j, None, mine, 99, t2) is None  # no such episode


async def test_hub_edit_scope_and_deadline(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """WP6.3 (`Т-§4.8`, D-A-193): the learner's hub — new scope and date as events; a topic of
    another domain or a foreign hub is «not found»; `null` removes the date."""
    from quack.domains.topic_edits import EditHub, HubTopicLevels, edit_hub

    mine = await create_learner(pg_db)
    hub, topic, far = uuid4(), uuid4(), uuid4()
    async with neo4j.session() as session:
        await session.run(
            "CREATE (:Hub {id: $h, learnerId: $l, domainId: 'd', deadlineAtMs: 0}), "
            "(:Topic {id: $t, learnerId: $l, domainId: 'd'}), "
            "(:Topic {id: $f, learnerId: $l, domainId: 'e'})",
            h=str(hub),
            t=str(topic),
            f=str(far),
            l=str(mine),
        )
    scope = EditHub(topics=[HubTopicLevels(topic_id=topic, required_levels=[3, 4])])
    assert await edit_hub(pg_db, neo4j, None, mine, hub, scope) == 1
    wrong = EditHub(topics=[HubTopicLevels(topic_id=far)])
    assert await edit_hub(pg_db, neo4j, None, mine, hub, wrong) is None
    assert await edit_hub(pg_db, neo4j, None, await create_learner(pg_db), hub, scope) is None
    assert await edit_hub(pg_db, neo4j, None, mine, hub, EditHub(deadline_at_ms=None)) == 2
    events = await pg_db.fetch_all(
        "SELECT type, payload FROM core.events WHERE learner_id = %(l)s ORDER BY seq", {"l": mine}
    )
    assert [e["type"] for e in events] == ["hub.topics_changed", "hub.deadline_set"]
    assert events[1]["payload"]["deadline_at_ms"] is None


async def test_topic_and_hub_reads(pg_db: Db, neo4j: AsyncDriver, settings: Settings) -> None:
    """WP6.5 (§6.4, `Т-§22.2`, D-A-198): a projected topic is read with codes and levels and its
    places in sessions; a hub created by the learner is in the hubs of the domain and has its
    readiness; another learner's ids are «not found»."""
    from quack.domains import graph_reads as reads

    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    domain, topic, skill, material, chunk = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    await append_events(pg_db, learner, _materials(domain, topic, skill, material, chunk))
    await _project_all(pg_db, neo4j, settings, learner)
    view = await reads.topic_view(pg_db, neo4j, learner, topic)
    assert view is not None
    assert (view.display.display_state, view.visibility, view.places) == ("not_tested", "shown", [])
    assert [lv.status for lv in view.skills[0].levels] == ["unknown"] * 6
    assert await reads.topic_view(pg_db, neo4j, other, topic) is None
    body = reads.CreateHub(name="Exam", control_type="tasks", idempotency_key="exam")
    created = await reads.create_hub(pg_db, neo4j, None, learner, domain, body)
    assert created is not None
    assert await reads.create_hub(pg_db, neo4j, None, learner, domain, body) == created  # retry
    assert await reads.create_hub(pg_db, neo4j, None, other, domain, body) is None
    await _project_all(pg_db, neo4j, settings, learner)
    hubs = await reads.domain_hubs(neo4j, settings, learner, domain)
    assert hubs is not None and created.hub_id in {h.hub_id for h in hubs}
    hub = await reads.hub_view(neo4j, settings, learner, created.hub_id)
    assert hub is not None and hub.goal.control_type == "tasks"
    assert await reads.hub_view(neo4j, settings, other, created.hub_id) is None


async def test_prep_mode_and_grade(pg_db: Db, neo4j: AsyncDriver, settings: Settings) -> None:
    """T-RV4, WP17 (`Т-§14.4`, `Т-§14.7`, `Т-§14.8`, D-A-195): «Сегодня» of a domain with nothing
    to repeat is empty and writes no event; the past deadline of a hub offers the grade until it
    is reported; prep mode switches only by its explicit event, once per state; another
    learner's ids are «not found». The deadline weight after the date:
    `test_review_candidates_reasons_scope_and_isolation`; slot 6 of the turn is B's (WP17.4)."""
    from datetime import UTC, datetime

    from quack.review import today as review

    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    domain, topic, skill, material, chunk = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    await append_events(pg_db, learner, _materials(domain, topic, skill, material, chunk))
    await _project_all(pg_db, neo4j, settings, learner)
    hub = uuid4()
    await append_events(pg_db, learner, [
        _new("hub.created", {"hub_id": str(hub), "domain_id": str(domain), "name": "Exam",
             "kind": "request", "origin": "learner", "control_type": "tasks"}, "h"),
        _new("hub.deadline_set", {"hub_id": str(hub), "deadline_at_ms": 1_000,
             "source": "learner"}, "dl"),
    ])  # fmt: skip
    await _project_all(pg_db, neo4j, settings, learner)
    now = datetime(2026, 11, 3, tzinfo=UTC)
    view = await review.today(pg_db, neo4j, None, settings, learner, domain, now)
    assert view is not None and (view.items, view.minutes) == ([], 0.0)
    assert view.grade_hub_ids == [hub]
    assert await review.today(pg_db, neo4j, None, settings, other, domain, now) is None
    on = await review.set_prep_mode(pg_db, neo4j, None, learner, hub, True)
    assert (
        on is not None and await review.set_prep_mode(pg_db, neo4j, None, learner, hub, True) == on
    )
    off = await review.set_prep_mode(pg_db, neo4j, None, learner, hub, False)
    assert off is not None and off > on
    assert await review.set_prep_mode(pg_db, neo4j, None, other, hub, True) is None
    grade = review.Grade(grade="5", scale="1-5")
    assert await review.report_grade(pg_db, neo4j, None, learner, hub, grade) is not None
    after = await review.today(pg_db, neo4j, None, settings, learner, domain, now)
    assert after is not None and after.grade_hub_ids == []


async def test_delete_domain(pg_db: Db, neo4j: AsyncDriver, settings: Settings) -> None:
    """WP25 (`Т-§19.5`, `Д-§8.5`, D-A-197): no record in the journal — nothing is deleted; a
    foreign domain is «not found»; the deletion removes the events and objects of the domain,
    keeps the rest, writes `ops.deletion_log` and rebuilds the graph without the domain."""
    from datetime import UTC, datetime

    from quack.deletion.domain import delete_domain
    from quack.storage.deletions import DeletionJournalUnavailable
    from tests.support.deletions import FakeDeletionJournal
    from tests.support.storage import InMemoryObjectStore

    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    gone_domain, kept_domain = uuid4(), uuid4()
    gone = _materials(gone_domain, *[uuid4() for _ in range(4)])
    kept = _materials(kept_domain, *[uuid4() for _ in range(4)])
    kept = [e.model_copy(update={"idempotency_key": f"kept:{e.idempotency_key}"}) for e in kept]
    await append_events(pg_db, learner, [*gone, *kept])
    await _project_all(pg_db, neo4j, settings, learner)
    store = InMemoryObjectStore()
    await store.put("k", b"pdf", "application/pdf")  # the object_key of both materials
    now = datetime(2026, 11, 3, tzinfo=UTC)
    down = FakeDeletionJournal(available=False)
    count = "SELECT count(*) AS n FROM core.events WHERE learner_id = %(l)s"
    before = await pg_db.fetch_one(count, {"l": learner})
    with pytest.raises(DeletionJournalUnavailable):  # T-DL1: no record — the data are intact
        await delete_domain(pg_db, neo4j, settings, down, store, learner, gone_domain, now)
    assert await pg_db.fetch_one(count, {"l": learner}) == before and "k" in store.objects
    journal = FakeDeletionJournal()
    assert (
        await delete_domain(pg_db, neo4j, settings, journal, store, other, gone_domain, now) is None
    )
    done = await delete_domain(pg_db, neo4j, settings, journal, store, learner, gone_domain, now)
    assert done is not None and done.copies_days == 15
    assert [r.deletion_id for r in journal.records] == [done.deletion_id]
    left = await pg_db.fetch_all(
        "SELECT domain_id FROM core.events WHERE learner_id = %(l)s", {"l": learner}
    )
    assert {r["domain_id"] for r in left} == {kept_domain}
    assert "k" not in store.objects
    log = await pg_db.fetch_one(
        "SELECT kind, target_id FROM ops.deletion_log WHERE deletion_id = %(d)s",
        {"d": done.deletion_id},
    )
    assert log is not None and (log["kind"], log["target_id"]) == ("domain", gone_domain)
    async with neo4j.session() as session:
        result = await session.run(
            "MATCH (n {learnerId: $l}) WHERE n.domainId = $d RETURN count(n) AS n",
            l=str(learner),
            d=str(gone_domain),
        )
        assert (await result.single() or {"n": -1})["n"] == 0


async def test_projector_process_walks_the_lagging_learners(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """WP1.3 (D-A-200): one pass projects every lagging learner and skips a stopped stream; the
    next pass finds nothing (the copy of the watermark is level)."""
    from quack.graph.projection.process import ProjectorP4

    first, second, stopped = [await create_learner(pg_db) for _ in range(3)]
    for learner in (first, second, stopped):
        await append_events(pg_db, learner, _materials(*[uuid4() for _ in range(5)]))
    await pg_db.execute(
        "INSERT INTO ops.projector_failures (learner_id, seq, error) "
        "VALUES (%(learner_id)s, 1, 'test')",
        {"learner_id": stopped},
    )
    projector = ProjectorP4(settings, pg_db, neo4j)
    mine = {first, second, stopped}
    assert mine & set(await projector.pending_learners(10_000)) == {first, second}
    await projector.run_once()
    assert mine & set(await projector.pending_learners(10_000)) == set()  # level now
    async with neo4j.session() as session:
        result = await session.run(
            "MATCH (l:Learner) WHERE l.id IN $ids RETURN l.id AS id, l.projectedSeq AS seq",
            ids=[str(first), str(second), str(stopped)],
        )
        seqs = {r["id"]: r["seq"] async for r in result}
    assert seqs == {str(first): 6, str(second): 6}


async def test_workers_adapter_runs_a_registered_handler(pg_db: Db, settings: Settings) -> None:
    """WP1.3 (D-A-116, D-A-200): a handler registered as in Phase 3 gets the job of the Phase 4
    queue in its shape, with the context of the process; the job succeeds."""
    from types import SimpleNamespace
    from typing import cast

    from quack.jobs import queue_p4
    from quack.jobs.executor_p4 import JobWorker
    from quack.jobs.rules_p4 import JobPlan
    from quack.jobs.runner import JobContext
    from quack.jobs.types import ClaimedJob as Phase3Job
    from quack.runtime.phase4 import bind_handlers

    learner = await create_learner(pg_db)
    seen: list[Phase3Job] = []

    async def handler(job: Phase3Job, ctx: JobContext) -> None:
        seen.append(job)

    ctx = cast(JobContext, SimpleNamespace(settings=settings, db=pg_db))
    plan = JobPlan(kind="check.model", job_key=f"adapter:{learner}", priority_class="P1",
                   learner_id=learner, payload={"answer_seq": 3})  # fmt: skip
    assert await queue_p4.enqueue(pg_db, plan) == "inserted"
    worker = JobWorker(settings, pg_db, bind_handlers({"check.model": handler}, ctx),
                       admission=lambda: _allowed(["check.model"]))  # fmt: skip
    while seen == [] and await worker.run_one():
        pass
    assert [(j.kind, j.learner_id, j.payload, j.attempt, j.job_key) for j in seen] == [
        ("check.model", learner, {"answer_seq": 3}, 1, f"adapter:{learner}")
    ]
    row = await pg_db.fetch_one(
        "SELECT status FROM ops.jobs WHERE job_key = %(k)s", {"k": f"adapter:{learner}"}
    )
    assert row is not None and row["status"] == "succeeded"


async def _allowed(kinds: list[str]) -> list[str]:
    return kinds


async def test_projector_process_starts_and_stops(neo4j: AsyncDriver, settings: Settings) -> None:
    """WP1.3 (D-A-200): `projector` of Phase 4 checks the Neo4j schema and its two supervised
    tasks end on `stop` (Д-§6.1)."""
    import asyncio

    from quack.runtime.phase4 import projector_process

    stop = asyncio.Event()
    stop.set()
    assert await asyncio.wait_for(projector_process(settings, stop), timeout=30) == 0


async def test_reconcile_and_replay(pg_db: Db, neo4j: AsyncDriver, settings: Settings) -> None:
    """T-DL3, WP25 п.5 (D-A-201): a record of the journal without its row of `ops.deletion_log`
    runs again; a second reconcile finds nothing; a replay after a «restore» deletes again and is
    idempotent."""
    from datetime import UTC, datetime

    from quack.deletion.journal import reconcile_deletions, replay_deletions
    from quack.storage.deletions import DeletionRecord
    from tests.support.deletions import FakeDeletionJournal
    from tests.support.storage import InMemoryObjectStore

    learner, domain = await create_learner(pg_db), uuid4()
    events = _materials(domain, *[uuid4() for _ in range(4)])
    await append_events(pg_db, learner, events)
    await _project_all(pg_db, neo4j, settings, learner)
    journal, store = FakeDeletionJournal(), InMemoryObjectStore()
    since = datetime(2026, 11, 1, tzinfo=UTC)
    journal.records.append(
        DeletionRecord(uuid4(), learner, "domain", domain, datetime(2026, 11, 3, tzinfo=UTC))
    )  # written, but the deletion did not run (a crash before `ops.deletion_log`)

    async def left() -> int:
        row = await pg_db.fetch_one(
            "SELECT count(*) AS n FROM core.events WHERE learner_id = %(l)s", {"l": learner}
        )
        return -1 if row is None else int(row["n"])

    assert await reconcile_deletions(pg_db, neo4j, settings, journal, store, since) == 1
    assert await left() == 0
    assert await reconcile_deletions(pg_db, neo4j, settings, journal, store, since) == 0
    restored = [e.model_copy(update={"idempotency_key": f"r:{e.idempotency_key}"}) for e in events]
    await append_events(pg_db, learner, restored)  # the dump brought the domain back
    assert await replay_deletions(pg_db, neo4j, settings, journal, store, since) == 1
    assert await left() == 0
    assert await replay_deletions(pg_db, neo4j, settings, journal, store, since) == 1
    assert await left() == 0


async def test_delete_topic(pg_db: Db, neo4j: AsyncDriver, settings: Settings) -> None:
    """WP25 (`Т-§19.5`, D-A-202): the events of the structure of the topic and of its skills, its
    tasks with their answers and verdicts go; the turn of the teacher stays (a message of the
    session); the graph is rebuilt without the topic, its skill and task; another topic stays."""
    from datetime import UTC, datetime

    from quack.deletion.topic import delete_topic
    from tests.graph.test_projection_batches import S1, S2, SCRIPT, T1, T2, TASK
    from tests.support.deletions import FakeDeletionJournal
    from tests.support.storage import InMemoryObjectStore

    learner = await create_learner(pg_db)
    events = [
        _new(e.type, e.payload, f"k{e.seq}", domain_id=e.domain_id, session_id=e.session_id)
        for e in SCRIPT
    ]
    await append_events(pg_db, learner, events)
    await _project_all(pg_db, neo4j, settings, learner)
    journal = FakeDeletionJournal()
    now = datetime(2026, 11, 3, tzinfo=UTC)
    store = InMemoryObjectStore()
    other = await create_learner(pg_db)  # law 1: a topic of another learner is «not found»
    assert await delete_topic(pg_db, neo4j, settings, journal, store, other, UUID(T1), now) is None
    assert journal.records == []
    done = await delete_topic(pg_db, neo4j, settings, journal, store, learner, UUID(T1), now)
    assert done is not None and [r.kind for r in journal.records] == ["topic"]
    rows = await pg_db.fetch_all(
        "SELECT type FROM core.events WHERE learner_id = %(l)s ORDER BY seq", {"l": learner}
    )
    left = [r["type"] for r in rows]
    gone = {"skill.created", "task.created", "answer.submitted", "verdict.recorded"}
    assert "turn.committed" in left and left.count("skill.created") == 1  # S2 stays
    assert not gone - {"skill.created"} & set(left)
    async with neo4j.session() as session:
        result = await session.run(
            "MATCH (n {learnerId: $l}) WHERE n.id IN $ids RETURN n.id AS id",
            l=str(learner),
            ids=[T1, T2, S1, S2, TASK],
        )
        found = {r["id"] async for r in result}
    assert found == {T2, S2}


async def test_manifests_cleanup_deletes_only_old_manifests(pg_db: Db) -> None:
    """WP7.5 (`Т-§3.6`, D-A-204): manifests older than `turn_manifest_retention_days` go; newer
    ones stay."""
    from datetime import UTC, datetime, timedelta

    from quack.jobs.cleanup_p4 import delete_old_manifests

    learner, now = await create_learner(pg_db), datetime(2026, 11, 3, tzinfo=UTC)
    for manifest, age in ((uuid4(), 40), (uuid4(), 5)):
        await pg_db.execute(
            "INSERT INTO ops.turn_manifests (manifest_id, learner_id, session_id, prompt_version, "
            "role, model, projected_seq, created_at) VALUES (%(m)s, %(l)s, %(s)s, 'teacher@1', "
            "'teacher', 'm', 0, %(at)s)",
            {"m": manifest, "l": learner, "s": uuid4(), "at": now - timedelta(days=age)},
        )
    assert await delete_old_manifests(pg_db, now - timedelta(days=30)) >= 1
    rows = await pg_db.fetch_all(
        "SELECT created_at FROM ops.turn_manifests WHERE learner_id = %(l)s", {"l": learner}
    )
    assert [r["created_at"] for r in rows] == [now - timedelta(days=5)]


async def test_exports_cleanup_deletes_only_expired_archives(pg_db: Db) -> None:
    """WP7.5 (`Д-§8.4`, `Т-§19.6`, D-A-215): an archive whose link expired is deleted from the
    store and its row is marked `expired`; a live one stays; a second run finds nothing."""
    from datetime import UTC, datetime, timedelta

    from quack.jobs.cleanup_p4 import delete_expired_exports
    from tests.support.storage import InMemoryObjectStore

    learner, now = await create_learner(pg_db), datetime(2026, 11, 4, tzinfo=UTC)
    store = InMemoryObjectStore()
    old, live = uuid4(), uuid4()
    for export_id, hours in ((old, -1), (live, 5)):
        key = f"learners/{learner}/exports/{export_id}.zip"
        await store.put(key, b"z", "application/zip")
        await pg_db.execute(
            "INSERT INTO content.exports (export_id, learner_id, status, object_key, expires_at) "
            "VALUES (%(e)s, %(l)s, 'ready', %(k)s, %(at)s)",
            {"e": export_id, "l": learner, "k": key, "at": now + timedelta(hours=hours)},
        )
    assert await delete_expired_exports(pg_db, store, now) == 1
    assert sorted(store.objects) == [f"learners/{learner}/exports/{live}.zip"]
    row = await pg_db.fetch_one(
        "SELECT status, object_key FROM content.exports WHERE export_id = %(e)s", {"e": old}
    )
    assert row is not None and row["status"] == "expired" and row["object_key"] is None
    assert await delete_expired_exports(pg_db, store, now) == 0


async def test_delete_account(pg_db: Db, neo4j: AsyncDriver, settings: Settings) -> None:
    """WP25 п.3 (`Т-§19.5`, D-A-205): the journal record first, then every row of the learner,
    its objects under `learners/{id}/` and its subgraph; the log row stays; a replay of the
    record finds nothing and does not fail; another learner is untouched."""
    from datetime import UTC, datetime

    from quack.deletion.account import delete_account
    from quack.deletion.journal import replay_deletions
    from quack.llm.tracing import NullTracer
    from tests.support.deletions import FakeDeletionJournal
    from tests.support.storage import InMemoryObjectStore

    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    for who in (learner, other):
        await append_events(pg_db, who, _materials(*[uuid4() for _ in range(5)]))
        await _project_all(pg_db, neo4j, settings, who)
    store = InMemoryObjectStore()
    await store.put(f"learners/{learner}/materials/a.pdf", b"x", "application/pdf")
    await store.put(f"learners/{other}/materials/b.pdf", b"y", "application/pdf")
    journal, now = FakeDeletionJournal(), datetime(2026, 11, 3, tzinfo=UTC)
    deleted: list[str] = []

    class _Tracer(NullTracer):
        async def delete_learner(self, pseudonym: str) -> bool:
            deleted.append(pseudonym)
            return False

    done = await delete_account(
        pg_db, neo4j, journal, store, learner, now, tracer=_Tracer(), pseudonym="p-1"
    )
    assert deleted == ["p-1"]
    assert [r.kind for r in journal.records] == ["account"]
    for query in (
        "SELECT count(*) AS n FROM core.learners WHERE learner_id = %(l)s",
        "SELECT count(*) AS n FROM core.events WHERE learner_id = %(l)s",
    ):
        row = await pg_db.fetch_one(query, {"l": learner})
        assert row is not None and row["n"] == 0, query
    log = await pg_db.fetch_one(
        "SELECT kind FROM ops.deletion_log WHERE deletion_id = %(d)s", {"d": done.deletion_id}
    )
    assert log is not None and log["kind"] == "account"
    assert sorted(store.objects) == [f"learners/{other}/materials/b.pdf"]
    nodes_mine, _ = await _graph(neo4j, learner)
    nodes_other, _ = await _graph(neo4j, other)
    assert nodes_mine == set() and nodes_other != set()
    assert await replay_deletions(pg_db, neo4j, settings, journal, store, now) == 1  # repeatable


async def test_slice_waits_for_the_journal_of_phase_4(pg_db: Db) -> None:
    """D-A-210: without `min_seq` the slice waits for the tail of `core.learners.seq` (`db=`);
    a watermark behind it gives `lag_degraded`, at or past it — a plain answer."""
    from quack.graph.slice import SliceDeps, read_consistent_slice

    learner = await create_learner(pg_db)
    domain = uuid4()
    await append_events(
        pg_db,
        learner,
        [_new("domain.created", {"domain_id": str(domain), "name": "A"}, "d", domain_id=domain)],
    )

    async def load() -> str:
        return "slice"

    def deps(applied: int) -> SliceDeps:
        async def reader(_: UUID) -> int:
            return applied

        return SliceDeps(None, reader, db=pg_db)

    behind = await read_consistent_slice(learner, None, load, 50, deps=deps(0))
    assert (behind.min_seq, behind.lag_degraded) == (1, True)
    caught_up = await read_consistent_slice(learner, None, load, 50, deps=deps(1))
    assert (caught_up.min_seq, caught_up.lag_degraded) == (1, False)


# --- findings of the audit of 2026-11-13 (T-P1: batches = rebuild; Т-§2.7) -------------------


def _practice(domain: UUID, topic: UUID, skill: UUID, task: UUID) -> list[NewEvent]:
    """A skill, a task on it, an answer and a code verdict (seq 1..6)."""
    d = str(domain)
    tests = [{"skill_id": str(skill), "level": 3}]
    answer_key = {"type": "number", "value": {"latex": "2", "sympy": "2"}}
    markup = {"tests": tests, "method_named": False, "answer": answer_key}
    created = {"task_id": str(task), "family_id": str(uuid4()), "source": "teacher",
               "verification": "unchecked", "markup": markup}  # fmt: skip
    answer = {"task_id": str(task), "answer": {"kind": "number", "value": "2"}, "source": "chat"}
    verdict = {"answer_seq": 5, "part": "main", "outcome": "correct", "method": "exact",
               "verification": "code"}  # fmt: skip
    new_topic = {"topic_id": str(topic), "domain_id": d, "name": "F", "origin": "session"}
    new_skill = {"skill_id": str(skill), "topic_id": str(topic), "domain_id": d, "name": "add",
                 "summary": "s", "status": "active", "origin": "session"}  # fmt: skip
    return [
        _new("domain.created", {"domain_id": d, "name": "Algebra"}, "d", domain_id=domain),
        _new("topic.created", new_topic, "t", domain_id=domain),
        _new("skill.created", new_skill, "s", domain_id=domain),
        _new("task.created", created, "k", domain_id=domain),
        _new("answer.submitted", answer, "a", domain_id=domain),
        _new("verdict.recorded", verdict, "v", domain_id=domain),
    ]


async def _evidence_classes(neo4j: AsyncDriver, learner: UUID) -> set[tuple[str, int]]:
    async with neo4j.session() as session:
        result = await session.run(
            "MATCH (e:Evidence {learnerId: $l}) RETURN e.verification AS v, e.tier AS t",
            l=str(learner),
        )
        return {(r["v"], r["t"]) async for r in result}


async def test_a_dispute_lowers_given_verdicts_in_the_batch_loop_as_in_the_rebuild(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """Т-§5.6, D-A-132, T-P1: `task.key_disputed` after the verdict lowers the evidence of the
    task read from Neo4j (the neighbourhood of the task) to class model, T2; the rebuild gives
    the same graph."""
    from quack.graph.projection.rebuild import rebuild_learner

    learner, task = await create_learner(pg_db), uuid4()
    await append_events(pg_db, learner, _practice(uuid4(), uuid4(), uuid4(), task))
    await _project_all(pg_db, neo4j, settings, learner)
    assert await _evidence_classes(neo4j, learner) == {("code", 1)}
    await append_events(pg_db, learner, [_new("task.key_disputed", {"task_id": str(task)}, "x")])
    assert await _project_all(pg_db, neo4j, settings, learner) == ["applied", "empty"]
    assert await _evidence_classes(neo4j, learner) == {("model", 2)}
    incremental = await _graph(neo4j, learner)
    assert (await rebuild_learner(pg_db, neo4j, settings, learner)).status == "rebuilt"
    assert await _graph(neo4j, learner) == incremental


async def test_a_stale_learned_property_of_the_profile_is_removed_by_a_batch(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """T-P1 (`Т-§9.5`, law 8): the profile fold sees the `Profile` that is in Neo4j, so a
    `learned*` without data is removed in a batch as in the rebuild."""
    from quack.graph.projection.rebuild import rebuild_learner

    learner = await create_learner(pg_db)
    await append_events(pg_db, learner, _practice(uuid4(), uuid4(), uuid4(), uuid4()))
    await _project_all(pg_db, neo4j, settings, learner)
    async with neo4j.session() as session:
        await session.run(
            "MATCH (:Learner {id: $l})-[:HAS_PROFILE]->(p:Profile) SET p.learnedBranchRate = 0.5",
            l=str(learner),
        )
    report = _new("confidence.reported", {"target_seq": 5, "value": 3}, "c")
    await append_events(pg_db, learner, [report])
    assert await _project_all(pg_db, neo4j, settings, learner) == ["applied", "empty"]
    async with neo4j.session() as session:
        result = await session.run(
            "MATCH (:Learner {id: $l})-[:HAS_PROFILE]->(p:Profile) RETURN properties(p) AS p",
            l=str(learner),
        )
        record = await result.single()
    assert record is not None and "learnedBranchRate" not in record["p"]
    incremental = await _graph(neo4j, learner)
    await rebuild_learner(pg_db, neo4j, settings, learner)
    assert await _graph(neo4j, learner) == incremental


@pytest.mark.parametrize("fail_at", [1, 3])
async def test_a_failed_chunked_rebuild_is_finished_by_the_batch_loop(
    pg_db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    fail_at: int,
) -> None:
    """Т-§2.7: a rebuild by parts that fails after the delete (before the first part, or midway)
    leaves the learner to the projector — the copy of the watermark is 0, `_PENDING` finds the
    learner, the batch loop clears the partial graph and projects the log again."""
    from quack.graph.projection import store
    from quack.graph.projection.process import ProjectorP4
    from quack.graph.projection.rebuild import rebuild_learner

    learner = await create_learner(pg_db)
    await append_events(pg_db, learner, _practice(uuid4(), uuid4(), uuid4(), uuid4()))
    await _project_all(pg_db, neo4j, settings, learner)
    projected = await _graph(neo4j, learner)

    real_write, calls = store._write, 0

    async def failing(*args: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise RuntimeError("neo4j went away")
        await real_write(*args)

    tiny = settings.model_copy(update={"rebuild_max_tx_ops": 2})
    monkeypatch.setattr(store, "_write", failing)
    with pytest.raises(RuntimeError):
        await rebuild_learner(pg_db, neo4j, tiny, learner, respect_presence=False)
    monkeypatch.setattr(store, "_write", real_write)
    assert await _graph(neo4j, learner) != projected  # empty or partial
    row = await pg_db.fetch_one(
        "SELECT seq FROM ops.projector_offsets WHERE learner_id = %(l)s", {"l": learner}
    )
    assert row is not None and row["seq"] == 0
    assert learner in await ProjectorP4(settings, pg_db, neo4j).pending_learners()

    assert (await _project_all(pg_db, neo4j, settings, learner))[0] == "applied"
    assert await _graph(neo4j, learner) == projected
    assert await store.read_watermark(neo4j, str(learner)) == (6, RULES_VERSION)
    assert learner not in await ProjectorP4(settings, pg_db, neo4j).pending_learners()
