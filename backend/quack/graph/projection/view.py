"""Slice of a batch and the read-only view a handler gets (Т-§2.4, Т-§2.5).

Every handler declares `needs(event)`. The slice of the whole batch is read with one query
before phase A; then each event sees what was read plus the changes of the earlier events of
the batch. A handler that reads outside its own `needs(event)` is a program error
(`OutsideSliceError`, T-P3), even if the node is in the slice for another event.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from quack.config import Settings
from quack.events.log import StoredEvent
from quack.graph.projection.mutations import (
    DeleteNode,
    DeleteRel,
    MergeNode,
    MergeRel,
    Mutation,
    RemoveProps,
    SetProps,
)
from quack.graph.projection.types import (
    REL_IDENTITY,
    NodeRecord,
    NodeRef,
    Props,
    RelKey,
    RelRecord,
    SubGraph,
    rel_key,
)


@dataclass(frozen=True)
class Need:
    """What one handler reads.

    `nodes` — the nodes themselves (through their aliases, Т-§2.8); `neighbourhoods` — the nodes,
    every relationship of theirs and the nodes at the other end; `domain_labels` — every node
    of a label in a domain, `(domain_id, label)` (e.g. skills by normalized name, Т-§4.4);
    `events` — events of the learner by `seq` (folds and handlers read events «через
    представление», WP3.2); `event_refs` — `(seq, payload field, label)`: the node named by a
    field of that event, with its neighbourhood (the task of an answer for its verdict). The
    neighbourhood of a task also opens its options and the evidence on it with their relations
    (traps for `RESISTED`, the evidence of a verdict for a dispute, D-A-134); `matches` —
    `(label, prop, value)`: the nodes with that value, with their relations (the evidence of an
    episode or of an observation, D-A-180).
    """

    nodes: frozenset[NodeRef] = frozenset()
    neighbourhoods: frozenset[NodeRef] = frozenset()
    domain_labels: frozenset[tuple[str, str]] = frozenset()
    events: frozenset[int] = frozenset()
    event_refs: frozenset[tuple[int, str, str]] = frozenset()
    matches: frozenset[tuple[str, str, int]] = frozenset()

    def __or__(self, other: Need) -> Need:
        return Need(
            nodes=self.nodes | other.nodes,
            neighbourhoods=self.neighbourhoods | other.neighbourhoods,
            domain_labels=self.domain_labels | other.domain_labels,
            events=self.events | other.events,
            event_refs=self.event_refs | other.event_refs,
            matches=self.matches | other.matches,
        )


NOTHING = Need()


class OutsideSliceError(Exception):
    """A handler read what its `needs(event)` does not declare (Т-§2.4: a program error)."""


class SliceView(Protocol):
    """Read-only view of one handler, limited to its `needs(event)`."""

    def node(self, label: str, node_id: str) -> Props | None:
        """Props of a live node, `None` if there is none (after alias resolution)."""
        ...

    def resolve(self, label: str, node_id: str) -> str | None:
        """Live id of the node through `Alias` (Т-§2.8); `None` — no node and no alias."""
        ...

    def out_rels(
        self, label: str, node_id: str, rel_type: str | None = None
    ) -> list[RelRecord]: ...

    def in_rels(self, label: str, node_id: str, rel_type: str | None = None) -> list[RelRecord]: ...

    def domain_nodes(self, domain_id: str, label: str) -> list[NodeRecord]: ...

    def event(self, seq: int) -> StoredEvent | None:
        """An event of the learner by `seq`; `None` — no such event (deleted, Т-§2.9)."""
        ...

    def matching(self, label: str, prop: str, value: int) -> list[NodeRecord]:
        """The nodes of `label` whose `prop` equals `value`."""
        ...

    @property
    def params(self) -> RulesParams: ...


@dataclass(frozen=True)
class RulesParams:
    """Parameters of `Т-§7`–`Т-§9` the handlers read; they belong to `rules@N` (`Т-§2.5`)."""

    root_cause_confidence: Mapping[str, float]
    requires_min_confidence: float = 0.6
    same_idea_min_confidence: float = 0.7

    @classmethod
    def from_settings(cls, settings: Settings) -> RulesParams:
        confidence = {str(k): v for k, v in settings.root_cause_confidence_map.items()}
        return cls(
            root_cause_confidence=confidence,
            requires_min_confidence=settings.requires_min_confidence,
            same_idea_min_confidence=settings.same_idea_min_confidence,
        )


@dataclass(frozen=True)
class SliceDiff:
    """Changed props of existing nodes and every prop of new ones; deleted nodes; new or
    changed relationships with their props; deleted relationships (identity props kept)."""

    upsert_nodes: list[NodeRecord]
    delete_nodes: list[NodeRef]
    upsert_rels: list[RelRecord]
    delete_rels: list[RelRecord]
    remove_props: list[tuple[NodeRef, tuple[str, ...]]] = field(default_factory=list)


class BatchSlice:
    """The slice of one learner's batch: the store read plus the changes of the batch.

    Applying is strict: `SetProps` on a missing node and `MergeRel` with a missing end are
    handler errors (a handler resolves its references first and applies nothing if they are
    gone, Т-§2.4).
    """

    def __init__(
        self,
        learner_id: str,
        loaded: SubGraph,
        events: Sequence[StoredEvent] = (),
        params: RulesParams | None = None,
    ) -> None:
        self.learner_id = learner_id
        self.params = params or RulesParams.from_settings(Settings())
        self._events: dict[int, StoredEvent] = {e.seq: e for e in events}
        self._nodes: dict[NodeRef, Props] = {(n.label, n.id): dict(n.props) for n in loaded.nodes}
        self._rels: dict[RelKey, RelRecord] = {r.key: _copy(r) for r in loaded.rels}
        self._loaded_nodes: dict[NodeRef, Props] = {k: dict(v) for k, v in self._nodes.items()}
        self._loaded_rels: dict[RelKey, RelRecord] = {k: _copy(r) for k, r in self._rels.items()}

    def view(self, need: Need) -> SliceView:
        """The view of one handler: reads outside `need` raise `OutsideSliceError`."""
        return _View(self, need)

    def add_event(self, event: StoredEvent) -> None:
        """An event of the batch, so the later events of the batch can read it."""
        self._events[event.seq] = event

    def apply(self, mutations: Sequence[Mutation]) -> None:
        """Apply the changes of one event, so later events of the batch see them."""
        for m in mutations:
            match m:
                case MergeNode(label, node_id, props):
                    self._nodes[(label, node_id)] = {
                        **self._nodes.get((label, node_id), {}),
                        **props,
                    }
                case SetProps(label, node_id, props):
                    if (label, node_id) not in self._nodes:
                        raise ValueError(f"SetProps on a missing node {label} {node_id}")
                    self._nodes[(label, node_id)].update(props)
                case RemoveProps(label, node_id, keys):
                    for key in keys:
                        self._nodes.get((label, node_id), {}).pop(key, None)
                case MergeRel(from_label, from_id, rel_type, to_label, to_id, props):
                    for end in ((from_label, from_id), (to_label, to_id)):
                        if end not in self._nodes:
                            raise ValueError(f"MergeRel {rel_type} with a missing end {end}")
                    key = rel_key(from_label, from_id, rel_type, to_label, to_id, props)
                    old = self._rels.get(key)
                    merged = {**(old.props if old else {}), **props}
                    self._rels[key] = RelRecord(
                        from_label, from_id, rel_type, to_label, to_id, merged
                    )
                case DeleteRel(from_label, from_id, rel_type, to_label, to_id, key_props):
                    key = rel_key(from_label, from_id, rel_type, to_label, to_id, key_props)
                    self._rels.pop(key, None)
                case DeleteNode(label, node_id):
                    self._nodes.pop((label, node_id), None)
                    for key, r in list(self._rels.items()):
                        if (label, node_id) in ((r.from_label, r.from_id), (r.to_label, r.to_id)):
                            del self._rels[key]

    def diff(self) -> SliceDiff:
        """What the batch changed against the slice read before it (WP3.3, D-A-181)."""
        upsert_nodes: list[NodeRecord] = []
        for (label, node_id), props in sorted(self._nodes.items()):
            before = self._loaded_nodes.get((label, node_id))
            if before is None:
                upsert_nodes.append(NodeRecord(label, node_id, dict(props)))
                continue
            changed = {k: v for k, v in props.items() if k not in before or before[k] != v}
            if changed:
                upsert_nodes.append(NodeRecord(label, node_id, changed))
        delete_nodes = sorted(ref for ref in self._loaded_nodes if ref not in self._nodes)
        remove_props = [
            (ref, tuple(sorted(set(before) - set(self._nodes[ref]))))
            for ref, before in sorted(self._loaded_nodes.items())
            if ref in self._nodes and set(before) - set(self._nodes[ref])
        ]
        ordered = sorted(self._rels.items(), key=lambda item: str(item[0]))
        upsert_rels = [
            _copy(r)
            for key, r in ordered
            if key not in self._loaded_rels or self._loaded_rels[key].props != r.props
        ]
        delete_rels = [
            RelRecord(r.from_label, r.from_id, r.rel_type, r.to_label, r.to_id, _identity(r))
            for key, r in sorted(self._loaded_rels.items(), key=lambda item: str(item[0]))
            if key not in self._rels
        ]
        return SliceDiff(upsert_nodes, delete_nodes, upsert_rels, delete_rels, remove_props)

    def extend(self, loaded: SubGraph) -> None:
        """Phase B reads more of the store after phase A (the evidence of dirty skills): what
        the slice does not hold yet joins it as read, what the batch changed or deleted stays."""
        for n in loaded.nodes:
            ref = (n.label, n.id)
            if ref not in self._nodes and ref not in self._loaded_nodes:
                self._nodes[ref] = dict(n.props)
                self._loaded_nodes[ref] = dict(n.props)
        for r in loaded.rels:
            if r.key not in self._rels and r.key not in self._loaded_rels:
                self._rels[r.key] = _copy(r)
                self._loaded_rels[r.key] = _copy(r)

    def node(self, label: str, node_id: str) -> Props | None:
        """Phase B reads the whole slice: folds are not handlers of one event (no `needs`)."""
        props = self._nodes.get((label, node_id))
        return None if props is None else dict(props)

    def nodes(self, label: str) -> list[NodeRecord]:
        return [
            NodeRecord(node_label, node_id, dict(props))
            for (node_label, node_id), props in sorted(self._nodes.items())
            if node_label == label
        ]

    def rels(self, label: str, node_id: str, rel_type: str, *, out: bool) -> list[RelRecord]:
        return [r for r in self._rels_of(label, node_id, out=out) if r.rel_type == rel_type]

    def dirty_skills(self) -> set[str]:
        """Skills whose fold input the batch changed (Т-§2.4, phase B): new skills, evidence and
        exposures added, changed or moved."""
        d = self.diff()
        dirty = {
            r.to_id for r in [*d.upsert_rels, *d.delete_rels]
            if r.rel_type in ("ON", "OF") and r.to_label == "Skill"
        }  # fmt: skip
        for n in d.upsert_nodes:
            if n.label == "Skill" and ("Skill", n.id) not in self._loaded_nodes:
                dirty.add(n.id)
            elif n.label in ("Evidence", "Exposure"):
                rel = "ON" if n.label == "Evidence" else "OF"
                dirty |= {r.to_id for r in self.rels(n.label, n.id, rel, out=True)}
        return {s for s in dirty if ("Skill", s) in self._nodes}

    def dirty_misconceptions(self) -> set[str]:
        """Misconceptions whose manifestations, resistances or own marks the batch changed."""
        d = self.diff()
        dirty = {
            r.to_id for r in [*d.upsert_rels, *d.delete_rels]
            if r.rel_type in ("MANIFESTS", "RESISTED")
        }  # fmt: skip
        for n in d.upsert_nodes:
            if n.label == "Misconception":
                dirty.add(n.id)
            elif n.label == "Evidence":
                for rel in ("MANIFESTS", "RESISTED"):
                    dirty |= {r.to_id for r in self.rels(n.label, n.id, rel, out=True)}
        return {m for m in dirty if ("Misconception", m) in self._nodes}

    def topic_seeds(self) -> tuple[set[str], set[str], set[str]]:
        """Phase C (WP6): topics the batch touched directly, skills whose topics it touched (the
        folded skills, the skills of folded misconceptions), hubs it changed."""
        d = self.diff()
        topics = {n.id for n in d.upsert_nodes if n.label == "Topic"}
        skills = {n.id for n in d.upsert_nodes if n.label == "Skill"}
        hubs = {n.id for n in d.upsert_nodes if n.label == "Hub"}
        for r in [*d.upsert_rels, *d.delete_rels]:
            if r.rel_type == "INCLUDES":
                topics.add(r.to_id)
            elif r.rel_type == "COVERS":
                topics.add(r.from_id)
        for n in d.upsert_nodes:
            if n.label == "Misconception":
                skills |= {r.to_id for r in self.rels(n.label, n.id, "ABOUT", out=True)}
        for skill in skills:
            topics |= {r.from_id for r in self.rels("Skill", skill, "COVERS", out=False)}
        for hub in hubs:
            topics |= {r.to_id for r in self.rels("Hub", hub, "INCLUDES", out=True)}
            if (self._nodes.get(("Hub", hub)) or {}).get("kind") == "domain_all":
                for d in self.rels("Hub", hub, "HAS_HUB", out=False):
                    topics |= {
                        r.to_id for r in self.rels("Domain", d.from_id, "HAS_TOPIC", out=True)
                    }
        return topics, skills, hubs

    def snapshot(self) -> SubGraph:
        """Every node and relationship of the slice now (for tests and rebuild)."""
        return SubGraph(
            nodes=[NodeRecord(label, i, dict(p)) for (label, i), p in sorted(self._nodes.items())],
            rels=[_copy(r) for _, r in sorted(self._rels.items(), key=lambda item: str(item[0]))],
        )

    # --- reads without the guard (the view checks the need first) ---

    def _resolve(self, label: str, node_id: str) -> str | None:
        seen: set[str] = set()
        while (label, node_id) not in self._nodes:
            if node_id in seen or ("Alias", node_id) not in self._nodes:
                return None
            seen.add(node_id)
            targets = [
                r.to_id
                for r in self._rels.values()
                if (r.from_label, r.from_id, r.rel_type) == ("Alias", node_id, "ALIAS_OF")
            ]
            if len(targets) != 1:
                return None
            node_id = targets[0]
        return node_id

    def _rels_of(self, label: str, node_id: str, *, out: bool) -> list[RelRecord]:
        return [
            _copy(r)
            for _, r in sorted(self._rels.items(), key=lambda item: str(item[0]))
            if ((r.from_label, r.from_id) if out else (r.to_label, r.to_id)) == (label, node_id)
        ]

    def _matching(self, label: str, prop: str, value: int) -> list[NodeRecord]:
        return [
            NodeRecord(node_label, node_id, dict(props))
            for (node_label, node_id), props in sorted(self._nodes.items())
            if node_label == label and props.get(prop) == value
        ]

    def _neighbours(self, label: str, node_id: str) -> set[NodeRef]:
        return {(r.to_label, r.to_id) for r in self._rels_of(label, node_id, out=True)} | {
            (r.from_label, r.from_id) for r in self._rels_of(label, node_id, out=False)
        }


def _identity(r: RelRecord) -> Props:
    return {name: r.props[name] for name in REL_IDENTITY.get(r.rel_type, ()) if name in r.props}


def _copy(r: RelRecord) -> RelRecord:
    return RelRecord(r.from_label, r.from_id, r.rel_type, r.to_label, r.to_id, dict(r.props))


class _View:
    def __init__(self, batch: BatchSlice, need: Need) -> None:
        self._batch = batch
        self._need = need
        refs: set[NodeRef] = set()
        for seq, field_name, label in need.event_refs:
            event = batch._events.get(seq)
            value = event.payload.get(field_name) if event is not None else None
            if isinstance(value, str):
                refs.add((label, value))
        near = set(need.neighbourhoods) | refs
        near |= {(n.label, n.id) for m in need.matches for n in batch._matching(*m)}
        for label, node_id in list(near):
            live = batch._resolve(label, node_id) if label == "Task" else None
            if live is not None:
                near |= {
                    (r.to_label, r.to_id)
                    for r in batch._rels_of("Task", live, out=True)
                    if r.rel_type == "HAS_OPTION"
                }
                near |= {
                    (r.from_label, r.from_id)
                    for r in batch._rels_of("Task", live, out=False)
                    if r.rel_type == "ON_TASK"
                }
                # ... and the skills of its markup with their relations (WP4.6, D-A-185).
                near |= {
                    (r.to_label, r.to_id)
                    for r in batch._rels_of("Task", live, out=True)
                    if r.rel_type == "TESTS"
                }
        self._near: frozenset[NodeRef] = frozenset(near)

    def _check_node(self, label: str, node_id: str) -> None:
        ref = (label, node_id)
        if ref in self._need.nodes or ref in self._near:
            return
        for near_label, near_id in self._near:
            live = self._batch._resolve(near_label, near_id)
            if live is not None and ref in self._batch._neighbours(near_label, live):
                return
        raise OutsideSliceError(f"{label} {node_id} is not in needs()")

    def node(self, label: str, node_id: str) -> Props | None:
        self._check_node(label, node_id)
        live = self._batch._resolve(label, node_id)
        return None if live is None else dict(self._batch._nodes[(label, live)])

    def resolve(self, label: str, node_id: str) -> str | None:
        self._check_node(label, node_id)
        return self._batch._resolve(label, node_id)

    def _rels(
        self, label: str, node_id: str, rel_type: str | None, *, out: bool
    ) -> list[RelRecord]:
        if (label, node_id) not in self._near:
            raise OutsideSliceError(f"relationships of {label} {node_id} are not in needs()")
        live = self._batch._resolve(label, node_id)
        if live is None:
            return []
        rels = self._batch._rels_of(label, live, out=out)
        return [r for r in rels if rel_type is None or r.rel_type == rel_type]

    def out_rels(self, label: str, node_id: str, rel_type: str | None = None) -> list[RelRecord]:
        return self._rels(label, node_id, rel_type, out=True)

    def in_rels(self, label: str, node_id: str, rel_type: str | None = None) -> list[RelRecord]:
        return self._rels(label, node_id, rel_type, out=False)

    @property
    def params(self) -> RulesParams:
        return self._batch.params

    def matching(self, label: str, prop: str, value: int) -> list[NodeRecord]:
        if (label, prop, value) not in self._need.matches:
            raise OutsideSliceError(f"{label} with {prop} = {value} are not in needs()")
        return self._batch._matching(label, prop, value)

    def event(self, seq: int) -> StoredEvent | None:
        if seq not in self._need.events:
            raise OutsideSliceError(f"event {seq} is not in needs()")
        return self._batch._events.get(seq)

    def domain_nodes(self, domain_id: str, label: str) -> list[NodeRecord]:
        if (domain_id, label) not in self._need.domain_labels:
            raise OutsideSliceError(f"{label} nodes of domain {domain_id} are not in needs()")
        return [
            NodeRecord(node_label, node_id, dict(props))
            for (node_label, node_id), props in sorted(self._batch._nodes.items())
            if node_label == label and props.get("domainId") == domain_id
        ]
