-- Pairing tokens of a canvas (ТЗ Ф4 WP22.1, Т-§16.1; D-A-128). Extending migration after G0
-- (§0.4.4 п.4, Д-§6.2). One-time, lives `pairing_ttl_s`; only the hash of the token is stored.
-- `core.auth_links` keeps mail links and `core.auth_sessions` sessions already issued (§6.1).
-- The index by learner is a separate file, CONCURRENTLY (Д-§6.2).

-- migrate:up
CREATE TABLE core.pairing_tokens (
    token_hash TEXT PRIMARY KEY,
    learner_id UUID NOT NULL,
    session_id UUID NOT NULL,
    canvas_id UUID NOT NULL,
    task_id UUID NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ NULL
);

-- migrate:down
-- Down migrations run only in development (Д-§6.2), no client depends on this table there.
-- squawk-ignore ban-drop-table
DROP TABLE core.pairing_tokens;
