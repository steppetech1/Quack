-- The index `uq_jobs_running_lock` (`running` only) is replaced by `uq_jobs_in_work_lock` of the
-- previous migration (`running` and `cancel_requested`); the new one is the stricter, so the
-- old one is redundant. CONCURRENTLY outside a transaction (Д-§6.2).

-- migrate:up transaction:false
-- squawk-disable-assume-in-transaction
DROP INDEX CONCURRENTLY IF EXISTS ops.uq_jobs_running_lock;

-- migrate:down transaction:false
-- squawk-disable-assume-in-transaction
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_jobs_running_lock
    ON ops.jobs (lock_key) WHERE status = 'running';
