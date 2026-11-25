-- Т-§6.10: the tests of a `code` task (inputs, expected results, the reference solution). Kept in
-- the task document only: the hidden tests must reach neither an event, nor the client, nor a
-- model, so they are not a field of `task.created`. NULL for tasks of other answer types.
-- Extending migration after G0 (§0.4.4 п.4, Д-§6.2): a nullable column without a default.

-- migrate:up
ALTER TABLE content.tasks ADD COLUMN code_tests JSONB NULL;

-- migrate:down
-- Down migrations run only in development (Д-§6.2).
-- squawk-ignore ban-drop-column
ALTER TABLE content.tasks DROP COLUMN code_tests;
