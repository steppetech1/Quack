-- Index of `core.pairing_tokens` by learner for the deletion of the learner's data (WP25;
-- D-A-128). CONCURRENTLY outside a transaction (Д-§6.2).

-- migrate:up transaction:false
-- squawk-disable-assume-in-transaction
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pairing_tokens_learner
    ON core.pairing_tokens (learner_id);

-- migrate:down transaction:false
DROP INDEX CONCURRENTLY IF EXISTS core.idx_pairing_tokens_learner;
