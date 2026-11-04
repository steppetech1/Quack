"""`ExtraReads` on a test Neo4j (ТЗ Ф4 WP6.4, WP17.1, WP17.4, WP8.3, WP15 п.2, WP16.2).

Every test owns a random learner and deletes only its own nodes; nothing here clears the
database. Nodes carry the properties of Т-§3.4 (levels `lNStatus`, retention `ret*`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from neo4j import AsyncDriver

from quack.config import Settings
from quack.embeddings.client import EmbeddingsUnavailable
from quack.events.log import StoredEvent as LogEvent
from quack.graph.memory import InMemoryGraph
from quack.graph.read.models import ReviewScope
from quack.graph.read.neo4j_extras import ExtraReads

DAY = 86_400_000
NOW = 1_800_000_000_000


class Reader(ExtraReads):
    def __init__(self, driver: AsyncDriver, settings: Settings) -> None:
        self._neo4j = driver
        self._settings = settings


class FakeEmbedder:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.vector for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self.vector

    async def aclose(self) -> None:
        return None


def vec(settings: Settings, hot: int) -> list[float]:
    v = [0.0] * settings.embedding_dim
    v[hot] = 1.0
    return v


class Graph:
    """Seeds nodes of one learner and removes them afterwards."""

    def __init__(self, driver: AsyncDriver) -> None:
        self.driver = driver
        self.learner = str(uuid4())

    def id(self) -> str:
        return str(uuid4())

    async def run(self, query: str, **params: Any) -> None:
        async with self.driver.session() as session:
            await session.run(query, learner=self.learner, **params)  # type: ignore[arg-type]

    async def node(self, label: str, node_id: str, **props: Any) -> str:
        await self.run(
            f"MERGE (n:{label} {{id: $id}}) SET n += $props, n.learnerId = $learner",
            id=node_id,
            props=props,
        )
        return node_id

    async def rel(self, a: str, rel: str, b: str, **props: Any) -> None:
        await self.run(
            f"MATCH (a {{id: $a}}), (b {{id: $b}}) WHERE a.learnerId = $learner "
            f"MERGE (a)-[r:{rel}]->(b) SET r += $props",
            a=a,
            b=b,
            props=props,
        )

    async def skill(self, topic: str | None = None, **props: Any) -> str:
        sid = await self.node("Skill", self.id(), **{"domainId": "d", "name": "s", **props})
        if topic:
            await self.rel(topic, "COVERS", sid)
        return sid

    async def topic(self, name: str = "t", **props: Any) -> str:
        return await self.node("Topic", self.id(), name=name, status="active", **props)

    async def evidence(
        self, skill: str, *, level: int = 3, tier: int = 1, outcome: str = "correct",
        at: int = NOW, seq: int = 1, task: str | None = None, session: str = "", **props: Any
    ) -> str:  # fmt: skip
        base: dict[str, Any] = {
            "level": level, "tier": tier, "outcome": outcome, "atMs": at, "sourceSeq": seq,
            "status": "active", "assistance": "none", "hintStep": 0, "quote": "",
            "sourceSessionId": session, "domainId": "d", "searchable": False,
        }  # fmt: skip
        eid = await self.node("Evidence", self.id(), **{**base, **props})
        await self.rel(eid, "ON", skill)
        if task:
            await self.rel(eid, "ON_TASK", task)
        return eid

    async def drop(self) -> None:
        await self.run("MATCH (n) WHERE n.learnerId = $learner DETACH DELETE n")


@pytest.fixture
async def g(neo4j_driver: AsyncDriver) -> AsyncIterator[Graph]:
    graph = Graph(neo4j_driver)
    yield graph
    await graph.drop()


@pytest.fixture
async def other(neo4j_driver: AsyncDriver) -> AsyncIterator[Graph]:
    graph = Graph(neo4j_driver)
    yield graph
    await graph.drop()


@pytest.fixture
def reader(neo4j_driver: AsyncDriver, settings: Settings) -> Reader:
    return Reader(neo4j_driver, settings)


# --- skill_history, prerequisites ---------------------------------------------------------


async def test_skill_history_orders_filters_and_isolates(
    g: Graph, other: Graph, reader: Reader
) -> None:
    skill = await g.skill()
    task = await g.node("Task", g.id())
    old = await g.evidence(skill, level=3, at=NOW - 10, seq=1, quote="old", task=task)
    new = await g.evidence(
        skill, level=4, tier=2, outcome="incorrect", at=NOW, seq=2, quote="new",
        assistance="hint", hintStep=2,
    )  # fmt: skip
    disputed = await g.evidence(skill, at=NOW + 1, seq=3)
    await g.run("MATCH (e:Evidence {id: $id}) SET e.status = 'disputed'", id=disputed)
    # foreign evidence hung on this skill by mistake must not be read
    intruder = await other.evidence(skill, at=NOW + 5, seq=4)

    found = await reader.skill_history(UUID(skill), limit=10)
    assert [str(f.evidence_id) for f in found] == [new, old]
    assert intruder not in {str(f.evidence_id) for f in found}
    assert found[0].source == "model" and found[0].assistance == "hint" and found[0].hint_step == 2
    assert (
        found[1].source == "code" and str(found[1].task_id) == task and found[1].hint_step is None
    )
    only4 = await reader.skill_history(UUID(skill), level=4)
    assert [str(f.evidence_id) for f in only4] == [new]
    assert len(await reader.skill_history(UUID(skill), limit=1)) == 1
    assert await reader.skill_history(uuid4()) == []


async def test_prerequisites_levels_and_order(g: Graph, reader: Reader) -> None:
    skill = await g.skill()
    strong = await g.skill(name="strong", l3Status="proven", l3Basis="direct")
    weak = await g.skill(name="weak", l3Status="failed", l3Basis="direct")
    await g.rel(skill, "REQUIRES", strong, confidence=0.9)
    await g.rel(skill, "REQUIRES", weak, confidence=0.5)
    await g.evidence(weak, level=3, outcome="incorrect", seq=1)

    found = await reader.prerequisites(UUID(skill))
    assert [str(p.skill_id) for p in found] == [strong, weak]
    by = {str(p.skill_id): {lv.level: lv for lv in p.levels} for p in found}
    assert by[strong][3].proven_by_direct and by[strong][3].status == "proven"
    assert by[strong][1].status == "not_tested"  # law 8: no data is not a failure
    assert by[weak][3].last_t1_failed and by[weak][3].evidence_count == 1
    assert len(await reader.prerequisites(UUID(skill), limit=1)) == 1


# --- similar_mistakes, past_solution ------------------------------------------------------


async def test_similar_mistakes_by_evidence_and_text(
    g: Graph, other: Graph, reader: Reader, settings: Settings
) -> None:
    skill = await g.skill()
    near = await g.evidence(
        skill, outcome="incorrect", seq=2, quote="near", embedding=vec(settings, 1), searchable=True
    )
    cur = await g.evidence(
        skill, outcome="incorrect", seq=1, embedding=vec(settings, 1), searchable=True
    )
    await g.evidence(  # a success is not a mistake
        skill, outcome="correct", seq=3, embedding=vec(settings, 1), searchable=True
    )
    oskill = await other.skill()
    await other.evidence(  # the same vector of another learner
        oskill, outcome="incorrect", embedding=vec(settings, 1), searchable=True
    )

    found = await reader.similar_mistakes(UUID(g.learner), evidence_id=UUID(cur))
    assert [str(f.evidence_id) for f in found] == [near]
    assert found[0].quote == "near" and str(found[0].skill_id) == skill

    with pytest.raises(EmbeddingsUnavailable):
        await reader.similar_mistakes(UUID(g.learner), text="x")
    reader._embedder = FakeEmbedder(vec(settings, 1))
    by_text = await reader.similar_mistakes(UUID(g.learner), text="x")
    assert {str(f.evidence_id) for f in by_text} == {near, cur}
    with pytest.raises(ValueError):
        await reader.similar_mistakes(UUID(g.learner))


async def test_past_solution_groups_quotes_by_task(g: Graph, reader: Reader) -> None:
    skill = await g.skill()
    t1, t2, t3 = [await g.node("Task", g.id()) for _ in range(3)]
    await g.evidence(skill, task=t1, at=NOW - 3, seq=1, quote="a1")
    await g.evidence(skill, task=t1, at=NOW - 2, seq=2, quote="a2")
    await g.evidence(skill, task=t2, at=NOW - 1, seq=3, quote="b")
    await g.evidence(skill, task=t3, at=NOW - 9, seq=4, quote="c")
    await g.evidence(skill, task=t3, at=NOW - 8, seq=5)  # no quote: nothing to show

    found = await reader.past_solution(UUID(g.learner), skill_id=UUID(skill))
    assert [(str(s.task_id), s.lines) for s in found] == [(t2, ["b"]), (t1, ["a1", "a2"])]  # 2 max
    by_task = await reader.past_solution(UUID(g.learner), task_id=UUID(t1))
    assert len(by_task) == 1 and by_task[0].occurred_at_ms == NOW - 2
    assert await reader.past_solution(uuid4(), skill_id=UUID(skill)) == []


# --- misconception_detail -----------------------------------------------------------------


async def test_misconception_detail_through_alias_and_status(
    g: Graph, other: Graph, reader: Reader
) -> None:
    skill = await g.skill(name="Fractions")
    m = await g.node(
        "Misconception", g.id(), status="confirmed", statement="adds denominators", triggers=["x"]
    )
    await g.rel(m, "ABOUT", skill)
    task = await g.node("Task", g.id())
    e2 = await g.evidence(skill, tier=2, outcome="incorrect", seq=7, quote="q2", task=task)
    e1 = await g.evidence(skill, tier=1, outcome="incorrect", seq=3, quote="q1")
    for e in (e1, e2):
        await g.rel(e, "MANIFESTS", m)
    gone = await g.evidence(skill, outcome="incorrect", seq=9)
    await g.rel(gone, "MANIFESTS", m)
    await g.run("MATCH (e:Evidence {id: $id}) SET e.status = 'disputed'", id=gone)
    alias = await g.node("Alias", g.id())
    await g.rel(alias, "ALIAS_OF", m)

    for ref in (m, alias):
        detail = await reader.misconception_detail(UUID(ref))
        assert detail is not None and str(detail.misconception_id) == m
        assert (detail.status, detail.statement, detail.triggers) == (
            "confirmed",
            "adds denominators",
            ["x"],
        )
        assert detail.skill_name == "Fractions"
        assert [(x.source_seq, x.tier) for x in detail.manifestations] == [
            (3, "code"),
            (7, "model"),
        ]
        assert (
            detail.manifestations[1].quote == "q2" and str(detail.manifestations[1].task_id) == task
        )

    fresh = await g.node("Misconception", g.id(), status="unknown", statement="h")
    await g.rel(fresh, "ABOUT", skill)
    hypothesis = await reader.misconception_detail(UUID(fresh))
    assert (
        hypothesis is not None
        and hypothesis.status == "unknown"
        and hypothesis.manifestations == []
    )
    dead = await g.node("Misconception", g.id(), status="dismissed", statement="d")
    await g.rel(dead, "ABOUT", skill)
    assert await reader.misconception_detail(UUID(dead)) is None
    assert await reader.misconception_detail(uuid4()) is None


# --- extraction_candidates ----------------------------------------------------------------


async def test_extraction_candidates_session_nearest_and_restriction(
    g: Graph, other: Graph, reader: Reader, settings: Settings
) -> None:
    session = str(uuid4())
    t_a, t_b, t_c = await g.topic("A"), await g.topic("B"), await g.topic("C")
    worked = await g.skill(t_a, name="worked", embedding=vec(settings, 5))
    mate = await g.skill(t_a, name="mate", embedding=vec(settings, 6))
    near = await g.skill(t_b, name="near", embedding=vec(settings, 1))
    await g.skill(t_c, name="far", embedding=vec(settings, 9))
    await g.evidence(worked, session=session)
    m = await g.node("Misconception", g.id(), status="suspected", statement="s")
    await g.rel(m, "ABOUT", worked)
    hidden = await g.node("Misconception", g.id(), status="dismissed", statement="d")
    await g.rel(hidden, "ABOUT", worked)
    ot = await other.topic()
    await other.skill(ot, name="foreign", embedding=vec(settings, 1))
    lid, sid = UUID(g.learner), UUID(session)

    no_vector = await reader.extraction_candidates(lid, sid, None, 15)
    assert [str(c.skill_id) for c in no_vector.skills] == [worked, mate]  # the session only
    assert [str(x.misconception_id) for x in no_vector.misconceptions] == [m]
    with_vector = await reader.extraction_candidates(lid, sid, vec(settings, 1), 15)
    assert [str(c.skill_id) for c in with_vector.skills][:2] == [worked, near]
    assert {c.name for c in with_vector.skills} <= {"worked", "mate", "near", "far"}
    capped = await reader.extraction_candidates(lid, sid, vec(settings, 1), 2)
    assert [str(c.skill_id) for c in capped.skills] == [worked, near]
    by_topic = await reader.extraction_candidates(
        lid, sid, vec(settings, 1), 15, topic_id=UUID(t_b)
    )
    assert [str(c.skill_id) for c in by_topic.skills] == [near]
    listed = await reader.extraction_candidates(
        lid, sid, None, 1, skill_ids=[UUID(mate), UUID(near)]
    )
    assert {str(c.skill_id) for c in listed.skills} == {mate, near}
    empty = await reader.extraction_candidates(lid, uuid4(), None, 15)
    assert empty.skills == []


# --- review_candidates, prep_context ------------------------------------------------------


async def test_review_candidates_reasons_scope_and_isolation(
    g: Graph, other: Graph, reader: Reader
) -> None:
    """T-RV1 (`test_review_candidates_at_read`): the reasons of `Т-§14.1`, the allowed forgetting
    of an idle domain, scopes and isolation; the candidates are read, never stored."""
    recent: dict[str, Any] = {"lastPracticeAtMs": NOW - DAY}
    due = await g.skill(
        retStability=2.0, retLastRecallAtMs=NOW - 30 * DAY // 10, retDueAtMs=NOW - 1, **recent
    )
    fresh = await g.skill(
        retStability=30.0, retLastRecallAtMs=NOW - DAY, retDueAtMs=NOW + 20 * DAY, **recent
    )
    flagged = await g.skill(needsConsolidation=True, **recent)
    watched = await g.skill(**recent)
    m = await g.node("Misconception", g.id(), status="watching", watchSinceMs=NOW - 5 * DAY)
    await g.rel(m, "ABOUT", watched)
    quiet = await g.skill(**recent)
    # idle domain: nothing practised for 100 days, not needed by a goal or a deadline
    idle = await g.skill(domainId="old", retStability=1.0, retLastRecallAtMs=NOW - 90 * DAY,
                         retDueAtMs=NOW - 80 * DAY, lastPracticeAtMs=NOW - 100 * DAY)  # fmt: skip
    # a deadline with an unproven required level
    topic = await g.topic()
    due_topic = await g.skill(topic, l3Status="unknown", **recent)
    done = await g.skill(topic, l3Status="proven", l3Basis="direct", l4Status="proven", **recent)
    hub = await g.node("Hub", g.id(), deadlineAtMs=NOW + 2 * DAY, controlType="tasks", kind="exam")
    await g.rel(hub, "INCLUDES", topic, requiredLevels=[3])
    goal = await g.skill(**recent)
    await g.rel(goal, "REQUIRES", quiet, confidence=0.9)
    osk = await other.skill(needsConsolidation=True)

    found = {str(c.skill_id): c for c in await reader.review_candidates(UUID(g.learner), NOW)}
    assert set(found) == {due, flagged, watched, due_topic}
    assert found[due].reasons == ["retention"]
    assert found[flagged].reasons == ["consolidation"]
    assert found[watched].reasons == ["misconception_recheck"]
    assert found[due_topic].reasons == ["hub_requirement"]
    assert {fresh, quiet, idle, done, osk}.isdisjoint(found)

    scoped = await reader.review_candidates(
        UUID(g.learner), NOW, ReviewScope(goal_skill_ids=[UUID(goal)])
    )
    needed = {str(c.skill_id): c for c in scoped}
    assert quiet not in needed  # no reason yet: not a candidate...
    # ...but a goal prerequisite with a reason is marked and ranked by need
    await g.run("MATCH (s:Skill {id: $id}) SET s.needsConsolidation = true", id=quiet)
    scoped = await reader.review_candidates(
        UUID(g.learner), NOW, ReviewScope(goal_skill_ids=[UUID(goal)])
    )
    assert str(scoped[0].skill_id) == quiet and scoped[0].is_goal_prerequisite
    by_hub = await reader.review_candidates(UUID(g.learner), NOW, ReviewScope(hub_id=UUID(hub)))
    assert [str(c.skill_id) for c in by_hub] == [due_topic]
    by_topic = await reader.review_candidates(
        UUID(g.learner), NOW, ReviewScope(topic_id=UUID(topic))
    )
    assert [str(c.skill_id) for c in by_topic] == [due_topic]
    assert len(await reader.review_candidates(UUID(g.learner), NOW, ReviewScope(max_items=1))) == 1
    assert await reader.review_candidates(uuid4(), NOW) == []
    # T-RV4: past the date (the grade is offered and entered after it, `Т-§14.8`) the deadline
    # weighs nothing; the topics go to ordinary repetition.
    await g.run("MATCH (h:Hub {id: $id}) SET h.deadlineAtMs = $at", id=hub, at=NOW - DAY)
    assert due_topic not in {str(c.skill_id) for c in await reader.review_candidates(
        UUID(g.learner), NOW)}  # fmt: skip


async def test_prep_context_readiness_days_and_candidates(g: Graph, reader: Reader) -> None:
    ready_topic, open_topic = await g.topic("ready"), await g.topic("open")
    await g.skill(ready_topic, l3Status="proven", l3Basis="direct")
    await g.skill(ready_topic, l3Status="implied", l3Basis="direct")
    transfer = await g.skill(open_topic, l3Status="unknown",
                             lastPracticeAtMs=NOW)  # fmt: skip
    hub = await g.node(
        "Hub", g.id(), deadlineAtMs=NOW + 3 * DAY + 1, controlType="tasks", kind="exam"
    )
    for t in (ready_topic, open_topic):
        await g.rel(hub, "INCLUDES", t, requiredLevels=[3])

    prep = await reader.prep_context(UUID(hub), NOW)
    assert (prep.ready_count, prep.total_count, prep.days_to_deadline) == (1, 2, 4)
    assert {str(t) for t in prep.coverage_topic_ids} == {ready_topic, open_topic}
    assert [str(c.skill_id) for c in prep.candidates] == [transfer]
    assert prep.control_materials == []
    missing = await reader.prep_context(uuid4(), NOW)
    assert missing.total_count == 0


# --- state_at -----------------------------------------------------------------------------


def _event(seq: int, kind: str, **payload: Any) -> LogEvent:
    return LogEvent.model_construct(
        learner_id=uuid4(), seq=seq, type=kind, payload=payload,
        occurred_at=datetime.fromtimestamp(1_700_000_000 + seq, UTC),
    )  # fmt: skip


async def test_state_at_replays_events_up_to_n_not_a_fold_by_seq(
    neo4j_driver: AsyncDriver, settings: Settings
) -> None:
    skill = str(uuid4())
    log = [
        _event(1, "t.skill"),
        _event(2, "t.evidence"),
        _event(3, "t.disputed"),  # a late dispute of the evidence of event 2
    ]

    class Replaying(Reader):
        async def _events_up_to(self, learner_id: UUID, seq_n: int) -> list[LogEvent]:
            return [e for e in log if e.seq <= seq_n]

        async def _replay(self, learner_id: UUID, events: Sequence[LogEvent]) -> InMemoryGraph:
            graph = InMemoryGraph()
            for e in events:  # a stand-in with the handlers' order: events decide, not seq
                if e.type == "t.skill":
                    graph.nodes[("Skill", skill)] = {"id": skill, "name": "s", "l3Status": "proven",
                                                      "l3Basis": "direct"}  # fmt: skip
                elif e.type == "t.evidence":
                    graph.nodes[("Evidence", "e")] = {"id": "e", "status": "active", "level": 3,
                                                       "tier": 1, "outcome": "correct", "atMs": 1,
                                                       "sourceSeq": 2}  # fmt: skip
                    graph.rels[("Evidence", "e", "ON", "Skill", skill)] = {}
                elif e.type == "t.disputed":
                    graph.nodes[("Evidence", "e")]["status"] = "disputed"
                    graph.nodes[("Skill", skill)]["l3Status"] = "unknown"
            return graph

    state = Replaying(neo4j_driver, settings)
    learner = uuid4()
    before = await state.state_at(learner, 2, [UUID(skill), uuid4()])
    after = await state.state_at(learner, 3, [UUID(skill)])
    assert before.seq == 2 and len(before.skills) == 1  # the unknown skill is skipped
    level3 = before.skills[0].levels[2]
    assert (level3.status, level3.evidence_count, level3.proven_by_direct) == ("proven", 1, True)
    assert (
        after.skills[0].levels[2].status == "not_tested"
        and after.skills[0].levels[2].evidence_count == 0
    )


async def test_state_at_without_event_log_is_explicit(
    neo4j_driver: AsyncDriver, settings: Settings
) -> None:
    with pytest.raises(RuntimeError):
        await Reader(neo4j_driver, settings).state_at(uuid4(), 1, [])
