"""Handler of `account.purge_deleted` (ТЗ Фазы 3 WP3.3), in the assembly layer because it calls
`ops purge-deleted` (§4.1)."""

from __future__ import annotations

from quack.jobs.queue import ClaimedJob
from quack.jobs.runner import JobContext, register_job_handler
from quack.ops.commands import purge_deleted


@register_job_handler("account.purge_deleted")
async def handle_purge_deleted(job: ClaimedJob, ctx: JobContext) -> None:
    """The logic of `ops purge-deleted` over the resources of `ctx`, plus the Redis keys of the
    learner (`presence:learner:{id}`, `ratelimit:*:{id}`; Т-§19.5)."""
    if ctx.engine is None or ctx.neo4j is None:
        raise RuntimeError(
            "account.purge_deleted needs the engine and the Neo4j driver in JobContext"
        )
    await purge_deleted(
        ctx.engine,
        ctx.session_factory,
        ctx.neo4j,
        chunk_size=ctx.settings.rebuild_max_tx_ops,
        redis=ctx.redis,
    )
