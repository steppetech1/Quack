"""Job handlers of the checks (ТЗ Ф4 WP11.3, WP12.5; rows of WP7.1): `check.symbolic` — the
insurance of a fast answer; `task.check` — the check of the tasks a turn gave. Bodies live in
`symbolic_job` and `task_check`; here the payload of the row is read. `check.model` — the
model verdict of a slow answer or of a fast one that failed to check (WP11.6), with the known
misconceptions and the level states of the tested skills from memory (S4). D-B-183."""

from __future__ import annotations

from uuid import UUID

from quack.events.event_types import AnswerSubmitted, TaskCreated
from quack.events.keys import check_failed_key, verdict_key
from quack.graph.read import SliceFocus, create_graph_reader
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.practice.answers import help_in_session
from quack.practice.model_check import (
    LevelState,
    check_with_model,
    known_misconceptions,
    record_verdict,
)
from quack.practice.repo import event_by_key, event_by_seq, task_events
from quack.practice.symbolic_job import insure_symbolic
from quack.practice.task_check import check_teacher_task
from quack.practice.task_store import get_task


def _learner(job: ClaimedJob) -> UUID:
    if job.learner_id is None:
        raise ValueError(f"{job.kind} needs learner_id")
    return job.learner_id


@register_job_handler("check.symbolic")
async def handle_check_symbolic(job: ClaimedJob, ctx: JobContext) -> None:
    await insure_symbolic(
        ctx.db,
        ctx.compute,
        ctx.settings,
        learner_id=_learner(job),
        answer_seq=int(str(job.payload["answer_seq"])),
        part=str(job.payload["part"]),
    )


@register_job_handler("task.check")
async def handle_task_check(job: ClaimedJob, ctx: JobContext) -> None:
    """Each task of the turn; the names of the tested skills come from memory (S4) for the
    checker's input."""
    learner_id = _learner(job)
    trigger_seq = int(str(job.payload["trigger_seq"]))
    task_ids = [UUID(str(t)) for t in job.payload["task_ids"]]
    trigger = await event_by_seq(ctx.db, learner_id=learner_id, seq=trigger_seq)
    created = await task_events(
        ctx.db, learner_id=learner_id, task_ids=task_ids, types=("task.created",)
    )
    skill_ids = [
        test.skill_id
        for event in created
        for test in TaskCreated.model_validate(event.payload).markup.tests
        if test.skill_id is not None
    ]
    names: dict[UUID, str] = {}
    if trigger is not None and trigger.session_id is not None:
        reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=learner_id)
        found = await reader.slice_for_turn(
            learner_id, trigger.session_id, SliceFocus(skill_ids=skill_ids)
        )
        names = {s.skill_id: s.name for s in found.skills if s.name}
    for task_id in task_ids:
        await check_teacher_task(
            ctx.db,
            ctx.llm,
            ctx.settings,
            learner_id=learner_id,
            task_id=task_id,
            skill_names=names,
            compute=ctx.compute,
            causation_seq=trigger_seq,
        )


@register_job_handler("check.model")
async def handle_check_model(job: ClaimedJob, ctx: JobContext) -> None:
    learner_id = _learner(job)
    answer_seq, part = int(str(job.payload["answer_seq"])), str(job.payload["part"])
    if await event_by_key(ctx.db, learner_id=learner_id, key=verdict_key(answer_seq, part)):
        return  # a retry, or the code already decided (law 5)
    event = await event_by_seq(ctx.db, learner_id=learner_id, seq=answer_seq)
    if event is None or event.type != "answer.submitted" or event.session_id is None:
        raise LookupError(f"seq {answer_seq} is no answer of a session")
    answer = AnswerSubmitted.model_validate(event.payload)
    doc = await get_task(ctx.db, learner_id=learner_id, task_id=answer.task_id)
    if doc is None:
        raise LookupError(f"task {answer.task_id} is not the learner's")
    created = await task_events(
        ctx.db, learner_id=learner_id, task_ids=[answer.task_id], types=("task.created",)
    )
    tests = [t for e in created for t in TaskCreated.model_validate(e.payload).markup.tests]
    skill_ids = [t.skill_id for t in tests if t.skill_id is not None]
    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=learner_id)
    found = await reader.slice_for_turn(
        learner_id, event.session_id, SliceFocus(skill_ids=skill_ids, domain_id=event.domain_id)
    )
    by_skill = {s.skill_id: {lv.level: lv for lv in s.levels} for s in found.skills}
    levels = [
        LevelState(proven_by_direct=fact.proven_by_direct, last_t1_failed=fact.last_t1_failed)
        for t in tests
        if t.skill_id is not None and (fact := by_skill.get(t.skill_id, {}).get(t.level))
    ]
    failed = await event_by_key(
        ctx.db, learner_id=learner_id, key=check_failed_key(answer_seq, part)
    )
    verdict = await check_with_model(
        ctx.llm,
        ctx.settings,
        learner_id=learner_id,
        session_id=event.session_id,
        condition=doc.condition,
        spec=doc.answer_spec,
        solution_space=doc.solution_space,
        answer=answer,
        answer_seq=answer_seq,
        part=part,
        failed=failed is not None,
        misconceptions=known_misconceptions(found.misconceptions, skill_ids=skill_ids),
        levels=levels,
        help_used=await help_in_session(
            ctx.db, learner_id=learner_id, session_id=event.session_id, task_id=answer.task_id
        ),
    )
    await record_verdict(
        ctx.db,
        verdict,
        learner_id=learner_id,
        session_id=event.session_id,
        domain_id=event.domain_id,
    )
