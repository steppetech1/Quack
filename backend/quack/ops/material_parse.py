"""Handler of the job `material.parse` (ТЗ Ф4 WP20.2; D-C-200).

Glue of the assembly layer: `quack.materials` (a product package) may not import
`quack.agents` (§4.1), so the structure step of the parse gets the reconciliation of stream B as
a function from here. The steps themselves are `quack.materials.parse`.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from quack.agents.reconcile import Subject, reconcile_domain
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.materials.parse import STEP_STRUCTURE, SubjectSpec, parse_step, structure_step


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


@register_job_handler("material.parse")
async def handle_material_parse(job: ClaimedJob, ctx: JobContext) -> None:
    if job.payload.get("step") == STEP_STRUCTURE:
        await structure_step(ctx, job, reconcile_subjects)
    else:
        await parse_step(ctx, job)
