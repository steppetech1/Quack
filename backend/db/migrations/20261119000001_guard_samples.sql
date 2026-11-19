-- The sentences of the teacher reply that the spoiler guard matched, for the manual review of
-- false and true hits (owner decision 2026-11-19: the guard runs in `shadow`). Only the matched
-- sentence of the reply and the matched answer token, both cut to `guard_sample_max_chars`; never
-- a learner's text, never a whole reply. Written after the commit of the turn, best effort
-- (law 7); rows older than `guard_samples_retention_days` go by `manifests.cleanup`; the deletion
-- of an account or a domain deletes them with the learner's other data (`learner_id`,
-- `session_id`). The roles get their grants from the default privileges of `quack_migrator`
-- (`ops db-roles`). Extending migration after G0 (§0.4.4 п.4, Д-§6.2).

-- migrate:up
CREATE TABLE ops.guard_samples (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    learner_id UUID NOT NULL,
    session_id UUID NOT NULL,
    turn_seq BIGINT NOT NULL,
    rule TEXT NOT NULL,
    reason TEXT NOT NULL,
    sentence TEXT NOT NULL,
    answer_token TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_guard_samples_learner ON ops.guard_samples (learner_id, session_id);
CREATE INDEX idx_guard_samples_created ON ops.guard_samples (created_at);

-- migrate:down
-- Down migrations run only in development (Д-§6.2), no client depends on this table there.
-- squawk-ignore ban-drop-table
DROP TABLE ops.guard_samples;
