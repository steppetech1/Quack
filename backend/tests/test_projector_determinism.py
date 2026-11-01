"""Projector determinism (Т-§2.4, §2.5, §2.7, spec §4, §9.1): batch-size independence,
rebuild equivalence, deterministic ids, Alias resolution, superseded evidence, rules@N."""

from __future__ import annotations

import random
from uuid import UUID

import pytest
from neo4j import AsyncDriver

from quack.config import FoldParams
from quack.events.catalog import EVENT_TYPES
from quack.events.models import StoredEvent
from quack.graph.cypher import compile_mutations
from quack.graph.ids import CURRENT_RULES_VERSION, deterministic_id, rules_version_number
from quack.graph.memory import InMemoryGraph
from quack.graph.mutations import (
    AdvanceWatermark,
    DeleteRel,
    RebindNodeRel,
    UpsertNode,
    UpsertRel,
)
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import (
    analyze_batch,
    project_in_memory,
    rules_mismatch,
    run_projector_batch,
)
from quack.graph.types import MERGE_REL_TYPES
from tests.factories import EventLog
from tests.scenario import build_scenario, s

P = FoldParams()


async def apply_in_chunks(
    store: InMemoryGraph | Neo4jGraphStore, learner: UUID, events: list[StoredEvent], sizes
) -> None:
    i = 0
    for size in sizes:
        if i >= len(events):
            break
        outcome = await run_projector_batch(learner, events[i : i + size], store, P)
        assert outcome.status == "applied"
        i += size
    assert i >= len(events)


def test_scenario_covers_all_36_event_types() -> None:
    log, _ = build_scenario()
    assert {e.type for e in log.events} == set(EVENT_TYPES)


async def test_batch_size_and_rebuild_equivalence() -> None:
    log, _ = build_scenario()
    learner, events = log.learner_id, log.events

    by_one = InMemoryGraph()
    await apply_in_chunks(by_one, learner, events, [1] * len(events))

    one_batch = InMemoryGraph()
    await apply_in_chunks(one_batch, learner, events, [len(events)])

    rng = random.Random(7)
    random_sizes = InMemoryGraph()
    await apply_in_chunks(random_sizes, learner, events, [rng.randint(1, 9) for _ in events])

    rebuilt = await project_in_memory(learner, events, P, batch_size=P_BATCH)

    snapshot = by_one.snapshot()
    assert snapshot == one_batch.snapshot()
    assert snapshot == random_sizes.snapshot()
    assert snapshot == rebuilt.snapshot()

    learner_node = by_one.get("Learner", str(learner))
    assert learner_node is not None
    assert learner_node["appliedSeq"] == events[-1].seq
    assert learner_node["projectorVersion"] == CURRENT_RULES_VERSION
    assert learner_node["learnerId"] == str(learner)


P_BATCH = 100


def _merged_skill_sublog() -> tuple[UUID, list[StoredEvent]]:
    """`structure.proposed`, `skill.merged` and a `task.evaluated` for the merged skill, numbered
    without gaps — the counterexample the property test found."""
    log, _ = build_scenario()
    picked = [
        next(e for e in log.events if e.type == "structure.proposed"),
        next(e for e in log.events if e.type == "skill.merged"),
    ]
    merged_from = picked[1].payload["source_skill_id"]
    picked.append(
        next(
            e
            for e in log.events
            if e.type == "task.evaluated"
            and any(x["skill_id"] == merged_from for x in e.payload["skill_scores"])
        )
    )
    return log.learner_id, [e.model_copy(update={"seq": i}) for i, e in enumerate(picked, 1)]


async def test_merged_skill_evidence_refreshes_every_topic_of_the_target() -> None:
    """Found by the property test (Т-§2.5): after `skill.merged` the target skill is PART_OF its
    own topic and the topic of the merged skill. Evidence for the merged skill in a later batch
    must refresh both topics, as a rebuild in one pass does."""
    learner, events = _merged_skill_sublog()
    by_one = InMemoryGraph()
    await apply_in_chunks(by_one, learner, events, [1] * len(events))
    rebuilt = await project_in_memory(learner, events, P, batch_size=len(events))
    assert by_one.snapshot() == rebuilt.snapshot()


