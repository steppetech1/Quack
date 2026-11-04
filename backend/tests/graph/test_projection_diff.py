"""ТЗ Ф4 WP3.3: a batch writes only what changed (`Т-§8.1` ориентир) — the difference between
the slice read before phase A and the slice after it, in one Neo4j transaction (D-A-181)."""

from __future__ import annotations

from quack.graph.projection.mutations import DeleteNode, DeleteRel, MergeNode, MergeRel, SetProps
from quack.graph.projection.types import NodeRecord, RelRecord, SubGraph
from quack.graph.projection.view import BatchSlice

L = "learner"


def test_diff_has_new_changed_and_deleted_nodes_and_relations() -> None:
    loaded = SubGraph(
        nodes=[
            NodeRecord("Skill", "s1", {"learnerId": L, "name": "a", "status": "active"}),
            NodeRecord("Skill", "s2", {"learnerId": L, "name": "b", "status": "active"}),
            NodeRecord("Topic", "t1", {"learnerId": L, "name": "t"}),
        ],
        rels=[
            RelRecord("Topic", "t1", "COVERS", "Skill", "s1", {}),
            RelRecord(
                "Skill", "s1", "REQUIRES", "Skill", "s2", {"origin": "model", "confidence": 0.7}
            ),
        ],
    )
    batch = BatchSlice(L, loaded)
    batch.apply(
        [
            SetProps("Skill", "s1", {"name": "a", "status": "hidden"}),  # name unchanged
            MergeNode("Skill", "s3", {"learnerId": L, "name": "c"}),
            MergeRel("Topic", "t1", "COVERS", "Skill", "s3", {}),
            MergeRel(
                "Skill", "s1", "REQUIRES", "Skill", "s2", {"origin": "model", "confidence": 0.7}
            ),
            DeleteRel("Skill", "s1", "REQUIRES", "Skill", "s2"),
            DeleteNode("Skill", "s2"),
        ]
    )
    diff = batch.diff()
    assert diff.upsert_nodes == [
        NodeRecord("Skill", "s1", {"status": "hidden"}),
        NodeRecord("Skill", "s3", {"learnerId": L, "name": "c"}),
    ]
    assert diff.delete_nodes == [("Skill", "s2")]
    assert diff.upsert_rels == [RelRecord("Topic", "t1", "COVERS", "Skill", "s3", {})]
    assert diff.delete_rels == [RelRecord("Skill", "s1", "REQUIRES", "Skill", "s2", {})]


def test_a_batch_that_changes_nothing_has_an_empty_diff() -> None:
    loaded = SubGraph(nodes=[NodeRecord("Skill", "s1", {"learnerId": L, "name": "a"})])
    batch = BatchSlice(L, loaded)
    batch.apply([SetProps("Skill", "s1", {"name": "a"})])
    diff = batch.diff()
    assert (diff.upsert_nodes, diff.delete_nodes, diff.upsert_rels, diff.delete_rels) == (
        [], [], [], []
    )  # fmt: skip
