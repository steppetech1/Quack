"""T-DB1 (ТЗ Ф4 §8.2): the dbmate baseline creates exactly the tables of §6.1 (Т-§3.1); the
extending migrations after G0 (§0.4.4 п.4) add only their own tables and roll back one by one
(description per §8.15 п.3, D-A-128)."""

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

# Extending migrations after the baseline, oldest first: the tables each one adds.
EXTENSIONS: tuple[frozenset[tuple[str, str]], ...] = (
    frozenset({("core", "pairing_tokens")}),  # D-A-128: WP22.1, Т-§16.1
    frozenset(),  # D-A-128: its index by learner, CONCURRENTLY (Д-§6.2)
    frozenset(),  # D-A-137: `ops.turn_manifests.frozen_slots` (Т-§10.1)
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
    every = EXPECTED_TABLES.union(*EXTENSIONS)

    run_dbmate(url, "up")
    assert _tables(scratch_database) == every

    again = run_dbmate(url, "up")
    assert "Applying" not in again.stdout
    assert _tables(scratch_database) == every

    # `down` rolls back one migration, the newest first: down to the tables of §6.1, then none.
    present = set(every)
    for added in reversed(EXTENSIONS):
        run_dbmate(url, "down")
        present -= added
        assert _tables(scratch_database) == present
    assert present == EXPECTED_TABLES
    run_dbmate(url, "down")
    assert _tables(scratch_database) == set()

    run_dbmate(url, "up")
    assert _tables(scratch_database) == every
    run_dbmate(url, "drop")


_COLUMNS = (
    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
    "WHERE table_schema = 'core' AND table_name = 'pairing_tokens'"
)
_PRIMARY_KEY = (
    "SELECT a.attname FROM pg_index i "
    "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
    "WHERE i.indrelid = 'core.pairing_tokens'::regclass AND i.indisprimary"
)
_LEADING_INDEX_COLUMNS = (
    "SELECT a.attname FROM pg_index i "
    "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0] "
    "WHERE i.indrelid = 'core.pairing_tokens'::regclass"
)


def test_pairing_tokens_table(p4_database: str) -> None:
    """D-A-128 (WP22.1, Т-§16.1): one-time pairing token of a canvas — only its hash is stored;
    bound to the learner, session, canvas and, if any, task; lives until `expires_at`; spent at
    `used_at`. Indexed by learner for deletion (WP25)."""
    timestamp = "timestamp with time zone"
    with psycopg.connect(p4_database) as conn:
        columns = set(conn.execute(_COLUMNS).fetchall())
        primary_key = [row[0] for row in conn.execute(_PRIMARY_KEY).fetchall()]
        leading = {row[0] for row in conn.execute(_LEADING_INDEX_COLUMNS).fetchall()}
    assert columns == {
        ("token_hash", "text", "NO"),
        ("learner_id", "uuid", "NO"),
        ("session_id", "uuid", "NO"),
        ("canvas_id", "uuid", "NO"),
        ("task_id", "uuid", "YES"),
        ("created_at", timestamp, "NO"),
        ("expires_at", timestamp, "NO"),
        ("used_at", timestamp, "YES"),
    }
    assert primary_key == ["token_hash"]
    assert "learner_id" in leading


def test_turn_manifests_frozen_slots_column(p4_database: str) -> None:
    """D-A-137 (WP14.1, Т-§10.1): slots 3 and 4 frozen at the session start live in the
    manifest of the first turn; a nullable JSONB — later turns carry none."""
    query = (
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'ops' AND table_name = 'turn_manifests' "
        "AND column_name = 'frozen_slots'"
    )
    with psycopg.connect(p4_database) as conn:
        assert conn.execute(query).fetchall() == [("jsonb", "YES")]
