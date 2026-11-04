"""A comparable dump of one learner's subgraph (ТЗ Ф4 §8.14, T-E4: law 1, rebuild = incremental).

Nodes are keyed by label and `id`, relations by their ends and type; properties are sorted, so
two dumps of the same state are equal as Python values."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from neo4j import AsyncDriver

_NODES = (
    "MATCH (n) WHERE n.learnerId = $learner OR n.id = $learner "
    "RETURN labels(n) AS labels, properties(n) AS props"
)
_RELS = (
    "MATCH (a)-[r]->(b) WHERE a.learnerId = $learner OR a.id = $learner "
    "RETURN labels(a) AS from_labels, a.id AS from_id, type(r) AS type, "
    "labels(b) AS to_labels, b.id AS to_id, properties(r) AS props"
)


def _key(item: dict[str, Any]) -> str:
    return repr(sorted(item.items(), key=lambda pair: pair[0]))


async def dump_learner(neo4j: AsyncDriver, learner_id: UUID) -> dict[str, list[dict[str, Any]]]:
    async with neo4j.session() as session:
        nodes = await (await session.run(_NODES, learner=str(learner_id))).data()
        rels = await (await session.run(_RELS, learner=str(learner_id))).data()
    return {
        "nodes": sorted(
            ({"labels": sorted(n["labels"]), "props": dict(n["props"])} for n in nodes),
            key=lambda n: (n["labels"], str(n["props"].get("id")), _key(n["props"])),
        ),
        "rels": sorted(
            (
                {
                    "from": (sorted(r["from_labels"]), r["from_id"]),
                    "type": r["type"],
                    "to": (sorted(r["to_labels"]), r["to_id"]),
                    "props": dict(r["props"]),
                }
                for r in rels
            ),
            key=lambda r: (str(r["from"]), r["type"], str(r["to"]), _key(r["props"])),
        ),
    }