async def test_neo4j_merged_skill_evidence_refreshes_every_topic(neo4j: AsyncDriver) -> None:
    learner, events = _merged_skill_sublog()
    expected = await project_in_memory(learner, events, P, batch_size=len(events))
    store = Neo4jGraphStore(neo4j, str(learner))
    await apply_in_chunks(store, learner, events, [1] * len(events))
    assert (await store.dump()).snapshot() == expected.snapshot()


async def test_reapplying_already_applied_events_changes_nothing() -> None:
    log, _ = build_scenario()
    graph = InMemoryGraph()
    await apply_in_chunks(graph, log.learner_id, log.events, [len(log.events)])
    before = graph.snapshot()
    outcome = await run_projector_batch(log.learner_id, log.events[5:20], graph, P)
    assert outcome.status == "empty"
    assert graph.snapshot() == before


async def test_projection_is_reproducible_across_runs() -> None:
    log, _ = build_scenario()
    a = await project_in_memory(log.learner_id, log.events, P, batch_size=3)
    b = await project_in_memory(log.learner_id, log.events, P, batch_size=50)
    assert a.snapshot() == b.snapshot()


async def test_deterministic_uuid5_ids() -> None:
    log, ids = build_scenario()
    graph = await project_in_memory(log.learner_id, log.events, P, batch_size=10)
    L = log.learner_id
    evaluated = [e for e in log.events if e.type == "task.evaluated"]
    for event in evaluated:
        for i in range(len(event.payload["skill_scores"])):
            assert graph.get("Evidence", deterministic_id(L, event.seq, i)) is not None
    explained = next(e for e in log.events if e.type == "teacher.concept_explained")
    assert graph.get("Exposure", deterministic_id(L, explained.seq, 0)) is not None
    chain = graph.get("Skill", ids.chain_rule_skill)
    assert chain is not None
    assert chain["normName"] == "chain rule"
    assert chain["origin"] == "extracted"
    assert chain["status"] == "active"  # promoted later
    product = graph.get("Skill", ids.product_rule_skill)
    assert product is not None and product["status"] == "provisional"
    assert deterministic_id(L, 5, 0) == deterministic_id(str(L), 5, "0")


async def test_inline_provisional_skills_reuse_by_normalized_name() -> None:
    log, ids = build_scenario()
    graph = await project_in_memory(log.learner_id, log.events, P, batch_size=1)
    chain_like = [
        sid
        for sid, props in graph.nodes_with_label("Skill")
        if props.get("normName") == "chain rule"
    ]
    assert chain_like == [ids.chain_rule_skill]
    task_a = s(ids.task_a)
    assert ("Task", task_a, "TESTS", "Skill", ids.chain_rule_skill) in graph.rels
    # Extracted misconception "sign ERROR" reuses the drafted "Sign error".
    sign_like = [
        mid
        for mid, props in graph.nodes_with_label("Misconception")
        if props.get("normName") == "sign error"
    ]
    assert sign_like == [s(ids.m_sign)]


