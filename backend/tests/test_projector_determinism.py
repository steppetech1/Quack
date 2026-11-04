"""Graph helpers that outlive the projector of Phase 3, and what the fold of Phase 4 owes them
(Т-§2.4, §2.5; D-A-228).

`InMemoryGraph` (the overlay and the reader's extras), the mutations and their Cypher compiler
(the seeds of the reader tests) keep their own tests. The determinism and equivalence of the
projector — batches of any size, rebuild, aliases, the rules guard — are checked on `rules@3` in
`graph/test_projection_batches.py` (T-P1, T-P2), `graph/test_projection_rules3.py` (T-P3–T-P5),
`graph/test_projection_runner.py` (T-P6, T-P8) and `test_projector_properties.py`.
"""

from __future__ import annotations

from uuid import uuid5

import pytest

from quack.graph.cypher import compile_mutations
from quack.graph.ids import CURRENT_RULES_VERSION, QUACK_UUID_NS, deterministic_id
from quack.graph.memory import InMemoryGraph
from quack.graph.mutations import (
    AdvanceWatermark,
    DeleteRel,
    RebindNodeRel,
    UpsertNode,
    UpsertRel,
)
from quack.graph.projection.misconception_fold import MisconceptionParams
from quack.graph.projection.phase_b import fold_dirty_misconceptions, fold_dirty_skills
from quack.graph.projection.skill_fold import FoldParams
from quack.graph.projection.types import NodeRecord, Props, RelRecord, SubGraph
from quack.graph.projection.view import BatchSlice

DAY = 24 * 3_600_000
SKILL, MISCONCEPTION = "s1", "m1"


def test_deterministic_id_is_the_uuid5_of_learner_seq_and_item() -> None:
    """Т-§2.5, spec §2.3: the id of a node a handler creates depends on the event only."""
    learner = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    assert deterministic_id(learner, 5, 0) == str(uuid5(QUACK_UUID_NS, f"{learner}:5:0"))
    assert deterministic_id(learner, 5, 0) == deterministic_id(learner, 5, "0")
    assert deterministic_id(learner, 5, 0) != deterministic_id(learner, 6, 0)
    assert deterministic_id(learner, 5, 0) != deterministic_id(learner, 5, 1)


def _evidence(name: str, outcome: str, status: str, at: int) -> NodeRecord:
    props = {
        "atMs": at, "sourceSeq": at, "level": 3, "outcome": outcome, "tier": 1,
        "verification": "code", "status": status,
    }  # fmt: skip
    return NodeRecord("Evidence", name, props)


def _fold(failure: str | None) -> tuple[Props | None, Props | None]:
    """The skill and the misconception after phase B: two active successes and, if given, a
    failure of the skill that manifests the misconception, in the given status."""
    nodes = [
        NodeRecord("Skill", SKILL, {"domainId": "d"}),
        NodeRecord("Misconception", MISCONCEPTION, {"status": "latent"}),
        _evidence("e1", "correct", "active", DAY),
        _evidence("e2", "correct", "active", 2 * DAY),
    ]
    rels = [RelRecord("Evidence", e, "ON", "Skill", SKILL, {}) for e in ("e1", "e2")]
    if failure is not None:
        nodes.append(_evidence("e3", "incorrect", failure, 2 * DAY + 1))
        rels.append(RelRecord("Evidence", "e3", "ON", "Skill", SKILL, {}))
        rels.append(RelRecord("Evidence", "e3", "MANIFESTS", "Misconception", MISCONCEPTION, {}))
    batch = BatchSlice("L", SubGraph(nodes=nodes, rels=rels))
    fold_dirty_skills(batch, {SKILL}, FoldParams())
    fold_dirty_misconceptions(batch, {MISCONCEPTION}, MisconceptionParams())
    return batch.node("Skill", SKILL), batch.node("Misconception", MISCONCEPTION)


def test_superseded_evidence_is_as_if_it_never_existed() -> None:
    """Т-§2.8 (carried over from the Phase 3 test of `evidence.superseded`, D-A-228): a
    superseded piece of evidence does not enter the fold of its skill or of its misconception;
    the same piece, active, would change both."""
    assert _fold("superseded") == _fold(None)
    skill, misconception = _fold("active")
    assert (skill, misconception) != _fold(None)
    assert skill != _fold(None)[0] and misconception != _fold(None)[1]


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
