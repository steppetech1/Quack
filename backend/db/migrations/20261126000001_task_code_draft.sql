-- The code sandbox (teacher@5, owner decision 2026-11-26): the learner's current code of a `code`
-- task and the statuses of its last run, kept by `task_id` so a reload or another device opens
-- the editor where the learner left it. Not an event: a draft is not an answer (only a submit
-- writes `answer.submitted`). NULL — nothing typed or run yet.
-- Extending migration after G0 (§0.4.4 п.4, Д-§6.2): a nullable column without a default.

-- migrate:up
ALTER TABLE content.tasks ADD COLUMN code_draft JSONB NULL;

-- migrate:down
-- Down migrations run only in development (Д-§6.2).
-- squawk-ignore ban-drop-column
ALTER TABLE content.tasks DROP COLUMN code_draft;