async def test_alias_resolution_after_merges() -> None:
    log, ids = build_scenario()
    graph = await project_in_memory(log.learner_id, log.events, P, batch_size=4)
    src_skill, tgt_skill = s(ids.s_dup), s(ids.s_power)
    src_topic, tgt_topic = s(ids.t_dup), s(ids.t_deriv)

    for label, src, tgt in (
        ("Skill", src_skill, tgt_skill),
        ("Topic", src_topic, tgt_topic),
        ("Misconception", ids.extracted_misc, s(ids.m_sign)),
    ):
        node = graph.get(label, src)
        assert node is not None and node["status"] == "merged", label
        alias = graph.get("Alias", src)
        assert alias is not None and alias["kind"] == label and alias["targetId"] == tgt
        assert ("Alias", src, "ALIAS_OF", label, tgt) in graph.rels
        for rel_type in MERGE_REL_TYPES:
            assert graph.out_rels(label, src, rel_type) == [], (label, rel_type)
            assert graph.in_rels(label, src, rel_type) == [], (label, rel_type)

    # Events after the merge that reference old ids land on the targets.
    last_eval = [e for e in log.events if e.type == "task.evaluated"][-1]
    evidence_id = deterministic_id(log.learner_id, last_eval.seq, 0)
    assert ("Evidence", evidence_id, "EVIDENCE_FOR", "Skill", tgt_skill) in graph.rels
    task_c = graph.get("Task", s(ids.task_c))
    assert task_c is not None and task_c["topicId"] == tgt_topic
    assert ("Task", s(ids.task_c), "TESTS", "Skill", tgt_skill) in graph.rels
    manifest = [k for k in graph.in_rels("Misconception", s(ids.m_sign), "OBSERVED_MISCONCEPTION")]
    assert any(k[1] == deterministic_id(log.learner_id, last_eval.seq, "hit:0") for k in manifest)
    # Skills of the merged topic now belong to the target topic.
    product = graph.get("Skill", ids.product_rule_skill)
    assert product is not None and product["topicId"] == tgt_topic
    assert ("Skill", ids.product_rule_skill, "PART_OF", "Topic", tgt_topic) in graph.rels
    assert ("Domain", s(ids.domain), "HAS_TOPIC", "Topic", tgt_topic) in graph.rels
    note = s(ids.note)
    assert ("Topic", tgt_topic, "HAS_ARTIFACT", "Artifact", note) in graph.rels
    # No self loops after the merge.
    assert not [k for k in graph.rels if k[1] == k[4] and k[2] != "ALIAS_OF"]


async def test_skill_merge_moves_history_into_target_fold() -> None:
    log, ids = build_scenario()
    graph = await project_in_memory(log.learner_id, log.events, P, batch_size=2)
    power = graph.get("Skill", s(ids.s_power))
    assert power is not None
    evidence_rels = graph.in_rels("Skill", s(ids.s_power), "EVIDENCE_FOR")
    active = [
        k
        for k in evidence_rels
        if graph.get("Evidence", k[1]) and not graph.get("Evidence", k[1])["superseded"]  # type: ignore[index]
    ]
    assert power["evidenceCount"] == len(active)
    assert power["distinctFamilies"] == 3  # power-a, power-b, power-c
    assert power["exposureCount"] == 1


async def test_superseded_evidence_equals_history_without_it() -> None:
    with_bad, ids = build_scenario(include_superseded_eval=True)
    without = EventLog(learner_id=with_bad.learner_id)
    _, ids2 = build_scenario(without, include_superseded_eval=False)
    a = await project_in_memory(with_bad.learner_id, with_bad.events, P, batch_size=1)
    # Ids differ between the two builds; compare skill folds by structural id.
    b = await project_in_memory(without.learner_id, without.events, P, batch_size=1)
    pa, pb = a.get("Skill", s(ids.s_power)), b.get("Skill", s(ids2.s_power))
    assert pa is not None and pb is not None
    for key in ("pMastery", "confidence", "evidenceCount", "distinctFamilies", "masteryStatus"):
        assert pa[key] == pb[key], key
    bad_evidence = a.get(
        "Evidence", deterministic_id(with_bad.learner_id, ids.superseded_eval_seq, 0)
    )
    assert bad_evidence is not None and bad_evidence["superseded"] is True
    ma, mb = a.get("Misconception", s(ids.m_sign)), b.get("Misconception", s(ids2.m_sign))
    assert ma is not None and mb is not None
    assert ma["occurrences"] == mb["occurrences"]
    assert ma["status"] == mb["status"] == "resolved"


