"""Neo4j implementation of GraphStore: the batch reads (Queries 1–3) and the single write
transaction. Selection rules are identical to `InMemoryGraph` (tests compare both)."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from typing import Any

from neo4j import AsyncDriver, AsyncManagedTransaction

from quack.db.neo4j import cypher, literal
from quack.graph.cypher import compile_mutations
from quack.graph.memory import InMemoryGraph, validate_props
from quack.graph.mutations import Mutation, UpsertNode, UpsertRel
from quack.graph.types import (
    NODE_LABELS,
    BatchNeed,
    LearnerWatermark,
    NodeKey,
    NodeRecord,
    RelRecord,
    SubGraph,
)

_DOMAIN_LABELS = ("Topic", "Skill", "Misconception")
_ENTITY_LABELS = tuple(label for label in NODE_LABELS if label not in ("Alias", "Learner"))


class _Collector:
    def __init__(self) -> None:
        self.nodes: dict[NodeKey, dict[str, Any]] = {}
        self.rels: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

    def node(self, label: str, node_id: str, props: dict[str, Any]) -> None:
        self.nodes.setdefault((label, node_id), dict(props))

    def rel(self, fl: str, fid: str, rt: str, tl: str, tid: str, props: dict[str, Any]) -> None:
        self.rels.setdefault((fl, fid, rt, tl, tid), dict(props))

    def subgraph(self, *, only_internal: bool = False) -> SubGraph:
        rels = [
            RelRecord(*k, p)
            for k, p in sorted(self.rels.items())
            if not only_internal or ((k[0], k[1]) in self.nodes and (k[3], k[4]) in self.nodes)
        ]
        return SubGraph(
            nodes=[NodeRecord(k[0], k[1], p) for k, p in sorted(self.nodes.items())], rels=rels
        )


class Neo4jGraphStore:
    """Only the projector (and ops rebuild/purge) may call `apply` (law 3)."""

    def __init__(self, driver: AsyncDriver, learner_id: str) -> None:
        self.driver = driver
        self.learner_id = learner_id

    async def _read(self, query: str, **params: Any) -> list[dict[str, Any]]:
        async with self.driver.session() as session:
            result = await session.run(cypher(query), params)
            return [record.data() async for record in result]

    async def _nodes_by_id(
        self, out: _Collector, labels: Iterable[str], ids: Collection[str]
    ) -> list[NodeKey]:
        found: list[NodeKey] = []
        if not ids:
            return found
        for label in labels:
            rows = await self._read(
                f"MATCH (n:{label}) WHERE n.id IN $ids AND n.learnerId = $l "
                "RETURN n.id AS id, properties(n) AS props",
                ids=sorted(ids),
                l=self.learner_id,
            )
            for row in rows:
                out.node(label, row["id"], row["props"])
                found.append((label, row["id"]))
        return found

    async def _out_rels(self, keys: Iterable[NodeKey]) -> list[dict[str, Any]]:
        by_label: dict[str, set[str]] = {}
        for label, node_id in keys:
            by_label.setdefault(label, set()).add(node_id)
        rows: list[dict[str, Any]] = []
        for label, ids in sorted(by_label.items()):
            part = await self._read(
                f"MATCH (a:{label})-[r]->(b) WHERE a.id IN $ids AND a.learnerId = $l "
                "RETURN a.id AS from_id, type(r) AS type, labels(b)[0] AS to_label, b.id AS to_id, "
                "b.learnerId AS to_learner, properties(r) AS props, properties(b) AS to_props",
                ids=sorted(ids),
                l=self.learner_id,
            )
            rows += [{**row, "from_label": label} for row in part]
        return rows

    # --- GraphSource ---

    async def load_watermark(self, learner_id: str) -> LearnerWatermark | None:
        rows = await self._read(
            "MATCH (l:Learner {id: $id}) RETURN l.appliedSeq AS seq, l.projectorVersion AS v",
            id=learner_id,
        )
        if not rows:
            return None
        seq, version = rows[0]["seq"], rows[0]["v"]
        return LearnerWatermark(
            applied_seq=int(seq) if isinstance(seq, int) else 0,
            projector_version=version if isinstance(version, str) else "",
        )

    async def load_batch_slice(self, need: BatchNeed) -> SubGraph:
        out = _Collector()
        learner = need.learner_id
        rows = await self._read(
            "MATCH (l:Learner {id: $l}) WHERE l.learnerId = $l RETURN properties(l) AS props",
            l=learner,
        )
        for row in rows:
            out.node("Learner", learner, row["props"])
        aliases = await self._read(
            "MATCH (a:Alias) WHERE a.learnerId = $l RETURN a.id AS id, properties(a) AS props",
            l=learner,
        )
        targets: set[str] = set()
        wanted = set(need.node_ids)
        for row in aliases:
            out.node("Alias", row["id"], row["props"])
            target = row["props"].get("targetId")
            if isinstance(target, str):
                targets.add(target)
                if row["id"] in need.node_ids:
                    wanted.add(target)
        seeds = set(await self._nodes_by_id(out, _ENTITY_LABELS, wanted))
        if need.superseded_seqs:
            rows = await self._read(
                "MATCH (e:Evidence) WHERE e.learnerId = $l AND e.sourceSeq IN $seqs "
                "RETURN e.id AS id, properties(e) AS props",
                l=learner,
                seqs=sorted(need.superseded_seqs),
            )
            for row in rows:
                out.node("Evidence", row["id"], row["props"])
                seeds.add(("Evidence", row["id"]))
        hop: set[NodeKey] = set()
        for row in await self._out_rels(seeds):
            if row["to_learner"] == learner:
                out.node(row["to_label"], row["to_id"], row["to_props"])
                hop.add((row["to_label"], row["to_id"]))
        domains = set(need.domain_ids)
        for key in seeds | hop:
            if key[0] == "Domain":
                domains.add(key[1])
            domain_id = out.nodes[key].get("domainId")
            if isinstance(domain_id, str):
                domains.add(domain_id)
        if domains:
            await self._nodes_by_id(out, ["Domain"], domains)
            for label in _DOMAIN_LABELS:
                rows = await self._read(
                    f"MATCH (n:{label}) WHERE n.learnerId = $l AND n.domainId IN $domains "
                    "RETURN n.id AS id, properties(n) AS props",
                    l=learner,
                    domains=sorted(domains),
                )
                for row in rows:
                    out.node(label, row["id"], row["props"])
        await self._nodes_by_id(out, _ENTITY_LABELS, targets)
        for row in await self._out_rels(list(out.nodes)):
            out.rel(
                row["from_label"],
                row["from_id"],
                row["type"],
                row["to_label"],
                row["to_id"],
                row["props"],
            )
        return out.subgraph(only_internal=True)

    async def load_fold_history(
        self, learner_id: str, skill_ids: Collection[str], misconception_ids: Collection[str]
    ) -> SubGraph:
        out = _Collector()
        await self._nodes_by_id(out, ["Skill"], set(skill_ids))
        await self._nodes_by_id(out, ["Misconception"], set(misconception_ids))
        patterns = (
            ("Evidence", "EVIDENCE_FOR", "Skill", skill_ids),
            ("Exposure", "EXPOSED_TO", "Skill", skill_ids),
            ("Evidence", "OBSERVED_MISCONCEPTION", "Misconception", misconception_ids),
        )
        for fl, rt, tl, ids in patterns:
            if not ids:
                continue
            rows = await self._read(
                f"MATCH (a:{fl})-[r:{rt}]->(b:{tl}) WHERE b.id IN $ids AND a.learnerId = $l "
                "AND b.learnerId = $l RETURN a.id AS from_id, properties(a) AS from_props, "
                "b.id AS to_id, properties(b) AS to_props, properties(r) AS props",
                ids=sorted(ids),
                l=learner_id,
            )
            for row in rows:
                out.node(fl, row["from_id"], row["from_props"])
                out.node(tl, row["to_id"], row["to_props"])
                out.rel(fl, row["from_id"], rt, tl, row["to_id"], row["props"])
        return out.subgraph()

    async def load_topic_context(self, learner_id: str, topic_ids: Collection[str]) -> SubGraph:
        out = _Collector()
        ids = sorted(set(topic_ids))
        await self._nodes_by_id(out, ["Topic"], ids)
        skills: set[str] = set()
        rows = await self._read(
            "MATCH (s:Skill)-[r:PART_OF]->(t:Topic) WHERE t.id IN $ids AND s.learnerId = $l "
            "AND t.learnerId = $l RETURN s.id AS sid, properties(s) AS sprops, t.id AS tid, "
            "properties(r) AS props",
            ids=ids,
            l=learner_id,
        )
        for row in rows:
            out.node("Skill", row["sid"], row["sprops"])
            out.rel("Skill", row["sid"], "PART_OF", "Topic", row["tid"], row["props"])
            skills.add(row["sid"])
        rows = await self._read(
            "MATCH (a:Topic)-[r:PREREQ_OF]->(b:Topic) WHERE (a.id IN $ids OR b.id IN $ids) "
            "AND a.learnerId = $l AND b.learnerId = $l RETURN a.id AS aid, properties(a) AS ap, "
            "b.id AS bid, properties(b) AS bp, properties(r) AS props",
            ids=ids,
            l=learner_id,
        )
        for row in rows:
            out.node("Topic", row["aid"], row["ap"])
            out.node("Topic", row["bid"], row["bp"])
            out.rel("Topic", row["aid"], "PREREQ_OF", "Topic", row["bid"], row["props"])
        if skills:
            rows = await self._read(
                "MATCH (a)-[r:DEPENDS_ON]->(b:Skill) WHERE b.id IN $ids AND a.learnerId = $l "
                "AND b.learnerId = $l RETURN labels(a)[0] AS al, a.id AS aid, "
                "properties(a) AS ap, b.id AS bid, properties(r) AS props",
                ids=sorted(skills),
                l=learner_id,
            )
            for row in rows:
                out.node(row["al"], row["aid"], row["ap"])
                out.rel(row["al"], row["aid"], "DEPENDS_ON", "Skill", row["bid"], row["props"])
        return out.subgraph()

    # --- GraphSink ---

    async def apply(self, mutations: Sequence[Mutation]) -> None:
        for m in mutations:
            if isinstance(m, UpsertNode | UpsertRel):
                validate_props(m.props)
        statements = compile_mutations(mutations)

        async def work(tx: AsyncManagedTransaction) -> None:
            for st in statements:
                result = await tx.run(literal(st.query), st.params)
                await result.consume()

        async with self.driver.session() as session:
            await session.execute_write(work)

    # --- maintenance ---

    async def dump(self) -> InMemoryGraph:
        """Read the whole learner subgraph (rebuild checks and tests)."""
        nodes: list[NodeRecord] = []
        rels: list[RelRecord] = []
        for label in NODE_LABELS:
            rows = await self._read(
                f"MATCH (n:{label}) WHERE n.learnerId = $l RETURN n.id AS id, properties(n) AS p",
                l=self.learner_id,
            )
            nodes += [NodeRecord(label, row["id"], row["p"]) for row in rows]
        for row in await self._out_rels([(n.label, n.id) for n in nodes]):
            rels.append(
                RelRecord(
                    row["from_label"],
                    row["from_id"],
                    row["type"],
                    row["to_label"],
                    row["to_id"],
                    row["props"],
                )
            )
        return InMemoryGraph.from_records(nodes, rels)

    async def delete_learner(self, chunk_size: int) -> int:
        """DETACH DELETE all nodes of the learner in chunks; returns deleted count."""
        total = 0
        for label in NODE_LABELS:
            while True:
                async with self.driver.session() as session:
                    result = await session.run(
                        cypher(
                            f"MATCH (n:{label}) WHERE n.learnerId = $l WITH n LIMIT $chunk "
                            "DETACH DELETE n RETURN count(*) AS deleted"
                        ),
                        {"l": self.learner_id, "chunk": chunk_size},
                    )
                    record = await result.single()
                deleted = int(record["deleted"]) if record else 0
                total += deleted
                if deleted < chunk_size:
                    break
        return total


async def read_applied_seq(driver: AsyncDriver, learner_id: str) -> int:
    watermark = await Neo4jGraphStore(driver, learner_id).load_watermark(learner_id)
    return watermark.applied_seq if watermark else 0


async def find_learners_with_rules(driver: AsyncDriver, *, current: str) -> list[str]:
    """Learner ids whose projectorVersion differs from `current`."""
    async with driver.session() as session:
        result = await session.run(
            "MATCH (l:Learner) WHERE l.projectorVersion IS NOT NULL "
            "AND l.projectorVersion <> $v RETURN l.id AS id ORDER BY id",
            {"v": current},
        )
        return [record["id"] async for record in result]
