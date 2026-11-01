"""T-DB1 (ТЗ Ф4 §8.2): the dbmate baseline creates exactly the tables of §6.1 (Т-§3.1)."""

from __future__ import annotations

import psycopg
import pytest

from tests.db.fixtures import database_url, p4_database_name, run_dbmate

# ТЗ Ф4 §6.1.
EXPECTED_TABLES = frozenset(
    {
        ("core", "learners"),
        ("core", "events"),
        ("core", "auth_sessions"),
        ("core", "auth_links"),
        ("content", "sessions"),
        ("content", "session_plans"),
        ("content", "study_plans"),
        ("content", "canvas_boards"),
        ("content", "tasks"),
        ("content", "materials"),
        ("content", "material_pages"),
        ("content", "artifacts"),
        ("content", "artifact_versions"),
        ("content", "learner_skills"),
        ("content", "exports"),
        ("ops", "jobs"),
        ("ops", "coverage"),
        ("ops", "event_types_seen"),
        ("ops", "projector_offsets"),
        ("ops", "dispatcher_offsets"),
        ("ops", "projector_failures"),
        ("ops", "turn_manifests"),
        ("ops", "llm_usage"),
        ("ops", "deletion_log"),
    }
)

# dbmate keeps its own bookkeeping table in `public`; everything of ours is in core/content/ops.
_TABLES = (
    "SELECT table_schema, table_name FROM information_schema.tables "
    "WHERE table_type = 'BASE TABLE' "
    "AND table_schema NOT IN ('pg_catalog', 'information_schema', 'public')"
)


def _tables(name: str) -> set[tuple[str, str]]:
    with psycopg.connect(database_url(name)) as conn:
        return {(schema, table) for schema, table in conn.execute(_TABLES).fetchall()}


@pytest.fixture
def scratch_database() -> str:
    name = p4_database_name() + "_migrate"
    run_dbmate(database_url(name, for_dbmate=True), "drop")
    run_dbmate(database_url(name, for_dbmate=True), "create")
    return name


def test_baseline_migration_matches_t31(scratch_database: str) -> None:
    url = database_url(scratch_database, for_dbmate=True)

    run_dbmate(url, "up")
    assert _tables(scratch_database) == EXPECTED_TABLES

    again = run_dbmate(url, "up")
    assert "Applying" not in again.stdout
    assert _tables(scratch_database) == EXPECTED_TABLES

    run_dbmate(url, "down")
    assert _tables(scratch_database) == set()

    run_dbmate(url, "up")
    assert _tables(scratch_database) == EXPECTED_TABLES
    run_dbmate(url, "drop")
