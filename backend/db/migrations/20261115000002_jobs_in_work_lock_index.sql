-- One job in work per `lock_key` (Т-§13.1, bug audit 2026-11-15): `uq_jobs_running_lock` covers
-- `running` only, so while a job is `cancel_requested` a second one with the same `lock_key` could
-- start. The new index covers both statuses of a job in work; `running → cancel_requested` keeps
-- the row inside it. The old index is dropped by the next migration, so there is no moment
-- without a guard. CONCURRENTLY outside a transaction (Д-§6.2), one statement per file.

-- migrate:up transaction:false
-- squawk-disable-assume-in-transaction
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_jobs_in_work_lock
    ON ops.jobs (lock_key) WHERE status IN ('running', 'cancel_requested');

-- migrate:down transaction:false
-- squawk-disable-assume-in-transaction
DROP INDEX CONCURRENTLY IF EXISTS ops.uq_jobs_in_work_lock;
