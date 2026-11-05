"""ТЗ Ф4 WP3.1: the Neo4j schema of Phase 4 (Т-§3.4.6–3.4.8): unique `id` per label, range
indexes on `learnerId`, the composite index of review candidates, vector indexes with their
filter properties."""

from __future__ import annotations

from neo4j import AsyncDriver

from quack.config import Settings
from quack.db.neo4j import P4_UNIQUE_ID_LABELS, VECTOR_INDEXES, schema_statements, verify_schema


def test_statements_are_repeatable_and_use_the_configured_dimension() -> None:
    statements = schema_statements(embedding_dim=32)
    assert all("IF NOT EXISTS" in s for s in statements)
    vectors = [s for s in statements if "VECTOR INDEX" in s]
    assert len(vectors) == len(VECTOR_INDEXES) == 5
    assert all("`vector.dimensions`: 32" in s for s in vectors)
    assert any("evidence_emb" in s and "n.searchable" in s for s in vectors)
    assert not any("topic_emb" in s and "searchable" in s for s in vectors)


def test_default_dimension_comes_from_settings() -> None:
    expected = f"`vector.dimensions`: {Settings().embedding_dim}"
    assert all(expected in s for s in schema_statements() if "VECTOR INDEX" in s)


async def test_schema_created_on_start(neo4j: AsyncDriver) -> None:
    """T-P7: constraints, range and vector indexes with filters are created `IF NOT EXISTS` by
    `verify_schema` (the start of `projector`); a repeated start passes without errors."""
    assert await verify_schema(neo4j) == []
    assert await verify_schema(neo4j) == []  # the second start
    async with neo4j.session() as session:
        result = await session.run("SHOW INDEXES YIELD name, type RETURN name, type")
        indexes = {r["name"]: r["type"] async for r in result}
        result = await session.run("SHOW CONSTRAINTS YIELD name RETURN collect(name) AS n")
        record = await result.single()
    assert {name for name, _, _ in VECTOR_INDEXES} <= {
        n for n, t in indexes.items() if t == "VECTOR"
    }
    assert indexes["skill_learner_ret_due"] == "RANGE"
    assert record is not None
    assert {f"{label.lower()}_id_unique" for label in P4_UNIQUE_ID_LABELS} <= set(record["n"])
