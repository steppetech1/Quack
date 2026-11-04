"""Jobs that generate tasks, WP16.6 / WP8.2 (ТЗ Ф4): `agent.task_generation` — variants, retests
and a task for a skill and a level by a request (`agent.requested {kind: task_generation}`);
`check.known_claim` — the short check of «я это уже знаю»: one task per skill of the topic at
its next target level, at most `known_check_max_tasks`. The body is `generate_tasks` (WP12.6),
which checks every draft and arbitrates; the result is `agent.completed {task_ids}`. A request
nothing of which passed the check is answered `ok: false`; the teacher goes on. D-B-192, D-B-193.

- The kind of the answer follows the level (`answer_kind`): level 1 recognizes — a choice;
  level 2 recalls — a short exact answer; levels 3–5 — a number; level 6 explains — a proof.
- A request with a source task (`params.task_id`) asks for variants of it: a retest, which proves
  retention and takes the family of the source (`Т-§5.4`, `Т-§5.7`).
- Target levels of a topic are the levels its hubs require, else all (as in `today.prepare`).
"""

from __future__ import annotations

from uuid import UUID

from quack.agents.delivery import NO_SESSION, deliver
from quack.agents.today import ALL_LEVELS
from quack.events.event_types import AgentRequested
from quack.events.values import AnswerType, Level
from quack.graph.read import GraphReader, MisconceptionFact, SliceFocus, create_graph_reader
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob, JobFailedPermanently
from quack.practice.repo import agent_request, event_by_seq
from quack.practice.task_generation import (
    GENERATOR_ROLE,
    GenerationRequest,
    Purpose,
    generate_tasks,
)
from quack.practice.task_store import get_task

KIND = "task_generation"
KNOWN_KIND = "known_check"
ANSWER_KIND_BY_LEVEL: dict[Level, AnswerType] = {
    1: "choice",
    2: "short_text",
    3: "number",
    4: "number",
    5: "number",
    6: "proof",
}


def answer_kind(level: Level) -> AnswerType:
    return ANSWER_KIND_BY_LEVEL[level]


def _learner(job: ClaimedJob) -> UUID:
    if job.learner_id is None:
        raise ValueError(f"{job.kind} needs learner_id")
    return job.learner_id


def _statements(facts: list[MisconceptionFact], skill_id: UUID) -> dict[UUID, str]:
    return {m.misconception_id: m.statement for m in facts if m.skill_id == skill_id}


async def run_request(
    ctx: JobContext, reader: GraphReader, *, learner_id: UUID, request_id: UUID
) -> None:
    """`agent.task_generation`: one `generate_tasks` call by the request; the key of the call is
    the request, so a retry stores the same tasks."""
    requested = await agent_request(ctx.db, learner_id=learner_id, request_id=request_id)
    params = AgentRequested.model_validate(requested.payload).params
    if params.skill_id is None or params.level is None:
        raise JobFailedPermanently("task_generation needs skill_id and level")
    found = await reader.slice_for_turn(
        learner_id, requested.session_id or NO_SESSION, SliceFocus(skill_ids=[params.skill_id])
    )
    skill = next((s for s in found.skills if s.skill_id == params.skill_id), None)
    if skill is None or skill.name is None:
        raise LookupError(f"skill {params.skill_id} is not in memory (yet)")
    source = (
        await get_task(ctx.db, learner_id=learner_id, task_id=params.task_id)
        if params.task_id is not None
        else None
    )
    misconceptions = _statements(found.misconceptions, params.skill_id)
    if params.misconception_id is not None:
        misconceptions = {
            k: v for k, v in misconceptions.items() if k == params.misconception_id
        } or misconceptions
    purpose: Purpose = "retest" if source is not None else "practice"
    stored = await generate_tasks(
        ctx.db,
        ctx.llm,
        ctx.settings,
        GenerationRequest(
            key=f"task_generation:{learner_id}:{request_id}",
            skill_id=params.skill_id,
            skill_name=skill.name,
            topic_name=skill.topic_name or "",
            level=params.level,
            kind=answer_kind(params.level),
            purpose=purpose,
            misconceptions=misconceptions,
            course_context=params.description or "",
            source=source,
            domain_id=requested.domain_id or skill.domain_id,
            session_id=requested.session_id,
        ),
        learner_id=learner_id,
        compute=ctx.compute,
    )
    await deliver(
        ctx.db,
        learner_id=learner_id,
        request_id=request_id,
        job_key=None,
        kind=KIND,
        ok=bool(stored),
        actor=ctx.settings.llm_roles[GENERATOR_ROLE].prompt or GENERATOR_ROLE,
        task_ids=stored,
        error=None if stored else "no_task_passed_check",
        domain_id=requested.domain_id,
        session_id=requested.session_id,
        causation_seq=requested.seq,
    )


