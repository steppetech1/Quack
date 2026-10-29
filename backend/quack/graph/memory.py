"""In-memory learner graph with the same mutation and loading semantics as Neo4j.

Used as the batch view of the projector, for rebuild from scratch, for the session
overlay and in tests. `quack.graph.neo4j_store` mirrors every rule implemented here.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence

from quack.graph.mutations import (
    AdvanceWatermark,
    DeleteRel,
    Mutation,
    RebindNodeRel,
    UpsertNode,
    UpsertRel,
)
from quack.graph.types import (
    MERGE_REL_TYPES,
    BatchNeed,
    LearnerWatermark,
    NodeKey,
    NodeRecord,
    Props,
    PropValue,
    RelKey,
    RelRecord,
    SubGraph,
)

type Snapshot = tuple[
    tuple[tuple[str, str, tuple[tuple[str, object], ...]], ...],
    tuple[tuple[str, str, str, str, str, tuple[tuple[str, object], ...]], ...],
]

_MERGE_TYPES = frozenset(MERGE_REL_TYPES)
_DOMAIN_LABELS = ("Topic", "Skill", "Misconception")


def validate_props(props: Mapping[str, object]) -> Props:
    """Neo4j drops a property assigned null (law 8), so None is a programming error."""
    clean: Props = {}
    for key, value in props.items():
        if value is None:
            raise ValueError(f"property {key!r} is None; use an explicit value such as 'unknown'")
        if isinstance(value, list):
            if not all(isinstance(v, str) for v in value):
                raise ValueError(f"list property {key!r} must contain only strings")
            clean[key] = list(value)
        elif isinstance(value, str | int | float | bool):
            clean[key] = value
        else:
            raise ValueError(f"unsupported property type for {key!r}: {type(value).__name__}")
    return clean


def _frozen(props: Props) -> tuple[tuple[str, object], ...]:
    return tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in props.items()))


def _copy_props(props: Props) -> Props:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in props.items()}


class InMemoryGraph:
    def __init__(self) -> None:
        self.nodes: dict[NodeKey, Props] = {}
        self.rels: dict[RelKey, Props] = {}
        # Rels deleted in this graph's lifetime: later loads must not resurrect them.
        self.deleted_rels: set[RelKey] = set()

    # --- mutation application (same semantics as the Cypher compiler) ---

    def apply_mutation(self, mutation: Mutation) -> None:
        match mutation:
            case UpsertNode(label=label, id=node_id, props=props):
                clean = validate_props(props)
                node = self.nodes.setdefault((label, node_id), {"id": node_id})
                node.update(clean)
            case UpsertRel(
                from_label=fl, from_id=fid, rel_type=rt, to_label=tl, to_id=tid, props=props
            ):
                clean = validate_props(props)
                if (fl, fid) in self.nodes and (tl, tid) in self.nodes:
                    key = (fl, fid, rt, tl, tid)
                    self.rels.setdefault(key, {}).update(clean)
                    self.deleted_rels.discard(key)
            case DeleteRel(from_label=fl, from_id=fid, rel_type=rt, to_label=tl, to_id=tid):
                self._delete_rel((fl, fid, rt, tl, tid))
            case RebindNodeRel():
                self._rebind(mutation)
            case AdvanceWatermark(
                learner_id=learner_id, applied_seq=seq, projector_version=version
            ):
                node = self.nodes.setdefault(("Learner", learner_id), {"id": learner_id})
                node.update(
                    {"learnerId": learner_id, "appliedSeq": seq, "projectorVersion": version}
                )

    async def apply(self, mutations: Sequence[Mutation]) -> None:
        # All-or-nothing like a Neo4j transaction: validate on a copy first.
        trial = self.copy()
        for mutation in mutations:
            trial.apply_mutation(mutation)
        self.nodes, self.rels, self.deleted_rels = trial.nodes, trial.rels, trial.deleted_rels

    def _delete_rel(self, key: RelKey) -> None:
        if self.rels.pop(key, None) is not None:
            self.deleted_rels.add(key)

    def _rebind(self, m: RebindNodeRel) -> None:
        src, tgt = (m.label, m.source_id), (m.label, m.target_id)
        if src == tgt or src not in self.nodes or tgt not in self.nodes:
            return
        ends = {src, tgt}
        for key in sorted(self.rels):
            fl, fid, rt, tl, tid = key
            if rt not in _MERGE_TYPES:
                continue
            if (fl, fid) == src:
                other = (tl, tid)
                new_key: RelKey = (m.label, m.target_id, rt, tl, tid)
            elif (tl, tid) == src:
                other = (fl, fid)
                new_key = (fl, fid, rt, m.label, m.target_id)
            else:
                continue
            props = self.rels[key]
            self._delete_rel(key)
            if other in ends:
                continue  # would become a loop
            if new_key not in self.rels:
                self.rels[new_key] = _copy_props(props)
                self.deleted_rels.discard(new_key)
        source = self.nodes[src]
        source.update({"status": "merged", "mergedInto": m.target_id, "mergedAt": m.merged_at_iso})
        learner_id = source.get("learnerId", "")
        # Older aliases of the source now point at the target.
        for key in sorted(self.rels):
            fl, fid, rt, tl, tid = key
            if rt == "ALIAS_OF" and (tl, tid) == src:
                self._delete_rel(key)
                self.rels[(fl, fid, "ALIAS_OF", m.label, m.target_id)] = {}
                self.nodes[(fl, fid)]["targetId"] = m.target_id
        alias = self.nodes.setdefault(("Alias", m.source_id), {"id": m.source_id})
        alias.update(
            {
                "learnerId": learner_id,
                "kind": m.label,
                "targetId": m.target_id,
                "mergedAt": m.merged_at_iso,
            }
        )
        for key in [k for k in self.rels if k[0] == "Alias" and k[1] == m.source_id]:
            if key[2] == "ALIAS_OF":
                self._delete_rel(key)
        self.rels[("Alias", m.source_id, "ALIAS_OF", m.label, m.target_id)] = {}

    # --- reading ---

    def get(self, label: str, node_id: str) -> Props | None:
        return self.nodes.get((label, node_id))

    def nodes_with_label(self, label: str) -> list[tuple[str, Props]]:
        return sorted((nid, props) for (lbl, nid), props in self.nodes.items() if lbl == label)

    def out_rels(self, label: str, node_id: str, rel_type: str | None = None) -> list[RelKey]:
        return sorted(
            k
            for k in self.rels
            if k[0] == label and k[1] == node_id and (rel_type is None or k[2] == rel_type)
        )

    def in_rels(self, label: str, node_id: str, rel_type: str | None = None) -> list[RelKey]:
        return sorted(
            k
            for k in self.rels
            if k[3] == label and k[4] == node_id and (rel_type is None or k[2] == rel_type)
        )

    def alias_map(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for node_id, props in self.nodes_with_label("Alias"):
            target = props.get("targetId")
            if isinstance(target, str):
                result[node_id] = target
        return result

    def insert_subgraph(self, subgraph: SubGraph, aliases: Mapping[str, str]) -> None:
        """Add loaded nodes/rels that are not present yet; rel endpoints that are alias
        sources are redirected to their targets (equivalent to RebindNodeRel)."""
        for node in subgraph.nodes:
            key = (node.label, node.id)
            if key not in self.nodes:
                self.nodes[key] = _copy_props(node.props)
        for rel in subgraph.rels:
            from_id, to_id = rel.from_id, rel.to_id
            if rel.rel_type in _MERGE_TYPES:
                if rel.from_label != "Alias":
                    from_id = _follow(aliases, from_id)
                to_id = _follow(aliases, to_id)
            key = (rel.from_label, from_id, rel.rel_type, rel.to_label, to_id)
            if key in self.rels or key in self.deleted_rels:
                continue
            if (rel.from_label, from_id) == (rel.to_label, to_id) and rel.rel_type != "ALIAS_OF":
                continue
            if (rel.from_label, from_id) in self.nodes and (rel.to_label, to_id) in self.nodes:
                self.rels[key] = _copy_props(rel.props)

    def copy(self) -> InMemoryGraph:
        clone = InMemoryGraph()
        clone.nodes = {k: _copy_props(v) for k, v in self.nodes.items()}
        clone.rels = {k: _copy_props(v) for k, v in self.rels.items()}
        clone.deleted_rels = set(self.deleted_rels)
        return clone

    def snapshot(self) -> Snapshot:
        """Canonical, sorted, comparable dump of all nodes and rels."""
        nodes = tuple(
            sorted((lbl, nid, _frozen(props)) for (lbl, nid), props in self.nodes.items())
        )
        rels = tuple(sorted((*key, _frozen(props)) for key, props in self.rels.items()))
        return nodes, rels

    def to_subgraph(self) -> SubGraph:
        return SubGraph(
            nodes=[
                NodeRecord(lbl, nid, _copy_props(p)) for (lbl, nid), p in sorted(self.nodes.items())
            ],
            rels=[RelRecord(*k, _copy_props(p)) for k, p in sorted(self.rels.items())],
        )

    @staticmethod
    def from_records(nodes: Iterable[NodeRecord], rels: Iterable[RelRecord]) -> InMemoryGraph:
        graph = InMemoryGraph()
        for node in nodes:
            graph.nodes[(node.label, node.id)] = _copy_props(node.props)
        for rel in rels:
            graph.rels[(rel.from_label, rel.from_id, rel.rel_type, rel.to_label, rel.to_id)] = (
                _copy_props(rel.props)
            )
        return graph

    # --- GraphSource (same selection rules as the Neo4j queries) ---

    def _of_learner(self, learner_id: str) -> Iterable[tuple[NodeKey, Props]]:
        for key, props in self.nodes.items():
            if props.get("learnerId") == learner_id:
                yield key, props

    def _subgraph(self, keys: set[NodeKey], extra_rels: Iterable[RelKey] = ()) -> SubGraph:
        rels = {k for k in self.rels if (k[0], k[1]) in keys and (k[3], k[4]) in keys}
        rels.update(extra_rels)
        return SubGraph(
            nodes=[NodeRecord(k[0], k[1], _copy_props(self.nodes[k])) for k in sorted(keys)],
            rels=[RelRecord(*k, _copy_props(self.rels[k])) for k in sorted(rels)],
        )

    async def load_watermark(self, learner_id: str) -> LearnerWatermark | None:
        node = self.nodes.get(("Learner", learner_id))
        if node is None:
            return None
        seq = node.get("appliedSeq", 0)
        version = node.get("projectorVersion", "")
        return LearnerWatermark(
            applied_seq=int(seq) if isinstance(seq, int) else 0,
            projector_version=version if isinstance(version, str) else "",
        )

    async def load_batch_slice(self, need: BatchNeed) -> SubGraph:
        learner = need.learner_id
        mine = dict(self._of_learner(learner))
        keys: set[NodeKey] = set()
        if ("Learner", learner) in self.nodes:
            keys.add(("Learner", learner))
        aliases = {k: p for k, p in mine.items() if k[0] == "Alias"}
        keys.update(aliases)
        targets = {t for p in aliases.values() if isinstance(t := p.get("targetId"), str)}
        wanted = set(need.node_ids) | {
            str(p["targetId"]) for k, p in aliases.items() if k[1] in need.node_ids
        }
        mentioned = {k for k in mine if k[0] not in ("Alias", "Learner") and k[1] in wanted}
        superseded = {
            k
            for k, p in mine.items()
            if k[0] == "Evidence" and p.get("sourceSeq") in need.superseded_seqs
        }
        seeds = mentioned | superseded
        hop_targets = {
            (k[3], k[4]) for k in self.rels if (k[0], k[1]) in seeds and (k[3], k[4]) in mine
        }
        domains = set(need.domain_ids)
        for key in seeds | hop_targets:
            props = mine[key]
            if key[0] == "Domain":
                domains.add(key[1])
            domain_id = props.get("domainId")
            if isinstance(domain_id, str):
                domains.add(domain_id)
        group = {
            k
            for k, p in mine.items()
            if (k[0] == "Domain" and k[1] in domains)
            or (k[0] in _DOMAIN_LABELS and p.get("domainId") in domains)
        }
        keys |= seeds | hop_targets | group
        # Alias targets must be present so ALIAS_OF rels are complete.
        keys |= {k for k in mine if k[1] in targets and k[0] != "Alias"}
        return self._subgraph(keys)

    async def load_fold_history(
        self, learner_id: str, skill_ids: Collection[str], misconception_ids: Collection[str]
    ) -> SubGraph:
        skill_set, misc_set = set(skill_ids), set(misconception_ids)
        mine = dict(self._of_learner(learner_id))
        keys = {k for k in mine if (k[0] == "Skill" and k[1] in skill_set)}
        keys |= {k for k in mine if (k[0] == "Misconception" and k[1] in misc_set)}
        rels: set[RelKey] = set()
        for k in self.rels:
            fl, fid, rt, tl, tid = k
            if (fl, fid) not in mine:
                continue
            if (
                (rt == "EVIDENCE_FOR" and fl == "Evidence" and tl == "Skill" and tid in skill_set)
                or (rt == "EXPOSED_TO" and fl == "Exposure" and tl == "Skill" and tid in skill_set)
                or (
                    rt == "OBSERVED_MISCONCEPTION"
                    and fl == "Evidence"
                    and tl == "Misconception"
                    and tid in misc_set
                )
            ):
                rels.add(k)
                keys.add((fl, fid))
                keys.add((tl, tid))
        return SubGraph(
            nodes=[NodeRecord(k[0], k[1], _copy_props(self.nodes[k])) for k in sorted(keys)],
            rels=[RelRecord(*k, _copy_props(self.rels[k])) for k in sorted(rels)],
        )

    async def load_topic_context(self, learner_id: str, topic_ids: Collection[str]) -> SubGraph:
        topic_set = set(topic_ids)
        mine = dict(self._of_learner(learner_id))
        keys = {k for k in mine if k[0] == "Topic" and k[1] in topic_set}
        rels: set[RelKey] = set()
        skills: set[str] = set()
        for k in self.rels:
            fl, fid, rt, tl, tid = k
            if rt == "PART_OF" and fl == "Skill" and tl == "Topic" and tid in topic_set:
                if (fl, fid) in mine:
                    rels.add(k)
                    keys.add((fl, fid))
                    skills.add(fid)
            elif (
                rt == "PREREQ_OF"
                and fl == "Topic"
                and tl == "Topic"
                and (tid in topic_set or fid in topic_set)
                and (fl, fid) in mine
                and (tl, tid) in mine
            ):
                rels.add(k)
                keys.update({(fl, fid), (tl, tid)})
        for k in self.rels:
            fl, fid, rt, tl, tid = k
            if rt == "DEPENDS_ON" and tl == "Skill" and tid in skills and (fl, fid) in mine:
                rels.add(k)
                keys.add((fl, fid))
        return SubGraph(
            nodes=[NodeRecord(k[0], k[1], _copy_props(self.nodes[k])) for k in sorted(keys)],
            rels=[RelRecord(*k, _copy_props(self.rels[k])) for k in sorted(rels)],
        )


def _follow(aliases: Mapping[str, str], node_id: str) -> str:
    seen: set[str] = set()
    while node_id in aliases and node_id not in seen:
        seen.add(node_id)
        node_id = aliases[node_id]
    return node_id


def prop_str(props: Props | None, key: str, default: str = "") -> str:
    if props is None:
        return default
    value = props.get(key, default)
    return value if isinstance(value, str) else default


def prop_float(props: Props | None, key: str, default: float = 0.0) -> float:
    if props is None:
        return default
    value: PropValue = props.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return float(value)


def prop_int(props: Props | None, key: str, default: int = 0) -> int:
    if props is None:
        return default
    value: PropValue = props.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return int(value)


def prop_bool(props: Props | None, key: str, default: bool = False) -> bool:
    if props is None:
        return default
    value = props.get(key, default)
    return value if isinstance(value, bool) else default


follow_alias = _follow