async def test_topic_aggregates_present_after_projection() -> None:
    log, ids = build_scenario()
    graph = await project_in_memory(log.learner_id, log.events, P, batch_size=5)
    deriv = graph.get("Topic", s(ids.t_deriv))
    limits = graph.get("Topic", s(ids.t_limits))
    assert deriv is not None and limits is not None
    for props in (deriv, limits):
        for key in ("mastery", "coverage", "confidence", "hubScore", "displayState", "skillCount"):
            assert key in props
        assert props["displayState"] in {"locked", "available", "in_progress", "solid", "rusty"}
    assert limits["name"] == "Limits & continuity"
    assert limits["hubScore"] == 2  # power rule and continuity depend on limit laws
    assert deriv["displayState"] == "in_progress"


async def test_rules_guard_skips_learner_until_rebuild() -> None:
    assert rules_version_number("rules@12") == 12
    with pytest.raises(ValueError):
        rules_version_number("rules12")
    assert not rules_mismatch(None)
    assert not rules_mismatch(CURRENT_RULES_VERSION)
    assert rules_mismatch("rules@0")
    assert rules_mismatch("rules@999")

    log, _ = build_scenario()
    for stale in ("rules@0", f"rules@{rules_version_number(CURRENT_RULES_VERSION) + 1}"):
        graph = InMemoryGraph()
        await graph.apply([AdvanceWatermark(str(log.learner_id), 0, stale)])
        before = graph.snapshot()
        outcome = await run_projector_batch(log.learner_id, log.events, graph, P)
        assert outcome.status == "skipped_rules"
        assert outcome.mutation_count == 0
        assert graph.snapshot() == before

    fresh = InMemoryGraph()
    outcome = await run_projector_batch(log.learner_id, log.events[:3], fresh, P)
    assert outcome.status == "applied" and outcome.applied_seq == 3


def test_analyze_batch_collects_needed_ids() -> None:
    log, ids = build_scenario()
    need = analyze_batch(log.learner_id, log.events)
    assert need.learner_id == str(log.learner_id)
    assert s(ids.domain) in need.domain_ids
    for node in (ids.s_dup, ids.t_dup, ids.task_c, ids.m_sign, ids.note):
        assert s(node) in need.node_ids
    assert ids.superseded_eval_seq in need.superseded_seqs


async def test_memory_mutation_semantics() -> None:
    g = InMemoryGraph()
    await g.apply(
        [
            UpsertNode("Skill", "a", {"learnerId": "L", "name": "A"}),
            UpsertNode("Skill", "a", {"status": "active"}),
            UpsertRel("Skill", "a", "DEPENDS_ON", "Skill", "missing", {}),
            UpsertNode("Skill", "b", {"learnerId": "L"}),
            UpsertRel("Skill", "a", "DEPENDS_ON", "Skill", "b", {"w": 1}),
            UpsertRel("Skill", "a", "DEPENDS_ON", "Skill", "b", {"x": 2}),
        ]
    )
    # MERGE (n:Label {id: $id}) stores `id` as a property (spec §4.2).
    assert g.get("Skill", "a") == {"id": "a", "learnerId": "L", "name": "A", "status": "active"}
    assert g.rels == {("Skill", "a", "DEPENDS_ON", "Skill", "b"): {"w": 1, "x": 2}}
    await g.apply([DeleteRel("Skill", "a", "DEPENDS_ON", "Skill", "b")])
    assert g.rels == {}
    with pytest.raises(ValueError):
        g.apply_mutation(UpsertNode("Skill", "a", {"bad": None}))  # type: ignore[dict-item]


