"""Job queue on `ops.jobs` (Phase 4): the queue is `quack.jobs.queue_p4`, the executor is
`quack.jobs.executor_p4`; the types shared with the handlers are re-exported here."""

from quack.jobs.types import ClaimedJob, JobDeferred, JobFailedPermanently, JobSpec

__all__ = ["ClaimedJob", "JobDeferred", "JobFailedPermanently", "JobSpec"]
