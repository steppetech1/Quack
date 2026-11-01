-- Phase 4 baseline (ТЗ Ф4 WP1.1, §6.1; Т-§3.1). Clean start (§2.3 row 1): no data is carried
-- over from the Alembic schema of Phases 1–3. After G0 only extending migrations (Д-§6.2).

-- migrate:up
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS content;
CREATE SCHEMA IF NOT EXISTS ops;

-- core -----------------------------------------------------------------------------------

-- The row is locked by append_events (Т-§2.3); seq is the learner's event counter.
CREATE TABLE core.learners (
    learner_id UUID PRIMARY KEY,
    seq BIGINT NOT NULL DEFAULT 0,
    email TEXT NOT NULL UNIQUE CHECK (email = lower(email)),
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Event fields: Т-§2.1. Append-only except deletion by the learner (Т-§2.9).
CREATE TABLE core.events (
    learner_id UUID NOT NULL,
    seq BIGINT NOT NULL,
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    type TEXT NOT NULL,
    schema_version BIGINT NOT NULL,
    actor TEXT NOT NULL,
    domain_id UUID NULL,
    session_id UUID NULL,
    causation_seq BIGINT NULL,
    correlation_id UUID NULL,
    idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    source_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (learner_id, seq),
    UNIQUE (learner_id, idempotency_key)
);
ALTER TABLE core.events ALTER COLUMN payload SET COMPRESSION lz4;
CREATE INDEX idx_events_session_seq ON core.events (session_id, seq);
-- Full-text search (Т-§3.1, Т-§22.2): only types with a `text` field (ТЗ Ф4 WP2.3).
-- Queries must use the same expression and predicate.
CREATE INDEX idx_events_fts_en ON core.events
    USING GIN (to_tsvector('english', payload ->> 'text'))
    WHERE type IN (
        'message.sent', 'answer.submitted', 'element.responded', 'turn.committed',
        'canvas.line_committed', 'voice.transcribed', 'item.flagged_unclear',
        'chunk.indexed', 'artifact.updated'
    );

CREATE TABLE core.auth_sessions (
    token_hash TEXT PRIMARY KEY,
    learner_id UUID NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('full', 'companion')),
    canvas_id UUID NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ NULL
);
CREATE INDEX idx_auth_sessions_learner ON core.auth_sessions (learner_id);

CREATE TABLE core.auth_links (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ NULL
);

-- content --------------------------------------------------------------------------------