async def test_rebind_semantics_no_duplicates_no_loops() -> None:
    g = InMemoryGraph()
    await g.apply(
        [
            UpsertNode("Skill", "src", {"learnerId": "L", "status": "active"}),
            UpsertNode("Skill", "tgt", {"learnerId": "L", "status": "active"}),
            UpsertNode("Skill", "x", {"learnerId": "L"}),
            UpsertNode("Topic", "t", {"learnerId": "L"}),
            UpsertNode("Evidence", "e1", {"learnerId": "L"}),
            UpsertRel("Skill", "src", "PART_OF", "Topic", "t", {"k": "src"}),
            UpsertRel("Skill", "tgt", "PART_OF", "Topic", "t", {"k": "tgt"}),
            UpsertRel("Skill", "src", "DEPENDS_ON", "Skill", "tgt", {}),
            UpsertRel("Skill", "x", "DEPENDS_ON", "Skill", "src", {"c": 1}),
            UpsertRel("Evidence", "e1", "EVIDENCE_FOR", "Skill", "src", {}),
            RebindNodeRel("Skill", "src", "tgt", "2026-10-01T00:00:00+00:00"),
        ]
    )
    assert g.rels[("Skill", "tgt", "PART_OF", "Topic", "t")] == {"k": "tgt"}
    assert ("Skill", "x", "DEPENDS_ON", "Skill", "tgt") in g.rels
    assert ("Evidence", "e1", "EVIDENCE_FOR", "Skill", "tgt") in g.rels
    assert ("Alias", "src", "ALIAS_OF", "Skill", "tgt") in g.rels
    assert not [k for k in g.rels if "src" in (k[1], k[4]) and k[0] != "Alias"]
    src = g.get("Skill", "src")
    assert src is not None and src["status"] == "merged" and src["mergedInto"] == "tgt"
    assert g.alias_map() == {"src": "tgt"}
    # Chained merge re-points older aliases.
    await g.apply(
        [
            UpsertNode("Skill", "final", {"learnerId": "L", "status": "active"}),
            RebindNodeRel("Skill", "tgt", "final", "2026-10-02T00:00:00+00:00"),
        ]
    )
    assert g.alias_map() == {"src": "final", "tgt": "final"}


def test_cypher_compiler_shapes() -> None:
    statements = compile_mutations(
        [
            UpsertNode("Skill", "a", {"learnerId": "L"}),
            UpsertNode("Skill", "b", {"learnerId": "L"}),
            UpsertRel("Skill", "a", "DEPENDS_ON", "Skill", "b", {}),
            DeleteRel("Skill", "a", "DEPENDS_ON", "Skill", "b"),
            RebindNodeRel("Skill", "a", "b", "2026-10-01T00:00:00+00:00"),
            AdvanceWatermark("L", 3, CURRENT_RULES_VERSION),
        ]
    )
    joined = "\n".join(st.query for st in statements)
    assert "MERGE (n:Skill {id: row.id})" in joined
    assert "UNWIND $rows AS row" in joined
    assert "DEPENDS_ON" in joined and "DELETE" in joined and "ALIAS_OF" in joined
    assert "appliedSeq" in statements[-1].query
    first = statements[0]
    assert len(first.params["rows"]) == 2  # consecutive same-shape upserts share UNWIND
    for st in statements:
        assert "$" in st.query  # parameterized, never inlined values


# --- Neo4j: the Cypher store gives exactly the in-memory result ------------------------


async def test_neo4j_projection_equals_in_memory(neo4j: AsyncDriver) -> None:
    log, _ = build_scenario()
    learner, events = log.learner_id, log.events
    expected = await project_in_memory(learner, events, P, batch_size=100)

    store = Neo4jGraphStore(neo4j, str(learner))
    await apply_in_chunks(store, learner, events, [1] * len(events))
    assert (await store.dump()).snapshot() == expected.snapshot()

    deleted = await store.delete_learner(chunk_size=50)
    assert deleted > 0
    assert (await store.dump()).snapshot() == InMemoryGraph().snapshot()
    await apply_in_chunks(store, learner, events, [17] * len(events))
    assert (await store.dump()).snapshot() == expected.snapshot()
    wm = await store.load_watermark(str(learner))
    assert wm is not None and wm.applied_seq == events[-1].seq


async def test_neo4j_rules_guard(neo4j: AsyncDriver) -> None:
    log, _ = build_scenario()
    store = Neo4jGraphStore(neo4j, str(log.learner_id))
    await store.apply([AdvanceWatermark(str(log.learner_id), 0, "rules@0")])
    outcome = await run_projector_batch(log.learner_id, log.events, store, P)
    assert outcome.status == "skipped_rules"
    dump = await store.dump()
    assert [label for label, _ in dump.nodes] == ["Learner"]
