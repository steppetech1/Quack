"""Neo4j AsyncDriver and schema (constraints/indexes, spec §4.1, Д-§6.2)."""

from __future__ import annotations

from typing import LiteralString, cast

from neo4j import AsyncDriver, AsyncGraphDatabase, Query

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


def schema_statements() -> list[str]:
    statements = [
        f"CREATE CONSTRAINT {label.lower()}_id_unique IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        for label in UNIQUE_ID_LABELS
    ]
    for name, label, props in INDEXES:
        cols = ", ".join(f"n.{p}" for p in props)
        statements.append(f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON ({cols})")
    return statements


async def verify_schema(driver: AsyncDriver) -> list[str]:
    """Idempotently create constraints and indexes; return names missing after creation."""
    # IF NOT EXISTS on an existing object produces an informational notification only.
    async with driver.session(notifications_min_severity="OFF") as session:
        for statement in schema_statements():
            await session.run(cypher(statement))
        await session.run("CALL db.awaitIndexes(300)")
        result = await session.run("SHOW CONSTRAINTS YIELD name RETURN collect(name) AS names")
        record = await result.single()
        constraints = set(record["names"]) if record else set()
        result = await session.run("SHOW INDEXES YIELD name RETURN collect(name) AS names")
        record = await result.single()
        indexes = set(record["names"]) if record else set()
    expected_constraints = {f"{label.lower()}_id_unique" for label in UNIQUE_ID_LABELS}
    expected_indexes = {name for name, _, _ in INDEXES}
    return sorted((expected_constraints - constraints) | (expected_indexes - indexes))
