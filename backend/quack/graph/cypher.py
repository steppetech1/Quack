"""Compiler of Mutation objects into parameterized Cypher (spec §4.2).

Labels and relationship types come only from code constants and are validated; values
always travel as parameters. Semantics match `InMemoryGraph.apply_mutation` exactly.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from quack.graph.mutations import (
    AdvanceWatermark,
    DeleteRel,
    Mutation,
    RebindNodeRel,
    UpsertNode,
    UpsertRel,
)
from quack.graph.types import MERGE_REL_TYPES

_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class CypherStatement:
    query: str
    params: dict[str, Any]


def _ident(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"invalid Cypher identifier {name!r}")
    return name


def _shape(m: Mutation) -> tuple[Any, ...] | None:
    match m:
        case UpsertNode(label=label):
            return ("node", label)
        case UpsertRel(from_label=fl, rel_type=rt, to_label=tl):
            return ("rel", fl, rt, tl)
        case DeleteRel(from_label=fl, rel_type=rt, to_label=tl):
            return ("del", fl, rt, tl)
        case _:
            return None


def _group_statement(shape: tuple[Any, ...], rows: list[dict[str, Any]]) -> CypherStatement:
    kind = shape[0]
    if kind == "node":
        label = _ident(shape[1])
        query = f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n += row.props"
    elif kind == "rel":
        fl, rt, tl = (_ident(x) for x in shape[1:])
        query = (
            f"UNWIND $rows AS row MATCH (a:{fl} {{id: row.from_id}}) "
            f"MATCH (b:{tl} {{id: row.to_id}}) MERGE (a)-[r:{rt}]->(b) SET r += row.props"
        )
    else:
        fl, rt, tl = (_ident(x) for x in shape[1:])
        query = (
            f"UNWIND $rows AS row MATCH (a:{fl} {{id: row.from_id}})-[r:{rt}]->"
            f"(b:{tl} {{id: row.to_id}}) DELETE r"
        )
    return CypherStatement(query, {"rows": rows})


def _row(m: Mutation) -> dict[str, Any]:
    match m:
        case UpsertNode(id=node_id, props=props):
            return {"id": node_id, "props": dict(props)}
        case UpsertRel(from_id=fid, to_id=tid, props=props):
            return {"from_id": fid, "to_id": tid, "props": dict(props)}
        case DeleteRel(from_id=fid, to_id=tid):
            return {"from_id": fid, "to_id": tid}
        case _:
            raise TypeError(f"not a groupable mutation: {m!r}")


def _rebind_statements(m: RebindNodeRel) -> list[CypherStatement]:
    label = _ident(m.label)
    params = {
        "source_id": m.source_id,
        "target_id": m.target_id,
        "merged_at": m.merged_at_iso,
        "label": m.label,
    }
    head = f"MATCH (src:{label} {{id: $source_id}}) MATCH (tgt:{label} {{id: $target_id}}) "
    statements: list[CypherStatement] = []
    for rel_type in MERGE_REL_TYPES:
        rt = _ident(rel_type)
        statements += [
            CypherStatement(
                head + f"WHERE src <> tgt MATCH (src)-[r:{rt}]->(o) WHERE o <> src AND o <> tgt "
                f"MERGE (tgt)-[n:{rt}]->(o) ON CREATE SET n = properties(r) DELETE r",
                params,
            ),
            CypherStatement(
                head + f"WHERE src <> tgt MATCH (o)-[r:{rt}]->(src) WHERE o <> src AND o <> tgt "
                f"MERGE (o)-[n:{rt}]->(tgt) ON CREATE SET n = properties(r) DELETE r",
                params,
            ),
            CypherStatement(
                head + f"WHERE src <> tgt MATCH (src)-[r:{rt}]->(x) "
                "WHERE x = src OR x = tgt DELETE r",
                params,
            ),
            CypherStatement(
                head + f"WHERE src <> tgt MATCH (tgt)-[r:{rt}]->(src) DELETE r",
                params,
            ),
        ]
    statements += [
        CypherStatement(
            head + "WHERE src <> tgt SET src.status = 'merged', src.mergedInto = $target_id, "
            "src.mergedAt = $merged_at",
            params,
        ),
        CypherStatement(
            head + "WHERE src <> tgt MATCH (old:Alias)-[ar:ALIAS_OF]->(src) DELETE ar "
            "MERGE (old)-[:ALIAS_OF]->(tgt) SET old.targetId = $target_id",
            params,
        ),
        CypherStatement(
            head + "WHERE src <> tgt MERGE (a:Alias {id: $source_id}) "
            "SET a.learnerId = coalesce(src.learnerId, ''), a.kind = $label, "
            "a.targetId = $target_id, a.mergedAt = $merged_at "
            "WITH a, tgt OPTIONAL MATCH (a)-[old:ALIAS_OF]->() DELETE old "
            "WITH DISTINCT a, tgt MERGE (a)-[:ALIAS_OF]->(tgt)",
            params,
        ),
    ]
    return statements


def compile_mutations(mutations: Sequence[Mutation]) -> list[CypherStatement]:
    """Compile mutations in order; consecutive mutations of the same shape share one
    UNWIND statement. Order of effects is preserved."""
    statements: list[CypherStatement] = []
    current: tuple[Any, ...] | None = None
    rows: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current, rows
        if current is not None and rows:
            statements.append(_group_statement(current, rows))
        current, rows = None, []

    for m in mutations:
        shape = _shape(m)
        if shape is not None:
            if shape != current:
                flush()
                current = shape
            rows.append(_row(m))
            continue
        flush()
        if isinstance(m, RebindNodeRel):
            statements += _rebind_statements(m)
        elif isinstance(m, AdvanceWatermark):
            statements.append(
                CypherStatement(
                    "MERGE (l:Learner {id: $learner_id}) SET l.learnerId = $learner_id, "
                    "l.appliedSeq = $applied_seq, l.projectorVersion = $projector_version",
                    {
                        "learner_id": m.learner_id,
                        "applied_seq": m.applied_seq,
                        "projector_version": m.projector_version,
                    },
                )
            )
    flush()
    return statements
