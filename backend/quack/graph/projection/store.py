"""Neo4j side of the `rules@3` projector (ТЗ Ф4 WP3.3; Т-§2.4, Т-§3.4.7; D-A-181).

`load_slice` reads what the handlers of a batch declared (`Need`) with their aliases; the batch
writes its difference (`SliceDiff`) and the watermark on `Learner` in one write transaction —
grouped by kind with `UNWIND $rows` (`Т-§3.4.7`). Labels, relationship types and the matched
properties come only from the code constants below, never from data.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from neo4j import AsyncDriver, AsyncManagedTransaction

from quack.db.neo4j import literal, read_session
from quack.graph.projection.types import (
    REL_IDENTITY,
    NodeRecord,
    NodeRef,
    Props,
    RelRecord,
    SubGraph,
)
from quack.graph.projection.view import Need, SliceDiff

# `Т-§3.4.2`, `Т-§3.4.3` (§6.2): every label and relationship type of `rules@3`.
LABELS = frozenset(
    {
        "Learner", "Domain", "Topic", "Skill", "Hub", "Evidence", "Exposure", "Misconception",
        "Task", "Option", "Material", "Chunk", "Digest", "Profile", "Alias",
    }
)  # fmt: skip
REL_TYPES = frozenset(
    {
        "OWNS", "HAS_TOPIC", "COVERS", "REQUIRES", "SAME_IDEA", "HAS_HUB", "INCLUDES", "ON",
        "ON_TASK", "MANIFESTS", "RESISTED", "ROOT_CAUSE", "DERIVED_FROM", "OF", "ABOUT",
        "CONFUSED_WITH", "TESTS", "HAS_OPTION", "TRAPS", "HAS_CHUNK", "TEACHES", "USES",
        "HAS_DIGEST", "HAS_PROFILE", "ALIAS_OF",
    }
)  # fmt: skip
MATCH_PROPS = frozenset({"episodeSeq", "observationSeq"})
_ALIAS_DEPTH = 8


def _label(label: str) -> str:
    if label not in LABELS:
        raise ValueError(f"unknown label {label!r}")
    return label


def _rel_type(rel_type: str) -> str:
    if rel_type not in REL_TYPES:
        raise ValueError(f"unknown relationship type {rel_type!r}")
    return rel_type


# --- reading ---------------------------------------------------------------------------------


class _Collected:
    def __init__(self) -> None:
        self.nodes: dict[NodeRef, Props] = {}
        self.rels: dict[tuple[object, ...], RelRecord] = {}

    def node(self, label: str, node_id: str, props: dict[str, Any]) -> None:
        self.nodes[(label, node_id)] = dict(props)

    def rel(self, r: RelRecord) -> None:
        self.rels[r.key] = r

    def subgraph(self) -> SubGraph:
        return SubGraph(
            nodes=[NodeRecord(label, i, p) for (label, i), p in sorted(self.nodes.items())],
            rels=[self.rels[k] for k in sorted(self.rels, key=str)],
        )


async def _nodes(
    tx: AsyncManagedTransaction, learner: str, label: str, ids: Iterable[str], out: _Collected
) -> set[str]:
    """Nodes of a label by id; returns the ids that were found."""
    wanted = sorted(set(ids))
    if not wanted:
        return set()
    query = (
        f"MATCH (n:{_label(label)}) WHERE n.id IN $ids AND n.learnerId = $learner "
        "RETURN n.id AS id, properties(n) AS props"
    )
    result = await tx.run(literal(query), ids=wanted, learner=learner)
    found: set[str] = set()
    async for row in result:
        out.node(label, row["id"], row["props"])
        found.add(row["id"])
    return found


async def _aliases(
    tx: AsyncManagedTransaction, learner: str, refs: set[NodeRef], out: _Collected
) -> set[NodeRef]:
    """`Alias` chains of the ids not found under their label (Т-§2.8); the live targets."""
    missing = {(label, i) for label, i in refs if (label, i) not in out.nodes}
    targets: set[NodeRef] = set()
    for _ in range(_ALIAS_DEPTH):
        if not missing:
            break
        query = (
            "MATCH (a:Alias)-[:ALIAS_OF]->(t) WHERE a.id IN $ids AND a.learnerId = $learner "
            "RETURN a.id AS id, properties(a) AS props, labels(t)[0] AS tl, t.id AS tid, "
            "properties(t) AS tp"
        )
        result = await tx.run(literal(query), ids=sorted({i for _, i in missing}), learner=learner)
        nxt: set[NodeRef] = set()
        async for row in result:
            out.node("Alias", row["id"], row["props"])
            out.rel(RelRecord("Alias", row["id"], "ALIAS_OF", row["tl"], row["tid"], {}))
            out.node(row["tl"], row["tid"], row["tp"])
            target = (row["tl"], row["tid"])
            targets.add(target)
            if row["tl"] == "Alias":
                nxt.add(target)
        missing = nxt
    return targets


async def _neighbourhoods(
    tx: AsyncManagedTransaction, learner: str, refs: Iterable[NodeRef], out: _Collected
) -> set[NodeRef]:
    """The nodes, every relationship of theirs and the nodes at the other end."""
    by_label: dict[str, set[str]] = defaultdict(set)
    for label, node_id in refs:
        by_label[label].add(node_id)
    seen: set[NodeRef] = set()
    for label, ids in sorted(by_label.items()):
        query = (
            f"MATCH (n:{_label(label)}) WHERE n.id IN $ids AND n.learnerId = $learner "
            "OPTIONAL MATCH (n)-[r]-(m) "
            "RETURN n.id AS id, properties(n) AS props, type(r) AS t, startNode(r) = n AS out, "
            "properties(r) AS rp, labels(m)[0] AS ml, m.id AS mid, properties(m) AS mp"
        )
        result = await tx.run(literal(query), ids=sorted(ids), learner=learner)
        async for row in result:
            out.node(label, row["id"], row["props"])
            seen.add((label, row["id"]))
            if row["t"] is None:
                continue
            out.node(row["ml"], row["mid"], row["mp"])
            if row["out"]:
                rel = RelRecord(label, row["id"], row["t"], row["ml"], row["mid"], row["rp"])
            else:
                rel = RelRecord(row["ml"], row["mid"], row["t"], label, row["id"], row["rp"])
            out.rel(rel)
    return seen


async def _domain_nodes(
    tx: AsyncManagedTransaction, learner: str, domain: str, label: str, out: _Collected
) -> None:
    query = (
        f"MATCH (n:{_label(label)}) WHERE n.learnerId = $learner AND n.domainId = $domain "
        "RETURN n.id AS id, properties(n) AS props"
    )
    result = await tx.run(literal(query), learner=learner, domain=domain)
    async for row in result:
        out.node(label, row["id"], row["props"])


async def _matching(
    tx: AsyncManagedTransaction, learner: str, label: str, prop: str, value: int
) -> set[NodeRef]:
    if prop not in MATCH_PROPS:
        raise ValueError(f"unknown match property {prop!r}")
    query = (
        f"MATCH (n:{_label(label)}) WHERE n.learnerId = $learner AND n.{prop} = $value "
        "RETURN n.id AS id"
    )
    result = await tx.run(literal(query), learner=learner, value=value)
    return {(label, row["id"]) async for row in result}


async def _read_slice(
    tx: AsyncManagedTransaction, learner: str, need: Need, event_refs: set[NodeRef]
) -> SubGraph:
    out = _Collected()
    refs = set(need.nodes) | set(need.neighbourhoods) | event_refs
    by_label: dict[str, set[str]] = defaultdict(set)
    for label, node_id in refs:
        by_label[label].add(node_id)
    for label, ids in sorted(by_label.items()):
        await _nodes(tx, learner, label, ids, out)
    targets = await _aliases(tx, learner, refs, out)
    # Alias targets are opened with their neighbourhood: reading more than declared is
    # harmless, the view still checks `needs()`.
    near = set(need.neighbourhoods) | event_refs | targets
    for label, prop, value in sorted(need.matches):
        near |= await _matching(tx, learner, label, prop, value)
    seen = await _neighbourhoods(tx, learner, near, out)
    # The slice of a task also opens its options and the evidence on it (D-A-134 п.6).
    second = {
        (r.from_label, r.from_id) if r.rel_type == "ON_TASK" else (r.to_label, r.to_id)
        for r in out.rels.values()
        if (r.rel_type in ("HAS_OPTION", "TESTS") and (r.from_label, r.from_id) in seen)
        or (r.rel_type == "ON_TASK" and (r.to_label, r.to_id) in seen)
    }  # the skills of the markup with their REQUIRES / SAME_IDEA (WP4.6, D-A-185)
    await _neighbourhoods(tx, learner, second - seen, out)
    for domain, label in sorted(need.domain_labels):
        await _domain_nodes(tx, learner, domain, label, out)
    return out.subgraph()


async def _read_fold(
    tx: AsyncManagedTransaction, learner: str, skills: set[str], misconceptions: set[str]
) -> SubGraph:
    """Phase B input of dirty skills and misconceptions: the skill with its evidence and
    exposures, the misconception with its manifestations and resistances, the tasks of the
    evidence with their options and markup (Т-§8.1, Т-§9.2)."""
    out = _Collected()
    roots = {("Skill", s) for s in skills} | {("Misconception", m) for m in misconceptions}
    seen = await _neighbourhoods(tx, learner, roots, out)
    evidence = {
        (r.from_label, r.from_id)
        for r in out.rels.values()
        if r.rel_type in ("ON", "MANIFESTS", "RESISTED") and (r.to_label, r.to_id) in seen
    }
    await _neighbourhoods(tx, learner, evidence, out)
    tasks = {(r.to_label, r.to_id) for r in out.rels.values() if r.rel_type == "ON_TASK"}
    await _neighbourhoods(tx, learner, tasks, out)
    return out.subgraph()


async def load_fold_inputs(
    driver: AsyncDriver, learner_id: str, skills: set[str], misconceptions: set[str]
) -> SubGraph:
    async with read_session(driver) as session:
        return await session.execute_read(_read_fold, learner_id, skills, misconceptions)


_TOPICS_OF = (
    "MATCH (t:Topic)-[:COVERS]->(s:Skill) WHERE s.learnerId = $learner AND s.id IN $skills "
    "RETURN t.id AS id UNION "
    "MATCH (h:Hub)-[:INCLUDES]->(t:Topic) WHERE h.learnerId = $learner AND h.id IN $hubs "
    "RETURN t.id AS id UNION "
    "MATCH (d:Domain)-[:HAS_HUB]->(h:Hub {kind: 'domain_all'}), (d)-[:HAS_TOPIC]->(t:Topic) "
    "WHERE h.learnerId = $learner AND h.id IN $hubs RETURN t.id AS id"
)
_TOPIC_SKILLS = (
    "MATCH (t:Topic) WHERE t.learnerId = $learner AND t.id IN $topics "
    "OPTIONAL MATCH (t)-[:COVERS]->(s:Skill) "
    "OPTIONAL MATCH (d:Domain)-[:HAS_TOPIC]->(t) "
    "RETURN t.id AS tid, properties(t) AS tp, s.id AS sid, properties(s) AS sp, "
    "d.id AS did, properties(d) AS dp"
)
_TOPIC_MISCONCEPTIONS = (
    "MATCH (m:Misconception)-[:ABOUT]->(s:Skill) WHERE m.learnerId = $learner AND s.id IN $skills "
    "OPTIONAL MATCH (m)-[:CONFUSED_WITH]->(o:Skill) "
    "RETURN m.id AS mid, properties(m) AS mp, s.id AS sid, o.id AS oid, properties(o) AS op"
)
_TOPIC_HUBS = (
    "MATCH (h:Hub)-[i:INCLUDES]->(t:Topic) WHERE h.learnerId = $learner AND t.id IN $topics "
    "RETURN h.id AS hid, properties(h) AS hp, properties(i) AS ip, t.id AS tid"
)
_DOMAIN_HUBS = (
    "MATCH (d:Domain)-[:HAS_HUB]->(h:Hub {kind: 'domain_all'}) "
    "WHERE d.learnerId = $learner AND d.id IN $domains "
    "RETURN d.id AS did, h.id AS hid, properties(h) AS hp"
)


async def _read_topics(
    tx: AsyncManagedTransaction,
    learner: str,
    topics: set[str],
    skills: set[str],
    hubs: set[str],
) -> tuple[SubGraph, set[str]]:
    """Phase C input (Т-§8.6): the topics, their skills with the confirmed misconceptions and
    what they are confused with, the hubs that include them and the hub of the whole domain."""
    out = _Collected()
    found = await tx.run(
        literal(_TOPICS_OF), learner=learner, skills=sorted(skills), hubs=sorted(hubs)
    )
    topics = topics | {row["id"] async for row in found}
    rows = await tx.run(literal(_TOPIC_SKILLS), learner=learner, topics=sorted(topics))
    skill_ids: set[str] = set()
    domains: set[str] = set()
    async for row in rows:
        out.node("Topic", row["tid"], row["tp"])
        if row["did"] is not None:
            domains.add(row["did"])
            out.node("Domain", row["did"], row["dp"])
            out.rel(RelRecord("Domain", row["did"], "HAS_TOPIC", "Topic", row["tid"], {}))
        if row["sid"] is not None:
            skill_ids.add(row["sid"])
            out.node("Skill", row["sid"], row["sp"])
            out.rel(RelRecord("Topic", row["tid"], "COVERS", "Skill", row["sid"], {}))
    rows = await tx.run(literal(_TOPIC_MISCONCEPTIONS), learner=learner, skills=sorted(skill_ids))
    async for row in rows:
        out.node("Misconception", row["mid"], row["mp"])
        out.rel(RelRecord("Misconception", row["mid"], "ABOUT", "Skill", row["sid"], {}))
        if row["oid"] is not None:
            out.node("Skill", row["oid"], row["op"])
            out.rel(
                RelRecord("Misconception", row["mid"], "CONFUSED_WITH", "Skill", row["oid"], {})
            )
    rows = await tx.run(literal(_TOPIC_HUBS), learner=learner, topics=sorted(topics))
    async for row in rows:
        out.node("Hub", row["hid"], row["hp"])
        out.rel(RelRecord("Hub", row["hid"], "INCLUDES", "Topic", row["tid"], row["ip"]))
    rows = await tx.run(literal(_DOMAIN_HUBS), learner=learner, domains=sorted(domains))
    async for row in rows:
        out.node("Hub", row["hid"], row["hp"])
        out.rel(RelRecord("Domain", row["did"], "HAS_HUB", "Hub", row["hid"], {}))
    return out.subgraph(), topics


async def load_topic_inputs(
    driver: AsyncDriver, learner_id: str, topics: set[str], skills: set[str], hubs: set[str]
) -> tuple[SubGraph, set[str]]:
    async with read_session(driver) as session:
        return await session.execute_read(_read_topics, learner_id, topics, skills, hubs)


_PROFILE_EVIDENCE = (
    "MATCH (e:Evidence)-[:ON]->(s:Skill) WHERE e.learnerId = $learner "
    "AND e.derivedKind = 'direct' AND e.status = 'active' "
    "OPTIONAL MATCH (e)-[:ON_TASK]->(t:Task) "
    "RETURN e.id AS eid, properties(e) AS ep, s.id AS sid, properties(s) AS sp, "
    "t.id AS tid, properties(t) AS tp"
)


async def _read_profile_evidence(tx: AsyncManagedTransaction, learner: str) -> SubGraph:
    """The direct active evidence of the learner with its skill and task (WP5.3)."""
    out = _Collected()
    rows = await tx.run(literal(_PROFILE_EVIDENCE), learner=learner)
    async for row in rows:
        out.node("Evidence", row["eid"], row["ep"])
        out.node("Skill", row["sid"], row["sp"])
        out.rel(RelRecord("Evidence", row["eid"], "ON", "Skill", row["sid"], {}))
        if row["tid"] is not None:
            out.node("Task", row["tid"], row["tp"])
            out.rel(RelRecord("Evidence", row["eid"], "ON_TASK", "Task", row["tid"], {}))
    return out.subgraph()


async def load_profile_evidence(driver: AsyncDriver, learner_id: str) -> SubGraph:
    async with read_session(driver) as session:
        return await session.execute_read(_read_profile_evidence, learner_id)


async def load_slice(
    driver: AsyncDriver, learner_id: str, need: Need, event_refs: Iterable[NodeRef] = ()
) -> SubGraph:
    """The slice of a batch: everything its handlers declared, read in one transaction.
    `event_refs` — the nodes named by the events of `need.event_refs`, resolved by the caller
    from the journal (they are opened with their neighbourhood)."""
    refs = set(event_refs)
    async with read_session(driver) as session:
        return await session.execute_read(_read_slice, learner_id, need, refs)


async def _watermark(tx: AsyncManagedTransaction, learner: str) -> tuple[int, str] | None:
    query = "MATCH (l:Learner {id: $learner}) RETURN l.projectedSeq AS seq, l.projectorVersion AS v"
    row = await (await tx.run(literal(query), learner=learner)).single()
    if row is None or row["seq"] is None:
        return None
    return int(row["seq"]), str(row["v"])


async def read_watermark(driver: AsyncDriver, learner_id: str) -> tuple[int, str] | None:
    """`Learner.projectedSeq` and `projectorVersion` (Т-§2.4); `None` — nothing projected."""
    async with read_session(driver) as session:
        return await session.execute_read(_watermark, learner_id)


# --- writing ---------------------------------------------------------------------------------


def _rel_row(r: RelRecord) -> dict[str, Any]:
    return {"f": r.from_id, "t": r.to_id, "props": r.props}


def _rel_groups(rels: Iterable[RelRecord]) -> dict[tuple[str, str, str], list[RelRecord]]:
    groups: dict[tuple[str, str, str], list[RelRecord]] = defaultdict(list)
    for r in rels:
        groups[(_label(r.from_label), _rel_type(r.rel_type), _label(r.to_label))].append(r)
    return groups


def _identity_pattern(rel_type: str) -> str:
    names = REL_IDENTITY.get(rel_type, ())
    if not names:
        return ""
    return " {" + ", ".join(f"{n}: r.props.{n}" for n in names) + "}"


async def _write(
    tx: AsyncManagedTransaction, learner: str, diff: SliceDiff, seq: int, version: str
) -> None:
    for (fl, rt, tl), rels in sorted(_rel_groups(diff.delete_rels).items()):
        query = (
            f"UNWIND $rows AS r MATCH (a:{fl} {{id: r.f}})-[x:{rt}{_identity_pattern(rt)}]->"
            f"(b:{tl} {{id: r.t}}) DELETE x"
        )
        await tx.run(literal(query), rows=[_rel_row(r) for r in rels])
    deletes: dict[str, list[str]] = defaultdict(list)
    for label, node_id in diff.delete_nodes:
        deletes[_label(label)].append(node_id)
    for label, ids in sorted(deletes.items()):
        query = f"UNWIND $ids AS id MATCH (n:{label} {{id: id}}) DETACH DELETE n"
        await tx.run(literal(query), ids=ids)
    upserts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in diff.upsert_nodes:
        upserts[_label(n.label)].append({"id": n.id, "props": n.props})
    for label, rows in sorted(upserts.items()):
        query = f"UNWIND $rows AS r MERGE (n:{label} {{id: r.id}}) SET n += r.props"
        await tx.run(literal(query), rows=rows)
    removals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (label, node_id), keys in diff.remove_props:  # a null in `+=` removes the property
        removals[_label(label)].append({"id": node_id, "props": dict.fromkeys(keys)})
    for label, rows in sorted(removals.items()):
        query = f"UNWIND $rows AS r MATCH (n:{label} {{id: r.id}}) SET n += r.props"
        await tx.run(literal(query), rows=rows)
    for (fl, rt, tl), rels in sorted(_rel_groups(diff.upsert_rels).items()):
        query = (
            f"UNWIND $rows AS r MATCH (a:{fl} {{id: r.f}}), (b:{tl} {{id: r.t}}) "
            f"MERGE (a)-[x:{rt}{_identity_pattern(rt)}]->(b) SET x += r.props"
        )
        await tx.run(literal(query), rows=[_rel_row(r) for r in rels])
    query = (
        "MERGE (l:Learner {id: $learner}) "
        "SET l.learnerId = $learner, l.projectedSeq = $seq, l.projectorVersion = $version"
    )
    await tx.run(literal(query), learner=learner, seq=seq, version=version)


async def write_batch(
    driver: AsyncDriver, learner_id: str, diff: SliceDiff, seq: int, version: str
) -> None:
    """The changes of a batch and its watermark in one write transaction (Т-§2.4: the batch is
    applied whole or not at all)."""
    async with driver.session() as session:
        await session.execute_write(_write, learner_id, diff, seq, version)


async def replace_learner(
    driver: AsyncDriver,
    learner_id: str,
    graph: SubGraph,
    seq: int,
    version: str,
    *,
    chunk: int | None = None,
) -> None:
    """Rebuild (WP3.5, `Т-§2.7`): the learner's subgraph is replaced. `chunk=None` — in one
    transaction; otherwise the old subgraph is deleted and the new one written by parts of at
    most `chunk` operations, the watermark last (a big learner without an active session)."""
    diff = SliceDiff(upsert_nodes=list(graph.nodes), delete_nodes=[], upsert_rels=list(graph.rels),
                     delete_rels=[])  # fmt: skip

    async def delete(tx: AsyncManagedTransaction) -> None:
        for label in sorted(LABELS):
            query = f"MATCH (n:{_label(label)}) WHERE n.learnerId = $learner DETACH DELETE n"
            await tx.run(literal(query), learner=learner_id)

    async def whole(tx: AsyncManagedTransaction) -> None:
        await delete(tx)
        await _write(tx, learner_id, diff, seq, version)

    async with driver.session() as session:
        if chunk is None:
            await session.execute_write(whole)
            return
        await session.execute_write(delete)
        for start in range(0, len(diff.upsert_nodes), chunk):
            part = SliceDiff(diff.upsert_nodes[start : start + chunk], [], [], [])
            await session.execute_write(_write, learner_id, part, 0, "")
        for start in range(0, len(diff.upsert_rels), chunk):
            part = SliceDiff([], [], diff.upsert_rels[start : start + chunk], [])
            await session.execute_write(_write, learner_id, part, 0, "")
        await session.execute_write(_write, learner_id, SliceDiff([], [], [], []), seq, version)


async def delete_learner_graph(driver: AsyncDriver, learner_id: str) -> None:
    """The deletion of an account (WP25): every node of the learner, per label (law 1)."""

    async def work(tx: AsyncManagedTransaction) -> None:
        for label in sorted(LABELS):
            query = f"MATCH (n:{_label(label)}) WHERE n.learnerId = $learner DETACH DELETE n"
            await tx.run(literal(query), learner=learner_id)

    async with driver.session() as session:
        await session.execute_write(work)


async def stale_learners(driver: AsyncDriver, version: str) -> list[str]:
    """`ops rebuild --stale-rules` (WP3.5, Д-§6.3): learners projected by other rules."""
    query = "MATCH (l:Learner) WHERE coalesce(l.projectorVersion, '') <> $v RETURN l.id AS id"
    async with read_session(driver) as session:
        result = await session.run(literal(query), v=version)
        return sorted([row["id"] async for row in result])
