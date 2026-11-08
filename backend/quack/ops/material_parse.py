"""Handler of the job `material.parse` (ТЗ Ф4 WP20.2; D-C-200).

Glue of the assembly layer: `quack.materials` (a product package) may not import
`quack.agents` (§4.1), so the structure step of the parse gets the reconciliation of stream B as
a function from here, and so do the seminar tasks of stream B (`quack.practice`, #271,
D-C-217). The steps themselves are `quack.materials.parse`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from quack.agents.reconcile import Subject, reconcile_domain
from quack.db import Db
from quack.jobs.queue_p4 import ClaimedJob as QueuedJob
from quack.jobs.queue_p4 import register_failure_hook
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob, JobDeferred, JobFailedPermanently
from quack.llm.errors import LLMBudgetExceededError
from quack.materials.parse import (
    STEP_STRUCTURE,
    SeminarSpec,
    SubjectSpec,
    mark_failed,
    mark_material_failed,
    parse_step,
    structure_step,
)
from quack.practice.seminar_tasks import SeminarTask, create_seminar_task


async def reconcile_subjects(
    ctx: JobContext, *, learner_id: UUID, domain_id: UUID, subjects: Sequence[SubjectSpec]
) -> dict[tuple[str, str], UUID]:
    """`reconcile_domain` for the subjects of the material: the id of the node of each one it
    linked or created, by `(kind, name)`; the origin of the edges is `material`."""
    result = await reconcile_domain(
        ctx,
        learner_id=learner_id,
        domain_id=domain_id,
        subjects=[Subject(s.name, s.kind, s.text, s.topic, s.requires) for s in subjects],
        origin="material",
    )
    return {
        (r.subject.kind, r.subject.name): r.node_id
        for r in result.resolutions
        if r.node_id is not None
    }


async def create_seminar_tasks(
    ctx: JobContext, *, learner_id: UUID, tasks: Sequence[SeminarSpec]
) -> None:
    """`create_seminar_task` of stream B for every problem of the material, at most
    `material_parser_concurrency` at a time. A task it throws away (`None`) is not an error; an
    error of the model goes up and the job is retried (the keys make the retry write nothing
    twice)."""
    gate = asyncio.Semaphore(ctx.settings.material_parser_concurrency)

    async def create(spec: SeminarSpec) -> None:
        async with gate:
            await create_seminar_task(
                ctx.db,
                ctx.llm,
                ctx.settings,
                SeminarTask(
                    key=spec.key,
                    statement=spec.statement,
                    skill_id=spec.skill_id,
                    skill_name=spec.skill_name,
                    topic_name=spec.topic_name,
                    answer=spec.answer,
                    course_context=spec.course_context,
                    domain_id=spec.domain_id,
                ),
                learner_id=learner_id,
                compute=ctx.compute,
            )

    await asyncio.gather(*(create(spec) for spec in tasks))


def _is_final(error: Exception, job: ClaimedJob) -> bool:
    """The runner will not retry this error: a permanent one, a session budget, or the last
    attempt (the same rule as `fail_or_retry`). A deferral is not a failure."""
    if isinstance(error, JobDeferred):
        return False
    if isinstance(error, LLMBudgetExceededError):
        return error.scope == "session"
    return isinstance(error, JobFailedPermanently) or job.attempt >= job.max_attempts


@register_job_handler("material.parse")
async def handle_material_parse(job: ClaimedJob, ctx: JobContext) -> None:
    try:
        if job.payload.get("step") == STEP_STRUCTURE:
            await structure_step(ctx, job, reconcile_subjects, create_seminar_tasks)
        else:
            await parse_step(ctx, job)
    except Exception as error:
        if _is_final(error, job):
            await mark_failed(ctx.db, job)
        raise


@register_failure_hook("material.parse")
async def mark_material_parse_failed(db: Db, job: QueuedJob) -> None:
    """The queue gave up on the job for good, also without the handler seeing it (a heartbeat
    timeout on the last attempt): the material is `failed`, a parsed one is left alone."""
    await mark_material_failed(db, job.learner_id, job.payload)
