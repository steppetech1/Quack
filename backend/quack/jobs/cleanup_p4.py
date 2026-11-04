"""The periodic jobs `manifests.cleanup` and `exports.cleanup` (ТЗ Ф4 WP7.5).

`manifests.cleanup` (Т-§3.2, Т-§3.6; closes Ф3 R14): once a day it deletes the turn manifests
older than `turn_manifest_retention_days`: the manifest is the trace of one turn for debugging,
not memory (law 1 — the log is the truth).

`exports.cleanup` (Д-§8.4, Т-§19.6; D-A-215): once a day it deletes the archives of the data
export whose link has expired (`expires_at`), in case the lifecycle rule of the export prefix is
not set on the store; the row stays with `status = 'expired'` and no `object_key`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from quack.db import Db
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.logging import get_logger
from quack.storage.objects import ObjectStore

log = get_logger(__name__)

# Every learner's old manifests (D-A-204): a cross-learner statement of the periodic job.
_DELETE_OLD = "DELETE FROM ops.turn_manifests WHERE created_at < %(before)s"
# Every learner's expired archives (D-A-215): a cross-learner statement of the periodic job.
_EXPIRED_EXPORTS = (
    "SELECT export_id, object_key FROM content.exports "
    "WHERE expires_at < %(now)s AND object_key IS NOT NULL"
)
_MARK_EXPIRED = (
    "UPDATE content.exports SET status = 'expired', object_key = NULL "
    "WHERE export_id = %(export_id)s"
)


async def delete_old_manifests(db: Db, before: datetime) -> int:
    async with db.transaction() as conn:
        cursor = await conn.execute(_DELETE_OLD, {"before": before})
        return cursor.rowcount


@register_job_handler("manifests.cleanup")
async def handle_manifests_cleanup(job: ClaimedJob, ctx: JobContext) -> None:
    days = ctx.settings.turn_manifest_retention_days
    deleted = await delete_old_manifests(ctx.db, datetime.now(UTC) - timedelta(days=days))
    log.info("manifests_cleaned", deleted=deleted, period=job.payload.get("period"))


async def delete_expired_exports(db: Db, store: ObjectStore, now: datetime) -> int:
    """The objects first, then the rows: a failure in between leaves the row to the next run
    (deleting a missing key is not an error), never a row pointing at nothing it claims."""
    rows = await db.fetch_all(_EXPIRED_EXPORTS, {"now": now})
    if not rows:
        return 0
    await store.delete_keys([row["object_key"] for row in rows])
    async with db.transaction():
        for row in rows:
            await db.execute(_MARK_EXPIRED, {"export_id": row["export_id"]})
    return len(rows)


@register_job_handler("exports.cleanup")
async def handle_exports_cleanup(job: ClaimedJob, ctx: JobContext) -> None:
    deleted = await delete_expired_exports(ctx.db, ctx.storage, datetime.now(UTC))
    log.info("exports_cleaned", deleted=deleted, period=job.payload.get("period"))