@register_job_handler("agent.task_generation")
async def handle_agent_task_generation(job: ClaimedJob, ctx: JobContext) -> None:
    learner_id = _learner(job)
    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=learner_id)
    await run_request(
        ctx, reader, learner_id=learner_id, request_id=UUID(str(job.payload["request_id"]))
    )


def _known_error(found: bool, task_ids: list[UUID], checked: int) -> str | None:
    if not found:
        return "topic_not_found"
    return "no_task_passed_check" if checked and not task_ids else None


async def known_check(
    ctx: JobContext,
    reader: GraphReader,
    *,
    learner_id: UUID,
    topic_id: UUID,
    trigger_seq: int,
    request_id: UUID | None = None,
) -> list[UUID]:
    """`check.known_claim` (WP8.2): the check tasks of the topic, then `agent.completed
    {kind: known_check, task_ids}`; a request id answers a request, else the job of the rule
    (`known.claimed`) answers by its key `check.known_claim:{learner}:{seq}` (WP7.1)."""
    trigger = await event_by_seq(ctx.db, learner_id=learner_id, seq=trigger_seq)
    topics = await reader.topic_memory([topic_id])
    memory = topics[0] if topics else None
    task_ids: list[UUID] = []
    checked = 0  # skills that needed a check
    if memory is not None:
        hubs = await reader.domain_goals(memory.domain_id) if memory.domain_id else []
        targets = sorted(
            {
                level
                for hub in hubs
                for topic in hub.topics
                if topic.topic_id == topic_id
                for level in topic.required_levels
            }
        ) or list(ALL_LEVELS)
        for skill in memory.skills:
            if len(task_ids) >= ctx.settings.known_check_max_tasks:
                break
            frontier = await reader.frontier(skill.skill_id, targets)
            if frontier.level is None or skill.name is None:
                continue  # every target level is proven: nothing to check
            checked += 1
            made = await generate_tasks(
                ctx.db,
                ctx.llm,
                ctx.settings,
                GenerationRequest(
                    key=f"known_check:{learner_id}:{trigger_seq}:{skill.skill_id}",
                    skill_id=skill.skill_id,
                    skill_name=skill.name,
                    topic_name=memory.name,
                    level=frontier.level,
                    kind=answer_kind(frontier.level),
                    purpose="known_check",
                    misconceptions=_statements(memory.misconceptions, skill.skill_id),
                    domain_id=memory.domain_id,
                    session_id=trigger.session_id if trigger else None,
                ),
                learner_id=learner_id,
                compute=ctx.compute,
            )
            task_ids.extend(made[:1])
    prompt = ctx.settings.llm_roles[GENERATOR_ROLE].prompt or GENERATOR_ROLE
    await deliver(
        ctx.db,
        learner_id=learner_id,
        request_id=request_id,
        job_key=None if request_id else f"check.known_claim:{learner_id}:{trigger_seq}",
        kind=KNOWN_KIND,
        ok=memory is not None and (bool(task_ids) or checked == 0),
        actor=prompt,
        task_ids=task_ids,
        error=_known_error(memory is not None, task_ids, checked),
        domain_id=trigger.domain_id if trigger else None,
        session_id=trigger.session_id if trigger else None,
        causation_seq=trigger_seq,
    )
    return task_ids


@register_job_handler("check.known_claim")
async def handle_known_claim(job: ClaimedJob, ctx: JobContext) -> None:
    learner_id = _learner(job)
    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=learner_id)
    raw = job.payload.get("request_id")
    await known_check(
        ctx,
        reader,
        learner_id=learner_id,
        topic_id=UUID(str(job.payload["topic_id"])),
        trigger_seq=int(str(job.payload["trigger_seq"])),
        request_id=UUID(str(raw)) if raw else None,
    )
