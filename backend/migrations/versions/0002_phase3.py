"""Phase 3: projection readiness, dispatcher offsets, turn manifests, ledger usage
(ТЗ Фазы 3 §6.1).

Expand-only (Д-§6.2): one nullable column on `ops.jobs`, two new tables, three nullable
columns on `ops.llm_ledger`. No column is rewritten, the code of Phase 2 runs on this schema.
`ops.llm_ledger.status` has no CHECK constraint, so the new status `incomplete` (WP5.2) needs
no change here.

Revision ID: 0002_phase3
Revises: 0001_initial_schema
Create Date: 2026-10-31
"""

from alembic import op

revision = "0002_phase3"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

UPGRADE_SQL = [
    # WP3.1 (Т-§13.1): a job that reads the graph waits for projector_offsets >= required_seq;
    # NULL = the job reads only the log (§2.3 row 10).
    "ALTER TABLE ops.jobs ADD COLUMN required_seq BIGINT NULL",
    # WP11.1 (Т-§2.10): dispatcher offset per learner (§2.3 row 12); no row = 0.
    """
    CREATE TABLE ops.dispatcher_offsets (
        learner_id UUID PRIMARY KEY,
        last_dispatched_seq BIGINT NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # WP14.2 (Т-§3.2): turn manifest, written with turn.teacher_replied; no context text.
    """
    CREATE TABLE ops.turn_manifests (
        turn_id UUID PRIMARY KEY,
        learner_id UUID NOT NULL,
        session_id UUID NOT NULL,
        prompt_version INT NOT NULL,
        renderer_version INT NULL,
        role TEXT NOT NULL,
        model TEXT NOT NULL,
        reasoning_effort TEXT NULL,
        projected_seq BIGINT NOT NULL,
        overlay_seqs BIGINT[] NOT NULL DEFAULT '{}',
        label_table JSONB NOT NULL DEFAULT '{}'::jsonb,
        degraded_mode TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX idx_turn_manifests_session ON ops.turn_manifests (session_id, created_at)",
    # WP5.7 (Т-§11.1): usage by kind and the effort of the call (D-B-003: cached_tokens stays).
    "ALTER TABLE ops.llm_ledger ADD COLUMN cached_input_tokens BIGINT NULL",
    "ALTER TABLE ops.llm_ledger ADD COLUMN reasoning_tokens BIGINT NULL",
    "ALTER TABLE ops.llm_ledger ADD COLUMN reasoning_effort TEXT NULL",
]

DOWNGRADE_SQL = [
    "ALTER TABLE ops.llm_ledger DROP COLUMN IF EXISTS reasoning_effort",
    "ALTER TABLE ops.llm_ledger DROP COLUMN IF EXISTS reasoning_tokens",
    "ALTER TABLE ops.llm_ledger DROP COLUMN IF EXISTS cached_input_tokens",
    "DROP TABLE IF EXISTS ops.turn_manifests",
    "DROP TABLE IF EXISTS ops.dispatcher_offsets",
    "ALTER TABLE ops.jobs DROP COLUMN IF EXISTS required_seq",
]


def upgrade() -> None:
    for statement in UPGRADE_SQL:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_SQL:
        op.execute(statement)
