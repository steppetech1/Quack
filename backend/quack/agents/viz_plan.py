"""Demos prepared ahead: the job `viz.plan` (owner decision 2026-11-20).

While the learner works on a step of the session plan, the demos for the steps that come next are
built in the background, so each is ready when its step arrives.

- The dispatcher plans one job per session after a turn that set the current step or the next
  ones (`rules_p4`, coalescing per session). The job is P3 and invisible on the plaque; the
  hot path never waits for it (law 9) and a failed job commits nothing (law 7).
- The job reads the plan document, takes the current step and the next ones (`viz_prefetch_horizon`
  steps in all) and drops those it has already decided on. For the rest one call to
  `viz_planner@1` says, step by step, whether a demo helps and, if so, writes the brief.
- A step that gets a demo gets an ordinary `agent.requested {kind: viz}` with the step id and the
  title in its parameters. From there it is the existing pipeline: the dispatcher plans
  `agent.viz`, the builder writes the page, compute checks it, the object store keeps it and
  `GET /v1/viz/{request_id}` serves it. Nothing here is a new store: the state of a demo is the
  pair of events `agent.requested` / `agent.completed` of its request.
- A step the planner turned down is written once as `agent.completed {kind: viz_plan}` under a key
  of the step, so the step is not asked about again (law 5: the key is the decision).
- At most `viz_prefetch_max_per_session` demos are ordered this way in one session. The
  planner sees only the goal and the step texts, never the learner's words.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel

from quack.agents.schemas import VizPlannerOutput
from quack.config import Settings
from quack.db.db import Db
from quack.events.errors import IdempotencyConflictError
from quack.events.event_types import AgentCompleted, AgentRequested
from quack.events.log import NewEvent, append_events
from quack.events.records import AgentParamsRecord, PlanStepRecord
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.llm.gateway import LLMGateway
from quack.logging import get_logger
from quack.practice.session_plan import lift_plan

log = get_logger(__name__)

PLANNER_ROLE = "viz_planner"
PLAN_KIND = "viz_plan"
NONE_LINE = "(none)"
_TITLE_MAX = 80
_BRIEF_MAX = 2000

_SESSION = (
    "SELECT domain_id, status FROM content.sessions "
    "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s"
)
_PLAN = (
    "SELECT plan FROM content.session_plans "
    "WHERE session_id = %(session_id)s AND learner_id = %(learner_id)s"
)
_DECIDED = (
    "SELECT idempotency_key FROM core.events WHERE learner_id = %(learner_id)s "
    "AND idempotency_key = ANY(%(keys)s)"
)
_ORDERED = (
    "SELECT payload -> 'params' ->> 'step_id' AS step_id, payload -> 'params' ->> 'title' AS title "
    "FROM core.events WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s "
    "AND type = 'agent.requested' AND payload ->> 'kind' = 'viz' "
    "AND payload -> 'params' ->> 'step_id' IS NOT NULL ORDER BY seq"
)


class _Session(BaseModel):
    domain_id: UUID
    status: str


class _Plan(BaseModel):
    plan: dict[str, object] | None


class _Key(BaseModel):
    idempotency_key: str


class _Ordered(BaseModel):
    step_id: str
    title: str | None


def request_key(session_id: UUID, step_id: str) -> str:
    return f"viz.request:{session_id}:{step_id}"


def declined_key(session_id: UUID, step_id: str) -> str:
    return f"viz.plan.declined:{session_id}:{step_id}"


def request_id_of(session_id: UUID, step_id: str) -> UUID:
    """The id of the demo of a step: the same for a repeat of the job, so the pair of events and the
    object are one per step (law 5)."""
    return uuid5(NAMESPACE_URL, request_key(session_id, step_id))


def steps_in_view(
    current: PlanStepRecord | None, upcoming: list[PlanStepRecord], horizon: int
) -> list[PlanStepRecord]:
    """The current step and the next ones, `horizon` in all, each step once."""
    seen: set[str] = set()
    out: list[PlanStepRecord] = []
    for step in ([current] if current is not None else []) + upcoming:
        if step.id in seen:
            continue
        seen.add(step.id)
        out.append(step)
    return out[:horizon]


def _clean(text: str | None, limit: int) -> str:
    return " ".join((text or "").split())[:limit].strip()


async def plan_demos(
    db: Db,
    gateway: LLMGateway,
    settings: Settings,
    *,
    learner_id: UUID,
    session_id: UUID,
    trigger_seq: int | None = None,
) -> list[UUID]:
    """Decide on the demos of the steps in view; returns the request ids ordered now."""
    if not settings.viz_prefetch_enabled:
        return []
    params = {"learner_id": learner_id, "session_id": session_id}
    session = await db.fetch_one(_SESSION, params, row=_Session)
    if session is None or session.status != "active":
        # no such session, or it is over: nobody waits for a demo (a log replay orders none)
        return []
    row = await db.fetch_one(_PLAN, params, row=_Plan)
    doc = lift_plan(None if row is None else row.plan)
    steps = steps_in_view(doc.current_step, doc.next_steps, settings.viz_prefetch_horizon)
    if not steps:
        return []
    keys = [
        k for s in steps for k in (request_key(session_id, s.id), declined_key(session_id, s.id))
    ]
    decided = {
        k.idempotency_key for k in await db.fetch_all(_DECIDED, {**params, "keys": keys}, row=_Key)
    }
    todo = [
        s
        for s in steps
        if request_key(session_id, s.id) not in decided
        and declined_key(session_id, s.id) not in decided
    ]
    if not todo:
        return []
    ordered = await db.fetch_all(_ORDERED, params, row=_Ordered)
    room = settings.viz_prefetch_max_per_session - len(ordered)
    if room <= 0:
        return []
    prompt = settings.llm_roles[PLANNER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'viz_planner' has no prompt")
    result = await gateway.generate_structured(
        PLANNER_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        VizPlannerOutput,
        {
            "goal": _clean(doc.goal, 300) or NONE_LINE,
            "steps": "\n".join(f"{s.id}: {_clean(s.text, 300)}" for s in todo),
            "existing": "\n".join(f"- {o.title or o.step_id}" for o in ordered) or NONE_LINE,
        },
        learner_id,
        session_id,
        None,
        f"viz.plan:{session_id}:{todo[0].id}:{len(ordered)}",
    )
    by_id = {item.step_id: item for item in result.value.steps}
    ordered_now: list[UUID] = []
    for step in todo:
        item = by_id.get(step.id)
        brief = _clean(item.brief, _BRIEF_MAX) if item is not None else ""
        title = _clean(item.title, _TITLE_MAX) if item is not None else ""
        wanted = item is not None and item.demo and bool(brief) and room > 0
        if wanted:
            payload: AgentRequested | AgentCompleted = AgentRequested(
                request_id=request_id_of(session_id, step.id),
                kind="viz",
                params=AgentParamsRecord(
                    description=brief, step_id=step.id, title=title or _clean(step.text, _TITLE_MAX)
                ),
                needed_in_session=False,
            )
            event_type = "agent.requested"
            key = request_key(session_id, step.id)
        else:
            payload = AgentCompleted(
                job_key=declined_key(session_id, step.id), kind=PLAN_KIND, ok=True
            )
            event_type = "agent.completed"
            key = declined_key(session_id, step.id)
        try:
            await append_events(
                db,
                learner_id,
                [
                    NewEvent(
                        type=event_type,
                        payload=payload,
                        actor=prompt,
                        idempotency_key=key,
                        domain_id=session.domain_id,
                        session_id=session_id,
                        causation_seq=trigger_seq,
                    )
                ],
            )
        except IdempotencyConflictError:
            continue  # another run decided it between the read and the write: the first wins
        if wanted:
            room -= 1
            ordered_now.append(request_id_of(session_id, step.id))
    log.info(
        "viz_plan_decided", session_id=str(session_id), asked=len(todo), ordered=len(ordered_now)
    )
    return ordered_now


@register_job_handler("viz.plan")
async def handle_viz_plan(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None or job.session_id is None:
        raise ValueError("viz.plan needs learner_id and session_id")
    trigger = job.payload.get("trigger_seq")
    await plan_demos(
        ctx.db,
        ctx.llm,
        ctx.settings,
        learner_id=job.learner_id,
        session_id=job.session_id,
        trigger_seq=int(str(trigger)) if trigger is not None else None,
    )


__all__ = ["declined_key", "plan_demos", "request_id_of", "request_key"]