-- Т-§22.9: «Сессий без домена нет».
CREATE TABLE content.sessions (
    session_id UUID PRIMARY KEY,
    learner_id UUID NOT NULL,
    domain_id UUID NOT NULL,
    intent TEXT NULL,
    topic_id UUID NULL,
    hub_id UUID NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ NULL,
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_sessions_learner ON content.sessions (learner_id, started_at);

CREATE TABLE content.session_plans (
    session_id UUID PRIMARY KEY,
    learner_id UUID NOT NULL,
    plan JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'active')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_session_plans_learner ON content.session_plans (learner_id);

CREATE TABLE content.study_plans (
    learner_id UUID NOT NULL,
    hub_id UUID NOT NULL,
    plan JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (learner_id, hub_id)
);

CREATE TABLE content.canvas_boards (
    canvas_id UUID PRIMARY KEY,
    learner_id UUID NOT NULL,
    session_id UUID NULL,
    task_id UUID NULL,
    doc JSONB NOT NULL DEFAULT '{}'::jsonb,
    version BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_canvas_boards_learner ON content.canvas_boards (learner_id);

CREATE TABLE content.tasks (
    task_id UUID PRIMARY KEY,
    learner_id UUID NOT NULL,
    domain_id UUID NULL,
    source TEXT NOT NULL,
    version BIGINT NOT NULL DEFAULT 1,
    condition JSONB NOT NULL,
    answer_spec JSONB NOT NULL,
    solution_space JSONB NULL,
    solution_space_version TEXT NULL,
    created_seq BIGINT NOT NULL
);
CREATE INDEX idx_tasks_learner_domain ON content.tasks (learner_id, domain_id);

CREATE TABLE content.materials (
    material_id UUID PRIMARY KEY,
    learner_id UUID NOT NULL,
    domain_id UUID NULL,
    session_id UUID NULL,
    object_key TEXT NOT NULL,
    mime TEXT NOT NULL,
    pages_count BIGINT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_materials_learner_domain ON content.materials (learner_id, domain_id);

CREATE TABLE content.material_pages (
    material_id UUID NOT NULL,
    page BIGINT NOT NULL,
    learner_id UUID NOT NULL,
    text TEXT NOT NULL,
    image_key TEXT NULL,
    PRIMARY KEY (material_id, page)
);
CREATE INDEX idx_material_pages_learner ON content.material_pages (learner_id);

CREATE TABLE content.artifacts (
    artifact_id UUID PRIMARY KEY,
    learner_id UUID NOT NULL,
    kind TEXT NOT NULL,
    domain_id UUID NULL,
    topic_id UUID NULL,
    hub_id UUID NULL,
    current_version BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX idx_artifacts_learner ON content.artifacts (learner_id, domain_id);

CREATE TABLE content.artifact_versions (
    artifact_id UUID NOT NULL,
    version BIGINT NOT NULL,
    learner_id UUID NOT NULL,
    content JSONB NOT NULL,
    skill_version BIGINT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_id, version)
);
CREATE INDEX idx_artifact_versions_learner ON content.artifact_versions (learner_id);

CREATE TABLE content.learner_skills (
    skill_id UUID NOT NULL,
    version BIGINT NOT NULL,
    learner_id UUID NOT NULL,
    name TEXT NOT NULL,
    instruction TEXT NOT NULL,
    bindings JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (skill_id, version)
);
CREATE INDEX idx_learner_skills_learner ON content.learner_skills (learner_id);

CREATE TABLE content.exports (
    export_id UUID PRIMARY KEY,
    learner_id UUID NOT NULL,
    status TEXT NOT NULL,
    object_key TEXT NULL,
    expires_at TIMESTAMPTZ NULL
);
CREATE INDEX idx_exports_learner ON content.exports (learner_id);

-- ops ------------------------------------------------------------------------------------

-- Fields: Т-§13.1 (ТЗ Ф4 WP7.4). learner_id is NULL only for periodic jobs (WP7.5).
CREATE TABLE ops.jobs (
    job_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    learner_id UUID NULL,
    session_id UUID NULL,
    priority_class TEXT NOT NULL CHECK (priority_class IN ('P1', 'P2', 'P3')),
    lock_key TEXT NULL,
    coalesce_key TEXT NULL,
    required_seq BIGINT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN (
            'queued', 'running', 'succeeded', 'failed', 'cancel_requested', 'cancelled',
            'skipped_budget'
        )
    ),
    attempts BIGINT NOT NULL DEFAULT 0,
    run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at TIMESTAMPTZ NULL,
    visible BOOLEAN NOT NULL DEFAULT false,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT NULL
);
CREATE UNIQUE INDEX uq_jobs_running_lock ON ops.jobs (lock_key) WHERE status = 'running';
CREATE UNIQUE INDEX uq_jobs_queued_coalesce ON ops.jobs (coalesce_key) WHERE status = 'queued';
CREATE INDEX idx_jobs_claim ON ops.jobs (status, priority_class, run_at, created_at);
CREATE INDEX idx_jobs_lock_required_seq ON ops.jobs (lock_key, required_seq);

CREATE TABLE ops.coverage (
    learner_id UUID NOT NULL,
    episode_seq BIGINT NOT NULL,
    extractor TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('extracted', 'nothing_to_extract')),
    observation_seq BIGINT NULL,
    PRIMARY KEY (learner_id, episode_seq, extractor)
);

CREATE TABLE ops.event_types_seen (
    type TEXT NOT NULL,
    schema_version BIGINT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (type, schema_version)
);

-- Copy of Learner.projectedSeq, updated after the Neo4j commit (Т-§2.4).
CREATE TABLE ops.projector_offsets (
    learner_id UUID PRIMARY KEY,
    seq BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ops.dispatcher_offsets (
    learner_id UUID PRIMARY KEY,
    last_dispatched_seq BIGINT NOT NULL DEFAULT 0,
    counters JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ops.projector_failures (
    learner_id UUID PRIMARY KEY,
    seq BIGINT NOT NULL,
    error TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Т-§3.2: Phase 3 columns + effort, doctrine_version, digest_id, chunk_ids, similar_ids,
-- context_text (sampled turns only). Keyed by turn.committed.manifest_id. Versions are
-- `<role>@N` and `renderer@N` (Т-§11.6, Т-§10.6).
CREATE TABLE ops.turn_manifests (
    manifest_id UUID PRIMARY KEY,
    learner_id UUID NOT NULL,
    session_id UUID NOT NULL,
    prompt_version TEXT NOT NULL,
    doctrine_version TEXT NULL,
    renderer_version TEXT NULL,
    role TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT NULL,
    projected_seq BIGINT NOT NULL,
    overlay_seqs BIGINT[] NOT NULL DEFAULT '{}',
    label_table JSONB NOT NULL DEFAULT '{}'::jsonb,
    digest_id UUID NULL,
    chunk_ids UUID[] NOT NULL DEFAULT '{}',
    similar_ids UUID[] NOT NULL DEFAULT '{}',
    context_text TEXT NULL,
    degraded_mode TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_turn_manifests_session ON ops.turn_manifests (learner_id, session_id, created_at);

-- Т-§11.4: one row per model call.
CREATE TABLE ops.llm_usage (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    learner_id UUID NULL,
    session_id UUID NULL,
    role TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NULL,
    effort TEXT NULL,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    cached_input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    reasoning_tokens BIGINT NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    incomplete BOOLEAN NOT NULL DEFAULT false,
    retried BOOLEAN NOT NULL DEFAULT false,
    latency_ms BIGINT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_llm_usage_learner ON ops.llm_usage (learner_id, created_at);
CREATE INDEX idx_llm_usage_session ON ops.llm_usage (session_id);

-- Т-§19.5, Д-§8.5: the fact of a deletion without content.
CREATE TABLE ops.deletion_log (
    deletion_id UUID PRIMARY KEY,
    learner_id UUID NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('topic', 'domain', 'account')),
    target_id UUID NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL
);

-- migrate:down
DROP SCHEMA ops CASCADE;
DROP SCHEMA content CASCADE;
DROP SCHEMA core CASCADE;
