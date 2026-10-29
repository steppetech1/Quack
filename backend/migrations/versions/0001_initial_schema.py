"""Initial schema: auth, core, ops (spec §3).

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-29
"""

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

# Event types whose payload carries a searchable `text` field (spec §3.2, §5.2 note).
FTS_EVENT_TYPES = (
    "turn.student_replied",
    "turn.teacher_replied",
    "artifact.note_created",
    "artifact.note_updated",
    "artifact.cheatsheet_updated",
    "session.digest_created",
)

UPGRADE_SQL = [
    "CREATE SCHEMA IF NOT EXISTS auth",
    "CREATE SCHEMA IF NOT EXISTS core",
    "CREATE SCHEMA IF NOT EXISTS ops",
    # --- auth (spec §3.1) ---
    """
    CREATE TABLE auth.users (
        id UUID PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT NULL,
        preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        deleted_at TIMESTAMPTZ NULL
    )
    """,
    # --- core (spec §3.2) ---
    """
    CREATE TABLE core.learner_seq (
        learner_id UUID PRIMARY KEY,
        last_seq BIGINT NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE core.events (
        learner_id UUID NOT NULL,
        seq BIGINT NOT NULL,
        event_id UUID NOT NULL UNIQUE,
        type TEXT NOT NULL,
        schema_version SMALLINT NOT NULL DEFAULT 1,
        actor TEXT NOT NULL,
        session_id UUID NULL,
        turn_id UUID NULL,
        domain_id UUID NULL,
        correlation_id UUID NULL,
        causation_id UUID NULL,
        idempotency_key TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        payload JSONB NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (learner_id, seq),
        CONSTRAINT uq_events_idempotency UNIQUE (learner_id, idempotency_key)
    )
    """,
    "CREATE INDEX idx_events_session_seq ON core.events (learner_id, session_id, seq)",
    "CREATE INDEX idx_events_fts_en ON core.events "
    "USING GIN (to_tsvector('english', coalesce(payload->>'text', ''))) "
    "WHERE type IN (" + ", ".join(f"'{t}'" for t in FTS_EVENT_TYPES) + ")",
    """
    CREATE TABLE core.sessions (
        id UUID PRIMARY KEY,
        learner_id UUID NOT NULL,
        domain_id UUID NOT NULL,
        topic_id UUID NULL,
        mode TEXT NOT NULL CHECK (mode IN ('learn', 'practice', 'review')),
        status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'abandoned')),
        goal TEXT NULL,
        plan JSONB NOT NULL DEFAULT '[]'::jsonb,
        last_turn_index INT NOT NULL DEFAULT -1,
        last_checkpoint_turn INT NOT NULL DEFAULT -1,
        tokens_used INT NOT NULL DEFAULT 0,
        started_at TIMESTAMPTZ NOT NULL,
        last_activity_at TIMESTAMPTZ NOT NULL,
        ended_at TIMESTAMPTZ NULL,
        start_seq BIGINT NOT NULL,
        end_seq BIGINT NULL
    )
    """,
    "CREATE UNIQUE INDEX uq_sessions_one_active ON core.sessions (learner_id) "
    "WHERE status = 'active'",
    "CREATE INDEX idx_sessions_learner ON core.sessions (learner_id, started_at)",
    """
    CREATE TABLE core.artifacts (
        id UUID PRIMARY KEY,
        learner_id UUID NOT NULL,
        domain_id UUID NOT NULL,
        topic_id UUID NULL,
        session_id UUID NULL,
        kind TEXT NOT NULL CHECK (kind IN ('digest', 'note', 'cheatsheet')),
        title TEXT NOT NULL,
        content_md TEXT NOT NULL,
        version INT NOT NULL DEFAULT 1,
        source_seq BIGINT NOT NULL,
        is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX uq_artifacts_topic_cheatsheet ON core.artifacts (learner_id, topic_id) "
    "WHERE kind = 'cheatsheet' AND NOT is_deleted",
    "CREATE INDEX idx_artifacts_learner ON core.artifacts (learner_id, domain_id)",
    """
    CREATE TABLE core.domains (
        id UUID PRIMARY KEY,
        learner_id UUID NOT NULL,
        name TEXT NOT NULL,
        slug TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('onboarding', 'structuring', 'active', 'archived')),
        has_materials BOOLEAN NOT NULL DEFAULT FALSE,
        active_structure_version INT NOT NULL DEFAULT 0,
        created_seq BIGINT NOT NULL,
        updated_seq BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_domains_learner_slug UNIQUE (learner_id, slug)
    )
    """,
    """
    CREATE TABLE core.task_bank (
        id UUID PRIMARY KEY,
        learner_id UUID NOT NULL,
        domain_id UUID NOT NULL,
        topic_id UUID NOT NULL,
        family_id TEXT NOT NULL,
        difficulty REAL NOT NULL,
        kind TEXT NOT NULL,
        prompt_text TEXT NOT NULL,
        reference_solution JSONB NOT NULL,
        target_skills JSONB NOT NULL,
        target_misconceptions JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_seq BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX idx_task_bank_topic ON core.task_bank (learner_id, topic_id, difficulty)",
    """
    CREATE TABLE core.llm_outbox (
        id UUID PRIMARY KEY,
        learner_id UUID NOT NULL,
        idempotency_key TEXT NOT NULL,
        role TEXT NOT NULL,
        prompt_version INT NOT NULL,
        model TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'failed')),
        request_hash TEXT NOT NULL,
        response_json JSONB NULL,
        usage_json JSONB NULL,
        pending_events JSONB NULL,
        committed_seq BIGINT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_llm_outbox_key UNIQUE (learner_id, idempotency_key)
    )
    """,
    # --- ops (spec §3.3) ---
    """
    CREATE TABLE ops.jobs (
        id UUID PRIMARY KEY,
        kind TEXT NOT NULL,
        learner_id UUID NULL,
        lock_key TEXT NOT NULL,
        coalesce_key TEXT NULL,
        priority SMALLINT NOT NULL DEFAULT 100,
        status TEXT NOT NULL
            CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        attempt SMALLINT NOT NULL DEFAULT 0,
        max_attempts SMALLINT NOT NULL DEFAULT 5,
        run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
        locked_by TEXT NULL,
        heartbeat_at TIMESTAMPTZ NULL,
        last_error TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX uq_jobs_running_lock ON ops.jobs (lock_key) WHERE status = 'running'",
    "CREATE UNIQUE INDEX uq_jobs_queued_coalesce ON ops.jobs (coalesce_key) "
    "WHERE status = 'queued'",
    "CREATE INDEX idx_jobs_claim ON ops.jobs (priority, created_at) WHERE status = 'queued'",
    """
    CREATE TABLE ops.dlq (
        id UUID PRIMARY KEY,
        source TEXT NOT NULL CHECK (source IN ('job', 'projector')),
        kind TEXT NOT NULL,
        learner_id UUID NULL,
        seq BIGINT NULL,
        job_id UUID NULL,
        payload JSONB NOT NULL,
        error_type TEXT NOT NULL,
        error_message TEXT NOT NULL,
        stacktrace TEXT NULL,
        replayed_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX idx_dlq_pending ON ops.dlq (source, learner_id) WHERE replayed_at IS NULL",
    """
    CREATE TABLE ops.llm_ledger (
        id UUID PRIMARY KEY,
        learner_id UUID NULL,
        session_id UUID NULL,
        turn_id UUID NULL,
        role TEXT NOT NULL,
        prompt_version INT NOT NULL,
        prompt_hash TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INT NOT NULL,
        output_tokens INT NOT NULL,
        cached_tokens INT NOT NULL DEFAULT 0,
        latency_ms INT NOT NULL,
        status TEXT NOT NULL,
        error_code TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX idx_llm_ledger_learner_day ON ops.llm_ledger (learner_id, created_at)",
    """
    CREATE TABLE ops.deletion_tombstones (
        learner_id UUID PRIMARY KEY,
        requested_at TIMESTAMPTZ NOT NULL,
        purged_at TIMESTAMPTZ NULL,
        status TEXT NOT NULL CHECK (status IN ('pending_purge', 'purged'))
    )
    """,
    # Copy of the projector watermark: only used to find learners with unapplied events
    # (Т-§2.4, implementation guideline); its lag is harmless.
    """
    CREATE TABLE ops.projector_offsets (
        learner_id UUID PRIMARY KEY,
        applied_seq BIGINT NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Pairs (type, schema_version) ever written; used for rollback checks (Т-§2.3, Д-§6.3).
    """
    CREATE TABLE ops.event_types_seen (
        type TEXT NOT NULL,
        schema_version SMALLINT NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (type, schema_version)
    )
    """,
]


def upgrade() -> None:
    for statement in UPGRADE_SQL:
        op.execute(statement)


def downgrade() -> None:
    for schema in ("ops", "core", "auth"):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
