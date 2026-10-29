"""Mutation catalog of the projector (spec §4.2)."""

from __future__ import annotations

from dataclasses import dataclass

from quack.graph.types import Props


@dataclass(frozen=True)
class UpsertNode:
    """MERGE (n:Label {id: $id}) SET n += $props"""

    label: str
    id: str
    props: Props


@dataclass(frozen=True)
class UpsertRel:
    """MATCH (a:From {id}), (b:To {id}) MERGE (a)-[r:REL]->(b) SET r += $props"""

    from_label: str
    from_id: str
    rel_type: str
    to_label: str
    to_id: str
    props: Props


@dataclass(frozen=True)
class DeleteRel:
    from_label: str
    from_id: str
    rel_type: str
    to_label: str
    to_id: str


@dataclass(frozen=True)
class RebindNodeRel:
    """Re-point all subject/trace rels from source to target, mark source merged, add Alias."""

    label: str
    source_id: str
    target_id: str
    merged_at_iso: str


@dataclass(frozen=True)
class AdvanceWatermark:
    learner_id: str
    applied_seq: int
    projector_version: str


type Mutation = UpsertNode | UpsertRel | DeleteRel | RebindNodeRel | AdvanceWatermark
