-- The name of a session in the history panel. Written once by the job `session.title` together
-- with the event `session.titled` (law 1: the log is the source, the column is its reading, like
-- the rest of `content.sessions`); NULL until then and for the sessions of the old logs, whose
-- provisional title is computed on read (first learner message), nothing is renamed in bulk.
-- Extending migration after G0 (§0.4.4 п.4, Д-§6.2): a nullable column without a default.

-- migrate:up
ALTER TABLE content.sessions ADD COLUMN title TEXT NULL;

-- migrate:down
-- Down migrations run only in development (Д-§6.2).
-- squawk-ignore ban-drop-column
ALTER TABLE content.sessions DROP COLUMN title;
