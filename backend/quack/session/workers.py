"""Background handlers `turn.extract` and `session.checkpoint` (ТЗ §2.4)."""

from __future__ import annotations

from quack.jobs.queue import ClaimedJob
from quack.jobs.runner import JobContext, register_job_handler


@register_job_handler("turn.extract")
async def handle_turn_extract(job: ClaimedJob, ctx: JobContext) -> None:
    raise NotImplementedError


@register_job_handler("session.checkpoint")
async def handle_session_checkpoint(job: ClaimedJob, ctx: JobContext) -> None:
    raise NotImplementedError
