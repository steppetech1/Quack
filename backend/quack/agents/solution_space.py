"""Jobs of the solution space, WP12.4 / WP16.6 (ТЗ Ф4): `task.solution_space` — the rule of
`task.created` of the bank (ahead, P2); `agent.solution_space` — `agent.requested {kind:
solution_space, task_id}` of a task on the fly opened on the canvas (lazily), answered with
`agent.completed {task_ids: [task]}`. The body is `build_solution_space`; the misconceptions
of the tested skills come from memory (S4). A task that has a space is not built again. D-B-191.
"""

from __future__ import annotations

from uuid import UUID

from quack.agents.delivery import NO_SESSION, deliver
from quack.events.event_types import AgentRequested, TaskCreated
from quack.graph.read import GraphReader, SliceFocus, create_graph_reader
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.practice.repo import agent_request, task_events
from quack.practice.role_outputs import SolutionSpace
from quack.practice.solution_space import SPACE_ROLE, SkillMisconception, build_solution_space
from quack.practice.task_store import get_task

KIND = "solution_space"


def _learner(job: ClaimedJob) -> UUID:
    if job.learner_id is None:
        raise ValueError(f"{job.kind} needs learner_id")
    return job.learner_id


async def ensure_space(
    ctx: JobContext, reader: GraphReader, *, learner_id: UUID, task_id: UUID
) -> SolutionSpace | None:
    """The space of the task: the stored one, else built with the known misconceptions of the
    skills it tests; `None` — not the learner's task."""
    doc = await get_task(ctx.db, learner_id=learner_id, task_id=task_id)
    if doc is None:
        return None
    if doc.solution_space is not None:
        return doc.solution_space
    created = await task_events(
        ctx.db, learner_id=learner_id, task_ids=[task_id], types=("task.created",)
    )
    skills = {
        test.skill_id
        for event in created
        for test in TaskCreated.model_validate(event.payload).markup.tests
        if test.skill_id is not None
    }
    found = await reader.slice_for_turn(learner_id, NO_SESSION, SliceFocus(skill_ids=list(skills)))
    known = [
        SkillMisconception(m.misconception_id, m.statement)
        for m in found.misconceptions
        if m.skill_id in skills
    ]
    return await build_solution_space(
        ctx.db,
        ctx.llm,
        ctx.settings,
        learner_id=learner_id,
        task_id=task_id,
        misconceptions=known,
    )


@register_job_handler("task.solution_space")
async def handle_task_solution_space(job: ClaimedJob, ctx: JobContext) -> None:
    learner_id = _learner(job)
    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=learner_id)
    await ensure_space(
        ctx, reader, learner_id=learner_id, task_id=UUID(str(job.payload["task_id"]))
    )


async def answer_request(
    ctx: JobContext, reader: GraphReader, *, learner_id: UUID, request_id: UUID
) -> None:
    """`agent.solution_space`: build, then `agent.completed`; a request without a task of the
    learner is answered `ok: false` (it cannot succeed on a retry)."""
    requested = await agent_request(ctx.db, learner_id=learner_id, request_id=request_id)
    task_id = AgentRequested.model_validate(requested.payload).params.task_id
    space = (
        None
        if task_id is None
        else await ensure_space(ctx, reader, learner_id=learner_id, task_id=task_id)
    )
    await deliver(
        ctx.db,
        learner_id=learner_id,
        request_id=request_id,
        job_key=None,
        kind=KIND,
        ok=space is not None,
        actor=ctx.settings.llm_roles[SPACE_ROLE].prompt or SPACE_ROLE,
        task_ids=[task_id] if task_id is not None and space is not None else [],
        error=None if space is not None else "task_not_found",
        domain_id=requested.domain_id,
        session_id=requested.session_id,
        causation_seq=requested.seq,
    )


@register_job_handler("agent.solution_space")
async def handle_agent_solution_space(job: ClaimedJob, ctx: JobContext) -> None:
    learner_id = _learner(job)
    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=learner_id)
    await answer_request(
        ctx, reader, learner_id=learner_id, request_id=UUID(str(job.payload["request_id"]))
    )
