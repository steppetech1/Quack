"""WP0 п.3б (ТЗ Фазы 4): is a composite range index `(learnerId, retDueAtMs)` on `Skill`
available in Neo4j Community (`Т-§3.4.8`), and does the review-candidate lookup use it?

Creates a throwaway composite range index on the `Wp0CheckSkill` label, writes skills of two
learners, runs the WP17.1 shape (`learnerId = $id AND retDueAtMs > 0 AND retDueAtMs <= $now`)
and checks the result and that the plan seeks that index. Drops the index and the nodes
afterwards. Prints one JSON line; exit 0 — works, 1 — does not.

Not part of `pytest`. Run from `backend/` against a disposable Neo4j:

    NEO4J_URI=bolt://127.0.0.1:57687 NEO4J_USER=neo4j NEO4J_PASSWORD=... \
        uv run python scripts/checks/skill_due_index.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from neo4j import AsyncGraphDatabase

LABEL = "Wp0CheckSkill"
INDEX = "wp0_check_skill_learner_due"

NODES = [
    # id, learnerId, retDueAtMs (0 = no card yet, Т-§3.4.4)
    ("a1", "learner-a", 1_000),
    ("a2", "learner-a", 5_000),
    ("a3", "learner-a", 0),
    ("a4", "learner-a", 9_000),
    ("b1", "learner-b", 1_000),
]


def _operators(plan: object) -> list[str]:
    found: list[str] = []
    stack = [plan]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        operator = getattr(node, "operator_type", None) or (
            node.get("operatorType") if isinstance(node, dict) else None
        )
        args = getattr(node, "arguments", None) or (
            node.get("args") if isinstance(node, dict) else {}
        )
        found.append(f"{operator} {args.get('Details', '') if args else ''}".strip())
        children = getattr(node, "children", None) or (
            node.get("children") if isinstance(node, dict) else []
        )
        stack.extend(children or [])
    return found


async def main() -> int:
    uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:57687")
    auth = (os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"])
    report: dict[str, object] = {"check": "WP0-3b composite range index (learnerId, retDueAtMs)"}
    async with AsyncGraphDatabase.driver(uri, auth=auth) as driver:
        info = await driver.get_server_info()
        report["server_agent"] = info.agent
        components = await driver.execute_query(
            "CALL dbms.components() YIELD name, versions, edition RETURN versions[0] AS v, edition"
        )
        report["version"] = components.records[0]["v"]
        report["edition"] = components.records[0]["edition"]
        try:
            await driver.execute_query(
                f"CREATE RANGE INDEX {INDEX} IF NOT EXISTS "
                f"FOR (s:{LABEL}) ON (s.learnerId, s.retDueAtMs)"
            )
            await driver.execute_query("CALL db.awaitIndexes(60)")
            shown = await driver.execute_query(
                "SHOW INDEXES YIELD name, type, state, properties WHERE name = $name "
                "RETURN type, state, properties",
                name=INDEX,
            )
            report["index"] = [dict(r) for r in shown.records]
            await driver.execute_query(
                f"UNWIND $rows AS r CREATE (:{LABEL} {{id: r[0], learnerId: r[1], "
                "retDueAtMs: r[2]})",
                rows=[list(n) for n in NODES],
            )
            query = (
                f"MATCH (s:{LABEL}) WHERE s.learnerId = $learnerId "
                "AND s.retDueAtMs > 0 AND s.retDueAtMs <= $now RETURN s.id AS id"
            )
            result = await driver.execute_query(query, learnerId="learner-a", now=6_000)
            got = sorted(r["id"] for r in result.records)
            report["returned_ids"] = got
            explained = await driver.execute_query(
                "EXPLAIN " + query, learnerId="learner-a", now=6_000
            )
            operators = _operators(explained.summary.plan)
            report["plan"] = operators
            seeks = any(
                "IndexSeek" in op and f"{LABEL}(learnerId, retDueAtMs)" in op for op in operators
            )
            report["index_seek"] = seeks
            ok = (
                got == ["a1", "a2"]
                and seeks
                and bool(report["index"])
                and shown.records[0]["state"] == "ONLINE"
            )
            report["works"] = ok
        except Exception as exc:  # the check reports any failure as a result
            report["works"] = False
            report["error"] = f"{type(exc).__name__}: {exc}"
            ok = False
        finally:
            await driver.execute_query(f"MATCH (s:{LABEL}) DETACH DELETE s")
            await driver.execute_query(f"DROP INDEX {INDEX} IF EXISTS")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
