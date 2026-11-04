"""WP0 п.3а (ТЗ Фазы 4): does `SEARCH … VECTOR INDEX … WHERE` with the in-index filter
`learnerId = $id AND searchable = true` work on Neo4j Community (`Т-§3.4.6`, `Т-§1.10`)?

Creates a throwaway vector index on the `Wp0CheckChunk` label with the filter properties
declared at creation, writes nodes of two learners with `searchable` true and false, runs the
`Т-§3.4.6` query shape and checks that only the filtered nodes come back. Drops the index and
the nodes afterwards. Prints one JSON line; exit 0 — works, 1 — does not.

Not part of `pytest`. Run from `backend/` against a disposable Neo4j:

    NEO4J_URI=bolt://127.0.0.1:57688 NEO4J_USER=neo4j NEO4J_PASSWORD=... \
        uv run python scripts/checks/vector_search_filter.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from neo4j import AsyncGraphDatabase

LABEL = "Wp0CheckChunk"
INDEX = "wp0_check_chunk_emb"
DIM = 4

NODES = [
    # id, learnerId, searchable, embedding
    ("a1", "learner-a", True, [1.0, 0.0, 0.0, 0.0]),
    ("a2", "learner-a", True, [0.9, 0.1, 0.0, 0.0]),
    ("a3", "learner-a", False, [1.0, 0.0, 0.0, 0.0]),
    ("b1", "learner-b", True, [1.0, 0.0, 0.0, 0.0]),
]


async def main() -> int:
    uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:57688")
    auth = (os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"])
    report: dict[str, object] = {"check": "WP0-3a vector SEARCH with in-index filter"}
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
                f"CREATE VECTOR INDEX {INDEX} IF NOT EXISTS FOR (c:{LABEL}) ON c.embedding "
                "WITH [c.learnerId, c.searchable] "
                f"OPTIONS {{indexConfig: {{`vector.dimensions`: {DIM}, "
                "`vector.similarity_function`: 'cosine'}}"
            )
            await driver.execute_query("CALL db.awaitIndexes(60)")
            await driver.execute_query(
                f"UNWIND $rows AS r CREATE (c:{LABEL} {{id: r[0], learnerId: r[1], "
                "searchable: r[2], embedding: r[3]})",
                rows=[list(n) for n in NODES],
            )
            result = await driver.execute_query(
                f"CYPHER 25 MATCH (c:{LABEL}) "
                f"SEARCH c IN (VECTOR INDEX {INDEX} FOR $q "
                "WHERE c.learnerId = $learnerId AND c.searchable = true LIMIT 30) "
                "SCORE AS score RETURN c.id AS id, score ORDER BY score DESC",
                q=[1.0, 0.0, 0.0, 0.0],
                learnerId="learner-a",
            )
            got = [r["id"] for r in result.records]
            report["returned_ids"] = got
            ok = sorted(got) == ["a1", "a2"]
            report["works"] = ok
        except Exception as exc:  # the check reports any failure as a result
            report["works"] = False
            report["error"] = f"{type(exc).__name__}: {exc}"
            ok = False
        finally:
            await driver.execute_query(f"MATCH (c:{LABEL}) DETACH DELETE c")
            await driver.execute_query(f"DROP INDEX {INDEX} IF EXISTS")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
