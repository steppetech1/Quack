-- The name of an uploaded file, as the learner knows it (shown on the attachment of a message and
-- in lists). Presentation metadata of the upload, not a fact of the student model, so it is a
-- column of `content.materials` and not a field of the event. NULL for the materials uploaded before.
-- Extending migration after G0 (§0.4.4 п.4, Д-§6.2): a nullable column without a default.

-- migrate:up
ALTER TABLE content.materials ADD COLUMN title TEXT NULL;

-- migrate:down
-- Down migrations run only in development (Д-§6.2).
-- squawk-ignore ban-drop-column
ALTER TABLE content.materials DROP COLUMN title;
