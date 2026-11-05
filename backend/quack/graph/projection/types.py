"""Graph records of the `rules@3` projector (Т-§3.4).

Values: enumerations always explicit (`'unknown'`, `'none'`), counters and weights 0, times in
int64 ms with 0 «не было» (`Т-§3.4.4`, law 8); lists — `requiredLevels`, `triggers`,
`notation`, `embedding`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

type PropValue = str | int | float | bool | list[str] | list[int] | list[float]
type Props = dict[str, PropValue]
type NodeRef = tuple[str, str]  # (label, id)
# (from_label, from_id, type, to_label, to_id) plus the identity props of the type, if any.
type RelKey = tuple[object, ...]

# Relationship types with more than one relationship between the same two nodes, told apart by
# these props: a task tests a skill at several levels and in its parts,
# `(Task)-[:TESTS {level, part}]->(Skill)` (Т-§3.4.3, Т-§7.8 п.1; D-A-133).
REL_IDENTITY: dict[str, tuple[str, ...]] = {"TESTS": ("level", "part")}


def rel_key(
    from_label: str, from_id: str, rel_type: str, to_label: str, to_id: str, props: Props
) -> RelKey:
    ident = tuple(props.get(name) for name in REL_IDENTITY.get(rel_type, ()))
    return (from_label, from_id, rel_type, to_label, to_id, *ident)


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

    @property
    def key(self) -> RelKey:
        return rel_key(
            self.from_label, self.from_id, self.rel_type, self.to_label, self.to_id, self.props
        )


@dataclass
class SubGraph:
    nodes: list[NodeRecord] = field(default_factory=list)
    rels: list[RelRecord] = field(default_factory=list)
