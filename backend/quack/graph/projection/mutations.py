"""Typed graph changes of `rules@3` (Т-§2.5): one layer applies them, to Neo4j or to memory.

`MergeNode` creates the node if it is missing and sets `props`; `SetProps` changes an existing
node; `MergeRel` / `DeleteRel` — one relationship; `DeleteNode` removes a node with its
relationships (the old node of a merge, Т-§2.8). Neo4j properties are `camelCase` (Т-§0.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quack.graph.projection.types import Props


@dataclass(frozen=True)
class MergeNode:
    label: str
    id: str
    props: Props


@dataclass(frozen=True)
class SetProps:
    label: str
    id: str
    props: Props


@dataclass(frozen=True)
class MergeRel:
    from_label: str
    from_id: str
    rel_type: str
    to_label: str
    to_id: str
    props: Props


@dataclass(frozen=True)
class DeleteRel:
    """`key_props` — the identity props of the type (`REL_IDENTITY`), e.g. `{"level": 3}`."""

    from_label: str
    from_id: str
    rel_type: str
    to_label: str
    to_id: str
    key_props: Props = field(default_factory=dict)


@dataclass(frozen=True)
class DeleteNode:
    label: str
    id: str


@dataclass(frozen=True)
class RemoveProps:
    """Properties that are «no data» now (`learned*` of the profile, WP5.3): absent, not null."""

    label: str
    node_id: str
    keys: tuple[str, ...]


type Mutation = MergeNode | SetProps | RemoveProps | MergeRel | DeleteRel | DeleteNode
