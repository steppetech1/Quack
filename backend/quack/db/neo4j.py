"""Neo4j AsyncDriver and schema (constraints/indexes, spec §4.1, Д-§6.2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import LiteralString, cast

from neo4j import READ_ACCESS, AsyncDriver, AsyncGraphDatabase, AsyncSession, Query

from quack.config import Settings

UNIQUE_ID_LABELS: tuple[str, ...] = (
    "Learner",
    "Domain",
    "Topic",
    "Skill",
    "Misconception",
    "Task",
    "Evidence",
    "Exposure",
    "Artifact",
    "Alias",
)

# ТЗ Ф4 WP3.1 (Т-§3.4.2, §6.2): labels added in Phase 4; they coexist with the Phase 3 labels
# until WP1.3 (D-A-103).
P4_UNIQUE_ID_LABELS: tuple[str, ...] = ("Hub", "Option", "Material", "Chunk", "Digest", "Profile")
# Т-§3.4.8: a range index on `learnerId` of every label that belongs to a learner.
P4_RANGE_LABELS: tuple[str, ...] = (
    "Domain",
    "Topic",
    "Skill",
    "Hub",
    "Evidence",
    "Exposure",
    "Misconception",
    "Task",
    "Option",
    "Material",
    "Chunk",
    "Digest",
    "Profile",
    "Alias",
)
# Т-§3.4.8, WP0 п.3б: candidates for review (`retDueAtMs`).
P4_SKILL_DUE_INDEX = ("skill_learner_ret_due", "Skill", ("learnerId", "retDueAtMs"))
# Т-§3.4.6: (index, label, filter properties declared at creation).
VECTOR_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("evidence_emb", "Evidence", ("learnerId", "domainId", "searchable")),
    ("misconception_emb", "Misconception", ("learnerId", "domainId", "searchable")),
    ("chunk_emb", "Chunk", ("learnerId", "domainId", "searchable")),
    ("skill_emb", "Skill", ("learnerId", "domainId")),
    ("topic_emb", "Topic", ("learnerId", "domainId")),
)

# (index name, label, properties)
INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("topic_learner_id", "Topic", ("learnerId",)),
    ("skill_learner_id", "Skill", ("learnerId",)),
    ("skill_domain_id", "Skill", ("domainId",)),
    ("misconception_learner_id", "Misconception", ("learnerId",)),
    ("evidence_learner_id", "Evidence", ("learnerId",)),
    ("exposure_learner_id", "Exposure", ("learnerId",)),
    ("skill_learner_next_review", "Skill", ("learnerId", "nextReviewAt")),
    ("topic_learner_domain_status", "Topic", ("learnerId", "domainId", "status")),
)


def literal(query: str) -> LiteralString:
    """Mark a query assembled from code constants (never from user input) as literal."""
    return cast(LiteralString, query)


def cypher(query: str) -> Query:
    """Wrap a query assembled from code constants (never from user input)."""
    return Query(cast(LiteralString, query))


def create_driver(settings: Settings) -> AsyncDriver:
    return AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )


def p4_range_indexes() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Phase 4 range indexes that Phase 3 `INDEXES` do not already cover: Neo4j refuses a
    second index with the same label and properties under another name."""
    have = {(label, props) for _, label, props in INDEXES}
    per_label = tuple(
        (f"{label.lower()}_learner", label, ("learnerId",))
        for label in P4_RANGE_LABELS
        if (label, ("learnerId",)) not in have
    )
    return (*per_label, P4_SKILL_DUE_INDEX)


def schema_statements(embedding_dim: int | None = None) -> list[str]:
    """Constraints and indexes of Phase 3 and Phase 4 (WP3.1); `IF NOT EXISTS` makes them
    repeatable. Vector indexes have the dimension `embedding_dim` and declare their filter
    properties (Т-§3.4.6)."""
    dim = Settings().embedding_dim if embedding_dim is None else embedding_dim
    statements = [
        f"CREATE CONSTRAINT {label.lower()}_id_unique IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        for label in (*UNIQUE_ID_LABELS, *P4_UNIQUE_ID_LABELS)
    ]
    for name, label, props in (*INDEXES, *p4_range_indexes()):
        cols = ", ".join(f"n.{p}" for p in props)
        statements.append(f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON ({cols})")
    for name, label, filters in VECTOR_INDEXES:
        with_filter = ", ".join(f"n.{p}" for p in filters)
        statements.append(
            f"CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (n:{label}) ON n.embedding "
            f"WITH [{with_filter}] OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, "
            "`vector.similarity_function`: 'cosine'}}"
        )
    return statements


@asynccontextmanager
async def read_session(driver: AsyncDriver) -> AsyncIterator[AsyncSession]:
    """A Neo4j session in READ access mode (law 3, ТЗ Фазы 3 WP4.1). Code outside the
    projector, ops rebuild/purge and `verify_schema` opens Neo4j sessions only through this."""
    async with driver.session(default_access_mode=READ_ACCESS) as session:
        yield session


async def verify_schema(driver: AsyncDriver, embedding_dim: int | None = None) -> list[str]:
    """Idempotently create constraints and indexes; return names missing after creation."""
    # IF NOT EXISTS on an existing object produces an informational notification only.
    async with driver.session(notifications_min_severity="OFF") as session:
        for statement in schema_statements(embedding_dim):
            await session.run(cypher(statement))
        await session.run("CALL db.awaitIndexes(300)")
        result = await session.run("SHOW CONSTRAINTS YIELD name RETURN collect(name) AS names")
        record = await result.single()
        constraints = set(record["names"]) if record else set()
        result = await session.run("SHOW INDEXES YIELD name RETURN collect(name) AS names")
        record = await result.single()
        indexes = set(record["names"]) if record else set()
    expected_constraints = {
        f"{label.lower()}_id_unique" for label in (*UNIQUE_ID_LABELS, *P4_UNIQUE_ID_LABELS)
    }
    expected_indexes = {name for name, _, _ in (*INDEXES, *p4_range_indexes(), *VECTOR_INDEXES)}
    return sorted((expected_constraints - constraints) | (expected_indexes - indexes))
