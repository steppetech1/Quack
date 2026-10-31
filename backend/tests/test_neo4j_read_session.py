"""WP4.1: READ sessions of Neo4j (ТЗ Фазы 3 §8.2, T-A9b)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from neo4j import AsyncDriver
from neo4j.exceptions import Neo4jError

from quack.db.neo4j import read_session
from quack.graph.neo4j_store import Neo4jGraphStore


async def test_read_session_reads(neo4j: AsyncDriver) -> None:
    async with read_session(neo4j) as session:
        result = await session.run("RETURN 1 AS one")
        record = await result.single()
    assert record is not None and record["one"] == 1


async def test_read_session_rejects_writes(neo4j: AsyncDriver) -> None:
    """T-A9b [ПРОВЕРИТЬ Д-§14.2]: `CREATE` through `read_session` fails in the driver and
    the node is not created."""
    probe = str(uuid4())
    with pytest.raises(Neo4jError):
        async with read_session(neo4j) as session:
            result = await session.run("CREATE (:Probe {id: $id})", {"id": probe})
            await result.consume()
    async with neo4j.session() as session:
        result = await session.run("MATCH (p:Probe {id: $id}) RETURN count(p) AS n", {"id": probe})
        record = await result.single()
    assert record is not None and record["n"] == 0


async def test_graph_store_reads_through_read_session(neo4j: AsyncDriver) -> None:
    """D-C-016: the batch reads of `Neo4jGraphStore` open READ sessions; a write query sent
    through `_read` fails in the driver."""
    store = Neo4jGraphStore(neo4j, str(uuid4()))
    assert await store._read("RETURN 1 AS one") == [{"one": 1}]  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(Neo4jError):
        await store._read("CREATE (:Probe {id: $id})", id=str(uuid4()))  # pyright: ignore[reportPrivateUsage]
