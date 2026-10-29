"""Plain graph records exchanged between the projector, stores and readers."""

from __future__ import annotations

from dataclasses import dataclass, field

type PropValue = str | int | float | bool | list[str]
type Props = dict[str, PropValue]
type NodeKey = tuple[str, str]  # (label, id)
type RelKey = tuple[str, str, str, str, str]  # (from_label, from_id, type, to_label, to_id)


@dataclass(frozen=True)
class NodeRecord:
    label: str
    id: str
    props: Props


@dataclass(frozen=True)
class RelRecord:
    from_label: str
    from_id: str
    rel_type: str
    to_label: str
    to_id: str
    props: Props


@dataclass
class SubGraph:
    nodes: list[NodeRecord] = field(default_factory=list)
    rels: list[RelRecord] = field(default_factory=list)


@dataclass(frozen=True)
class LearnerWatermark:
    applied_seq: int
    projector_version: str


@dataclass
class BatchNeed:
    """What Query 1 must load before Phase A (spec §4.3)."""

    learner_id: str
    node_ids: set[str] = field(default_factory=set)
    domain_ids: set[str] = field(default_factory=set)
    superseded_seqs: set[int] = field(default_factory=set)


# Relationship types re-pointed by RebindNodeRel (spec §4.2).
MERGE_REL_TYPES: tuple[str, ...] = (
    "HAS_TOPIC",
    "PREREQ_OF",
    "PART_OF",
    "DEPENDS_ON",
    "ABOUT_SKILL",
    "TESTS",
    "EVIDENCE_FOR",
    "EXPOSED_TO",
    "OBSERVED_MISCONCEPTION",
    "TRIGGERS_MISCONCEPTION",
    "HAS_ARTIFACT",
)

NODE_LABELS: tuple[str, ...] = (
    "Learner",
    "Domain",
    "Topic",
    "Skill",
    "Misconception",
    "Task",
    "Evidence",
    "Exposure",
    "Artifact",
    "Alias",
)
