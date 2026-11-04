"""The periodic job `deletions.reconcile` (ТЗ Ф4 WP7.5, WP25 п.5; Т-§19.5, Д-§8.5)."""

from __future__ import annotations

from datetime import UTC, datetime

from quack.deletion.journal import reconcile_deletions
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.logging import get_logger

log = get_logger(__name__)

# «Объекты R2 без строки `ops.deletion_log` → удаление заново» (WP25 п.5): the whole journal.
_SINCE = datetime(1970, 1, 1, tzinfo=UTC)


@register_job_handler("deletions.reconcile")
async def handle_reconcile(job: ClaimedJob, ctx: JobContext) -> None:
    done = await reconcile_deletions(
        ctx.db, ctx.neo4j, ctx.settings, ctx.deletions, ctx.storage, _SINCE, redis=ctx.redis
    )
    log.info("deletions_reconciled", repeated=done, period=job.payload.get("period"))
