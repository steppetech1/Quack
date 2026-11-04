"""WP6.4: `Neo4jGraphReader` core on a test Neo4j (D-A-140…D-A-143). T-TH4 (`test_read_functions`,
§8.4): `test_frontier`, `test_topic_readiness_direct_basis_only`, `test_why`,
`test_topic_requires_is_derived_from_skill_links`.

The data is written with the projector's mutations (`UpsertNode` / `UpsertRel`, compiled by
`compile_mutations`) in the `rules@3` shape of Т-§3.4. The tests never wipe the database:
each uses its own learner ids and removes only its own nodes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from neo4j import AsyncDriver, AsyncManagedTransaction

from quack.config import Settings
from quack.db.neo4j import literal
from quack.graph.cypher import compile_mutations
from quack.graph.memory import InMemoryGraph
from quack.graph.mutations import Mutation, UpsertNode, UpsertRel
from quack.graph.overlay import SessionOverlay
from quack.graph.read import SliceFocus, create_graph_reader
from quack.graph.read.models import ProfileFacts
from quack.graph.read.neo4j_reader import Neo4jGraphReader, level_is_ready_direct

NOW = 1_700_000_000_000
DAY = 86_400_000


def _id() -> str:
    return str(uuid4())


@dataclass
class World:
    learner: UUID
    session: UUID
    domain: str
    t1: str
    t2: str
    t3: str
    s1: str
    s2: str
    s3: str
    e1: str
    e2: str
    e3: str
    mis: str
    hub: str
    hub_all: str
    exposure: str
    ids: list[str]


def _node(label: str, node_id: str, learner: UUID, **props: object) -> UpsertNode:
    return UpsertNode(label, node_id, {"learnerId": str(learner), **props})  # type: ignore[arg-type]


def _rel(fl: str, fid: str, rt: str, tl: str, tid: str, **props: object) -> UpsertRel:
    return UpsertRel(fl, fid, rt, tl, tid, props)  # type: ignore[arg-type]


async def _write(driver: AsyncDriver, learner: UUID, mutations: list[Mutation]) -> None:
    # The Phase 3 store refuses int lists (`requiredLevels`) that rules@3 writes; the same
    # compiled statements go to the database directly.
    statements = compile_mutations(mutations)

    async def work(tx: AsyncManagedTransaction) -> None:
        for st in statements:
            await (await tx.run(literal(st.query), st.params)).consume()

    async with driver.session() as session:
        await session.execute_write(work)


async def _build(driver: AsyncDriver, seq: int = 10) -> World:
    learner, session = uuid4(), uuid4()
    w = World(learner, session, *(_id() for _ in range(14)), ids=[])
    w.ids = [str(learner)]
    domain, t1, t2, t3, s1, s2, s3, e1, e2, e3, mis, hub, hub_all, exposure = (
        w.domain, w.t1, w.t2, w.t3, w.s1, w.s2, w.s3, w.e1, w.e2, w.e3, w.mis, w.hub,
        w.hub_all, w.exposure,
    )  # fmt: skip
    skill1 = {
        "name": "ratio test", "status": "active", "domainId": domain, "l1Status": "implied",
        "l1Basis": "implied", "l3Status": "proven", "l3Basis": "direct",
        "l3DecisiveEvidenceId": e1, "l4Status": "failed", "l4Basis": "direct",
        "l4DecisiveEvidenceId": e2, "retState": "review", "retStability": 10.0,
        "retLastRecallAtMs": NOW - 10 * DAY, "retDueAtMs": NOW - DAY, "retLastLapseAtMs": 0,
        "gapType": "when_to_apply", "gapTypeConfidence": 0.8, "needsConsolidation": True,
    }  # fmt: skip
    skill2 = {
        "name": "limits", "status": "active", "domainId": domain, "l3Status": "proven",
        "l3Basis": "transfer", "retState": "none", "retStability": 0.0, "gapType": "none",
        "retLastLapseAtMs": NOW - 5 * DAY,
    }  # fmt: skip
    muts: list[Mutation] = [
        UpsertNode("Learner", str(learner), {"learnerId": str(learner), "projectedSeq": seq}),
        _node("Domain", domain, learner, name="Analysis", status="active"),
        _node("Topic", t1, learner, name="Series", domainId=domain, status="active"),
        _node("Topic", t2, learner, name="Limits", domainId=domain, status="active"),
        _node("Topic", t3, learner, name="Empty", domainId=domain, status="proposed"),
        _node("Skill", s1, learner, **skill1),
        _node("Skill", s2, learner, **skill2),
        _node("Skill", s3, learner, name="quiet", status="active", domainId=domain),
        _rel("Topic", t1, "COVERS", "Skill", s1),
        _rel("Topic", t2, "COVERS", "Skill", s2),
        _rel("Topic", t3, "COVERS", "Skill", s3),
        _rel("Skill", s1, "REQUIRES", "Skill", s2, origin="model", confidence=0.8),
        _node("Evidence", e1, learner, status="active", level=3, outcome="correct", tier=1,
              atMs=NOW - 10 * DAY, sourceSeq=3, quote="solved it", assistance="none",
              hintStep=0, sourceSessionId=""),
        _node("Evidence", e2, learner, status="active", level=4, outcome="incorrect", tier=1,
              atMs=NOW - 2 * DAY, sourceSeq=5, quote="root, so Cauchy", assistance="hint",
              hintStep=2, sourceSessionId=str(session)),
        _node("Evidence", e3, learner, status="active", level=3, outcome="incorrect", tier=3,
              atMs=NOW - DAY, sourceSeq=7, quote="", assistance="none", hintStep=0,
              sourceSessionId=""),
        _rel("Evidence", e1, "ON", "Skill", s1),
        _rel("Evidence", e2, "ON", "Skill", s1),
        _rel("Evidence", e3, "ON", "Skill", s1),
        _rel("Evidence", e2, "ROOT_CAUSE", "Skill", s2, confidence=0.5),
        _rel("Evidence", e3, "ROOT_CAUSE", "Skill", s2, confidence=0.5),
        _node("Misconception", mis, learner, status="confirmed", statement="takes root as Cauchy",
              triggers=["factorial"], targetedInSessionId=str(session)),
        _rel("Misconception", mis, "ABOUT", "Skill", s1),
        _rel("Misconception", mis, "CONFUSED_WITH", "Skill", s2),
        _rel("Evidence", e2, "MANIFESTS", "Misconception", mis),
        _node("Exposure", exposure, learner, atMs=NOW - 11 * DAY, kind="explained",
              approach="example_first", sourceSeq=2),
        _rel("Exposure", exposure, "OF", "Skill", s1),
        _node("Hub", hub, learner, domainId=domain, name="Exam", kind="from_request",
              origin="learner", controlType="tasks", deadlineAtMs=NOW + 3 * DAY, status="active"),
        _node("Hub", hub_all, learner, domainId=domain, name="Analysis", kind="domain_all",
              origin="learner", controlType="no_goal", deadlineAtMs=0, status="active"),
        _rel("Domain", domain, "HAS_HUB", "Hub", hub),
        _rel("Domain", domain, "HAS_HUB", "Hub", hub_all),
        _rel("Hub", hub, "INCLUDES", "Topic", t1, requiredLevels=[1, 3]),
        _rel("Hub", hub, "INCLUDES", "Topic", t2, requiredLevels=[3]),
    ]  # fmt: skip
    await _write(driver, learner, muts)
    return w


async def _drop(driver: AsyncDriver, *learners: UUID) -> None:
    async with driver.session() as session:
        await session.run(
            "MATCH (n) WHERE n.learnerId IN $ids DETACH DELETE n", ids=[str(x) for x in learners]
        )


@pytest.fixture
async def world(neo4j_driver: AsyncDriver) -> AsyncIterator[World]:
    w = await _build(neo4j_driver)
    yield w
    await _drop(neo4j_driver, w.learner)


@pytest.fixture
def reader(neo4j_driver: AsyncDriver, settings: Settings) -> Neo4jGraphReader:
    return Neo4jGraphReader(neo4j_driver, settings, clock=lambda: NOW)


async def test_slice_state_of_a_skill(world: World, reader: Neo4jGraphReader) -> None:
    focus = SliceFocus(skill_ids=[UUID(world.s1)])
    sl = await reader.slice_for_turn(world.learner, world.session, focus)
    assert (sl.projected_seq, sl.stable, sl.lag_degraded) == (10, True, False)
    [skill] = sl.skills
    assert (skill.name, skill.topic_name) == ("ratio test", "Series")
    assert skill.topic_id == UUID(world.t1)
    by = {lv.level: lv for lv in skill.levels}
    assert len(by) == 6
    assert (by[3].status, by[3].basis, by[3].proven_by_direct) == ("proven", "direct", True)
    assert by[3].deciding_evidence_id == UUID(world.e1)
    assert by[3].evidence_count == 2 and not by[3].last_t1_failed  # the T3 failure is not a T1
    assert (by[4].status, by[4].last_t1_failed) == ("failed", True)
    assert by[1].status == "implied" and by[1].basis is None
    assert by[2].status == "not_tested" and by[2].evidence_count == 0  # law 8
    assert skill.due_at_ms == NOW - DAY and skill.consolidation_flag
    assert skill.gap_type == "when_to_apply" and skill.gap_type_confidence == 0.8
    assert skill.last_lapse_at_ms is None
    # R(10 days, S = 10 days) = 0.9
    assert skill.recall_probability == pytest.approx(0.9, abs=0.01)
    [expo] = skill.exposures
    assert (expo.kind, expo.seq) == ("explained", 2)
    [link] = skill.requires
    assert (link.skill_id, link.confidence) == (UUID(world.s2), 0.8)
    assert link.has_evidence is False and link.has_exposure is False
    [mis] = sl.misconceptions
    assert mis.status == "in_work"  # confirmed + targeted in this session
    assert (mis.manifestation_count, mis.quotes) == (1, ["root, so Cauchy"])
    assert mis.confused_with_skill_id == UUID(world.s2) and mis.triggers == ["factorial"]
    [root] = sl.root_causes
    assert (root.skill_id, root.hypothesis_count) == (UUID(world.s2), 2)


async def test_slice_misconception_not_in_work_for_another_session(
    world: World, reader: Neo4jGraphReader
) -> None:
    sl = await reader.slice_for_turn(world.learner, uuid4(), SliceFocus(topic_ids=[UUID(world.t1)]))
    assert [s.skill_id for s in sl.skills] == [UUID(world.s1)]  # skills of the topic
    assert [m.status for m in sl.misconceptions] == ["confirmed"]


async def test_slice_unknown_ids_and_empty_focus(world: World, reader: Neo4jGraphReader) -> None:
    sl = await reader.slice_for_turn(
        world.learner, world.session, SliceFocus(skill_ids=[uuid4()], topic_ids=[uuid4()])
    )
    assert sl.skills == [] and sl.projected_seq == 10


async def test_learners_are_isolated(
    neo4j_driver: AsyncDriver, world: World, reader: Neo4jGraphReader
) -> None:
    other = await _build(neo4j_driver, seq=4)
    try:
        # a foreign skill id in the focus reads nothing of the first learner
        sl = await reader.slice_for_turn(
            other.learner, other.session, SliceFocus(skill_ids=[UUID(world.s1)])
        )
        assert sl.skills == [] and sl.misconceptions == []
        # a plant: a skill of the other learner required by a skill of the first one
        await _write(
            neo4j_driver,
            world.learner,
            [
                _rel(
                    "Skill", world.s1, "REQUIRES", "Skill", other.s1, origin="model", confidence=1.0
                )
            ],
        )
        mine = await reader.slice_for_turn(
            world.learner, world.session, SliceFocus(skill_ids=[UUID(world.s1)])
        )
        assert [r.skill_id for r in mine.skills[0].requires] == [UUID(world.s2)]
        assert [r.to_topic_id for r in await reader.topic_requires(UUID(world.domain))] == [
            UUID(world.t2)
        ]
        # the reader bound to the other learner does not see the first one's nodes by id
        bound = reader.for_learner(other.learner)
        assert (await bound.frontier(UUID(world.s1), [3])).level == 3  # open: nothing known
        assert (await reader.frontier(UUID(world.s1), [3])).level is None
        assert await bound.topic_memory([UUID(world.t1)]) == []
        assert await bound.recall_probability(UUID(world.s1), NOW) is None
        assert (await bound.slice_for_turn(world.learner, world.session, SliceFocus())).skills == []
    finally:
        await _drop(neo4j_driver, other.learner)


async def test_frontier(world: World, reader: Neo4jGraphReader) -> None:
    s1 = UUID(world.s1)
    assert (await reader.frontier(s1, [3])).level is None
    assert (await reader.frontier(s1, [4, 3])).level == 4  # sorted: 3 is proven, 4 failed
    assert (await reader.frontier(s1, [1, 3])).level is None  # 1 is implied
    assert (await reader.frontier(UUID(world.s3), [3, 2])).level == 2
    assert (await reader.frontier(s1, [])).level is None
    assert (await reader.frontier(uuid4(), [5, 2])).level == 2


async def test_frontier_follows_alias_and_overlay(
    neo4j_driver: AsyncDriver, world: World, reader: Neo4jGraphReader
) -> None:
    old = _id()
    await _write(
        neo4j_driver,
        world.learner,
        [
            _node("Alias", old, world.learner, kind="Skill", targetId=world.s1),
            _rel("Alias", old, "ALIAS_OF", "Skill", world.s1),
        ],
    )
    assert (await reader.frontier(UUID(old), [3])).level is None
    graph = InMemoryGraph()
    graph.nodes[("Skill", world.s1)] = {"id": world.s1, "l4Status": "proven"}
    overlay = SessionOverlay(applied_seq=10, overlay_seq=12, events=[], graph=graph)
    assert (await reader.frontier(UUID(world.s1), [3, 4], overlay)).level is None
    stale = SessionOverlay(applied_seq=10, overlay_seq=10, events=[], graph=graph)
    assert (await reader.frontier(UUID(world.s1), [3, 4], stale)).level == 4


async def test_topic_readiness_direct_basis_only(world: World, reader: Neo4jGraphReader) -> None:
    r = await reader.topic_readiness(UUID(world.hub))
    # T1: level 3 proven direct, level 1 implied by it; T2: level 3 proven by transfer only
    assert r.ready_topic_ids == [UUID(world.t1)]
    assert r.not_ready_topic_ids == [UUID(world.t2)]
    assert (r.ready_count, r.total) == (1, 2)
    domain_all = await reader.topic_readiness(UUID(world.hub_all))
    # `no_goal` asks for level 3: only T1 has it proven directly
    assert domain_all.ready_topic_ids == [UUID(world.t1)]
    assert sorted(domain_all.not_ready_topic_ids) == sorted([UUID(world.t2), UUID(world.t3)])
    assert (await reader.topic_readiness(uuid4())).total == 0


def test_implied_level_needs_a_direct_source() -> None:
    impl = {3: [1], 4: [3, 1]}
    props = {"l3Status": "proven", "l3Basis": "direct", "l1Status": "implied"}
    assert level_is_ready_direct(props, 1, impl)
    assert not level_is_ready_direct({**props, "l3Basis": "transfer"}, 1, impl)
    assert not level_is_ready_direct({"l1Status": "implied"}, 1, impl)
    assert not level_is_ready_direct({"l3Status": "unstable", "l3Basis": "direct"}, 3, impl)


async def test_why(world: World, reader: Neo4jGraphReader) -> None:
    w = await reader.why(UUID(world.t1), 3)
    assert w.level == 3
    assert w.deciding is not None and w.deciding.evidence_id == UUID(world.e1)
    assert (w.deciding.source, w.deciding.quote) == ("code", "solved it")
    assert w.deciding.task_id is None and w.deciding.session_id is None
    assert [e.evidence_id for e in w.recent] == [UUID(world.e3), UUID(world.e1)]
    assert w.recent[0].source == "conversation"
    w4 = await reader.why(UUID(world.t1), 4)
    assert w4.deciding is not None and w4.deciding.hint_step == 2
    assert (w4.deciding.assistance, w4.deciding.session_id) == ("hint", world.session)
    anyl = await reader.why(UUID(world.t1))
    assert anyl.level is None and anyl.deciding is None
    assert [e.evidence_id for e in anyl.recent] == [UUID(world.e3), UUID(world.e2), UUID(world.e1)]
    unknown = await reader.why(uuid4(), 3)
    assert unknown.deciding is None and unknown.recent == []


async def test_topic_requires_is_derived_from_skill_links(
    neo4j_driver: AsyncDriver, world: World, reader: Neo4jGraphReader
) -> None:
    s4 = _id()
    await _write(
        neo4j_driver,
        world.learner,
        [
            _node("Skill", s4, world.learner, name="s4", status="active", domainId=world.domain),
            _rel("Topic", world.t1, "COVERS", "Skill", s4),
            _rel("Skill", s4, "REQUIRES", "Skill", world.s2, origin="model", confidence=0.7),
            _rel("Skill", world.s1, "REQUIRES", "Skill", s4, origin="model", confidence=0.7),
        ],
    )
    [link] = await reader.topic_requires(UUID(world.domain))
    assert (link.from_topic_id, link.to_topic_id, link.weight) == (
        UUID(world.t1),
        UUID(world.t2),
        2,  # s1 -> s2 and s4 -> s2; s1 -> s4 stays inside the topic
    )
    assert await reader.topic_requires(uuid4()) == []


async def test_recall_probability(world: World, reader: Neo4jGraphReader) -> None:
    p = await reader.recall_probability(UUID(world.s1), NOW)
    assert p == pytest.approx(0.9, abs=0.01)
    later = await reader.recall_probability(UUID(world.s1), NOW + 100 * DAY)
    assert later is not None and later < p  # type: ignore[operator]
    assert await reader.recall_probability(UUID(world.s2), NOW) is None  # no retention card
    assert await reader.recall_probability(UUID(world.s3), NOW) is None
    assert await reader.recall_probability(uuid4(), NOW) is None


async def test_domain_goals(world: World, reader: Neo4jGraphReader) -> None:
    goals = await reader.domain_goals(UUID(world.domain))
    assert [g.name for g in goals] == ["Exam", "Analysis"]  # the deadline first
    exam, whole = goals
    assert (exam.control_type, exam.deadline_at_ms) == ("tasks", NOW + 3 * DAY)
    assert {(t.name, tuple(t.required_levels)) for t in exam.topics} == {
        ("Series", (1, 3)),
        ("Limits", (3,)),
    }
    assert whole.kind == "domain_all" and whole.deadline_at_ms is None
    assert {t.name for t in whole.topics} == {"Series", "Limits", "Empty"}
    assert all(t.required_levels == [3] for t in whole.topics)  # `no_goal` default
    assert await reader.domain_goals(uuid4()) == []


async def test_topic_memory(world: World, reader: Neo4jGraphReader) -> None:
    got = await reader.topic_memory([UUID(world.t2), uuid4(), UUID(world.t1), UUID(world.t2)])
    assert [t.name for t in got] == ["Limits", "Series"]  # order kept, unknown skipped, no dups
    series = got[1]
    assert series.domain_id == UUID(world.domain)
    assert [s.skill_id for s in series.skills] == [UUID(world.s1)]
    assert [m.status for m in series.misconceptions] == ["confirmed"]


async def test_overlay_patches_state_while_the_projector_lags(
    world: World, neo4j_driver: AsyncDriver, settings: Settings
) -> None:
    fast = settings.model_copy(update={"projector_lag_timeout_ms": 50})
    reader = Neo4jGraphReader(neo4j_driver, fast, clock=lambda: NOW)
    new_ev, new_x = _id(), _id()
    graph = InMemoryGraph()
    graph.nodes[("Skill", world.s1)] = {"id": world.s1, "l4Status": "proven", "l4Basis": "direct"}
    graph.nodes[("Evidence", new_ev)] = {
        "id": new_ev, "status": "active", "level": 4, "tier": 1, "outcome": "correct",
        "atMs": NOW, "sourceSeq": 11,
    }  # fmt: skip
    graph.nodes[("Exposure", new_x)] = {
        "id": new_x,
        "kind": "example",
        "atMs": NOW,
        "sourceSeq": 12,
    }
    graph.nodes[("Evidence", world.e1)] = {"id": world.e1, "status": "active", "level": 3}
    graph.rels[("Evidence", new_ev, "ON", "Skill", world.s1)] = {}
    graph.rels[("Evidence", world.e1, "ON", "Skill", world.s1)] = {}  # already projected
    graph.rels[("Exposure", new_x, "OF", "Skill", world.s1)] = {}
    overlay = SessionOverlay(applied_seq=10, overlay_seq=12, events=[], graph=graph)
    focus = SliceFocus(skill_ids=[UUID(world.s1)])
    sl = await reader.slice_for_turn(world.learner, world.session, focus, overlay)
    assert sl.lag_degraded and sl.projected_seq == 10
    by = {lv.level: lv for lv in sl.skills[0].levels}
    assert by[4].status == "proven"
    assert by[4].evidence_count == 2  # one projected, one only in the overlay
    assert by[3].evidence_count == 2  # e1 is not counted twice
    assert not by[4].last_t1_failed  # the newer T1 of the overlay is a success
    assert [x.kind for x in sl.skills[0].exposures] == ["example", "explained"]
    # without the overlay the same slice shows the projected state and is not degraded
    plain = await reader.slice_for_turn(world.learner, world.session, focus)
    assert not plain.lag_degraded
    assert {lv.level: lv for lv in plain.skills[0].levels}[4].status == "failed"


async def test_the_projector_catching_up_ends_the_wait(
    world: World, neo4j_driver: AsyncDriver, settings: Settings
) -> None:
    reader = Neo4jGraphReader(neo4j_driver, settings, clock=lambda: NOW)
    overlay = SessionOverlay(applied_seq=5, overlay_seq=10, events=[], graph=InMemoryGraph())
    sl = await reader.slice_for_turn(world.learner, world.session, SliceFocus(), overlay)
    assert not sl.lag_degraded  # projectedSeq = 10 has reached the tail


async def test_unstable_watermark_is_marked_after_the_retries(
    world: World, reader: Neo4jGraphReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def moving(_learner: UUID) -> int:
        calls["n"] += 1
        return calls["n"]

    monkeypatch.setattr(reader, "_projected_seq", moving)
    sl = await reader.slice_for_turn(
        world.learner, world.session, SliceFocus(skill_ids=[UUID(world.s1)])
    )
    assert sl.stable is False and len(sl.skills) == 1
    assert calls["n"] == 2 * reader._settings.snapshot_read_retries  # pyright: ignore[reportPrivateUsage]


async def test_watermark_falls_back_to_applied_seq(
    neo4j_driver: AsyncDriver, reader: Neo4jGraphReader
) -> None:
    learner = uuid4()
    await _write(
        neo4j_driver,
        learner,
        [UpsertNode("Learner", str(learner), {"learnerId": str(learner), "appliedSeq": 7})],
    )
    try:
        sl = await reader.slice_for_turn(learner, uuid4(), SliceFocus())
        assert sl.projected_seq == 7
    finally:
        await _drop(neo4j_driver, learner)


async def test_factory_reader_is_bound_to_its_learner(
    settings: Settings, neo4j_driver: AsyncDriver, world: World
) -> None:
    """C's blocker on #175 (law 1): a reader from the factory with learner B does not return
    the nodes of learner A by their ids — for every method that starts from an id."""
    other = await _build(neo4j_driver, seq=4)
    try:
        mine = create_graph_reader(neo4j_driver, settings, learner_id=other.learner)
        assert await mine.topic_memory([UUID(world.t1)]) == []
        assert await mine.topic_requires(UUID(world.domain)) == []
        assert await mine.recall_probability(UUID(world.s1), 1) is None
        assert await mine.domain_goals(UUID(world.domain)) == []
        assert (await mine.topic_readiness(UUID(world.hub))).total == 0
        own = create_graph_reader(neo4j_driver, settings, learner_id=world.learner)
        assert [m.topic_id for m in await own.topic_memory([UUID(world.t1)])] == [UUID(world.t1)]
    finally:
        await _drop(neo4j_driver, other.learner)


async def test_factory_and_extras(settings: Settings, neo4j_driver: AsyncDriver) -> None:
    reader = create_graph_reader(neo4j_driver, settings, learner_id=uuid4())
    assert isinstance(reader, Neo4jGraphReader)
    other = uuid4()
    assert await reader.profile(other) == ProfileFacts(learner_id=other)  # bound: law 1


async def test_factory_reader_does_not_leak_through_extra_reads(
    settings: Settings, neo4j_driver: AsyncDriver, world: World
) -> None:
    """D-A-144 for the reads of `ExtraReads`: a reader of learner B, asked by ids and learner
    ids of learner A, answers nothing."""
    other = await _build(neo4j_driver, seq=4)
    try:
        mine = create_graph_reader(neo4j_driver, settings, learner_id=other.learner)
        assert await mine.skill_history(UUID(world.s1)) == []
        assert await mine.prerequisites(UUID(world.s1)) == []
        assert await mine.misconception_detail(UUID(world.mis)) is None
        assert await mine.review_candidates(world.learner, 1) == []
        assert await mine.past_solution(world.learner) == []
        assert (
            await mine.extraction_candidates(world.learner, world.session, None, 5)
        ).skills == []
        own = create_graph_reader(neo4j_driver, settings, learner_id=world.learner)
        assert await own.skill_history(UUID(world.s1)) != []
    finally:
        await _drop(neo4j_driver, other.learner)
