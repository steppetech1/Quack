-- A stopped dispatcher stream (bug audit 2026-11-15; Т-§2.10, law 7): the counterpart of
-- `ops.projector_failures` for the log dispatcher. A batch that raises is rolled back whole and
-- writes its failure here in its own transaction: `seq` is the event it stopped on, `attempts`
-- counts the failures in a row on that event, `stopped_at` is set when `attempts` reaches
-- `dispatcher_failure_limit` — from then on the learner is not picked up. The event is never
-- skipped: `ops resume` deletes the row and the dispatcher goes on from the same mark.
-- Extending migration after G0 (§0.4.4 п.4, Д-§6.2).

-- migrate:up
CREATE TABLE ops.dispatcher_failures (
    learner_id UUID PRIMARY KEY,
    seq BIGINT NOT NULL,
    error_type TEXT NOT NULL,
    error TEXT NOT NULL,
    attempts BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    stopped_at TIMESTAMPTZ NULL
);

-- migrate:down
-- Down migrations run only in development (Д-§6.2), no client depends on this table there.
-- squawk-ignore ban-drop-table
DROP TABLE ops.dispatcher_failures;
