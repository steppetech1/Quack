"""«Сегодня» preparation — the job `today.prepare` (ТЗ Ф4 WP16.8; Т-§14.4, Т-§13.4). D-B-184.

- The trigger is `today.shown {domain_id, items}` (the WP7.1 row). For each item the code picks
  the level — the frontier of the skill against the levels its hubs require (all levels without
  a hub) — and the bank candidates (`select_tasks`, Т-§5.7).
- One call of `today_planner` (Spark): per recommendation the bank tasks (labels `K…`), whether to
  generate, the draft steps. Tasks are cut so the call stays within `label_max_tasks` labels.
- Write: per item a draft plan in `content.session_plans` with status `prepared`, under the
  session id the session of this item will take — `uuid5(learner, "today:{trigger}:{skill}")`;
  `generate` is an `agent.requested {kind: task_generation}` of the skill and level, P2.
  A repeat of the job writes nothing new (law 5).
"""

from __future__ import annotations

import json
from uuid import UUID, uuid5

from quack.agents.schemas import TodayPlannerOutput
from quack.events.event_types import AgentRequested, TodayItem, TodayShown
from quack.events.log import NewEvent, append_events
from quack.events.records import AgentParamsRecord, PlanUpdateRecord
from quack.events.values import Level
from quack.graph.read import GraphReader, HubGoal, create_graph_reader
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.llm.labels import LabelMap, UnknownLabelError
from quack.logging import get_logger
from quack.practice.repo import event_by_seq
from quack.practice.select import select_tasks
from quack.practice.task_store import get_task
from quack.practice.today_plan import prepared_session_id

log = get_logger(__name__)

PLANNER_ROLE = "today_planner"
ALL_LEVELS: list[Level] = [1, 2, 3, 4, 5, 6]
NONE_LINE = "(none)"

_PREPARED = (
    "INSERT INTO content.session_plans (session_id, learner_id, plan, status) "
    "VALUES (%(session_id)s, %(learner_id)s, %(plan)s, 'prepared') "
    "ON CONFLICT (session_id) DO NOTHING"
)


def _target_levels(item: TodayItem, hubs: list[HubGoal]) -> list[Level]:
    levels = {
        level
        for hub in hubs
        if item.hub_id is None or hub.hub_id == item.hub_id
        for topic in hub.topics
        if topic.topic_id == item.topic_id
        for level in topic.required_levels
    }
    return sorted(levels) or ALL_LEVELS


async def prepare_today(
    ctx: JobContext, reader: GraphReader, *, learner_id: UUID, trigger_seq: int
) -> int:
    """The number of plans written; `0` for a trigger that is not a `today.shown`."""
    trigger = await event_by_seq(ctx.db, learner_id=learner_id, seq=trigger_seq)
    if trigger is None or trigger.type != "today.shown":
        return 0
    shown = TodayShown.model_validate(trigger.payload)
    settings = ctx.settings
    hubs = await reader.domain_goals(shown.domain_id)
    per_item = max(1, settings.label_max_tasks // len(shown.items))
    levels: dict[UUID, Level] = {}
    candidates: dict[UUID, list[UUID]] = {}
    for item in shown.items:
        targets = _target_levels(item, hubs)
        frontier = await reader.frontier(item.skill_id, targets)
        levels[item.skill_id] = frontier.level if frontier.level is not None else targets[-1]
        picked = await select_tasks(
            ctx.neo4j,
            settings,
            learner_id=learner_id,
            skill_id=item.skill_id,
            level=levels[item.skill_id],
        )
        # WP13.5 «Сгенерированное проверено»: a bank task is shown only when `verification =
        # checked`, so the plan names no other; an empty bank means `generate` (WP16.8).
        candidates[item.skill_id] = [t.task_id for t in picked if t.checked][:per_item]
    labels = LabelMap.build(
        settings,
        skills=[item.skill_id for item in shown.items],
        tasks=[t for item in shown.items for t in candidates[item.skill_id]],
    )
    recommendations, tasks_block = [], []
    for n, item in enumerate(shown.items, start=1):
        skill = labels.label("skill", item.skill_id)
        reasons = ", ".join(item.reasons)
        recommendations.append(f"{n}. {skill} level {levels[item.skill_id]}: {reasons}")
        for task_id in candidates[item.skill_id]:
            doc = await get_task(ctx.db, learner_id=learner_id, task_id=task_id)
            label = labels.label("task", task_id)
            if doc is not None and label is not None:
                tasks_block.append(f"{label} (for {n}): {doc.condition.text}")
    prompt = settings.llm_roles[PLANNER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'today_planner' has no prompt")
    result = await ctx.llm.generate_structured(
        PLANNER_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        TodayPlannerOutput,
        {
            "recommendations": "\n".join(recommendations),
            "candidate_tasks": "\n".join(tasks_block) or NONE_LINE,
            "cards": NONE_LINE,
        },
        learner_id,
        None,
        None,
        f"today.prepare:{trigger_seq}",
    )
    if len(result.value.items) != len(shown.items):
        # One plan item per recommendation, in order (the prompt's contract): with another count
        # the plans cannot be told from each other, so none is written and the teacher starts
        # from the goal by itself (WP16.8).
        log.warning(
            "today_plan_count_mismatch", asked=len(shown.items), got=len(result.value.items)
        )
        return 0
    plans: list[dict[str, object]] = []
    requests: list[NewEvent] = []
    for item, planned in zip(shown.items, result.value.items, strict=True):
        steps = [s.strip() for s in planned.plan_steps if s.strip()]
        for label in planned.tasks:
            try:
                task_id = labels.resolve("task", label)
            except UnknownLabelError:
                continue  # a label outside the candidates is dropped (Т-§4.5)
            doc = await get_task(ctx.db, learner_id=learner_id, task_id=task_id)
            if doc is not None:
                steps.append(f"Task: {doc.condition.text}")
        if not steps:
            continue
        plan = PlanUpdateRecord(
            completed_steps=[],
            current_step=steps[0],
            next_steps=steps[1:3],
            branches=[],
            cancel_request_ids=[],
        )
        plans.append(
            {
                "session_id": prepared_session_id(learner_id, trigger_seq, item.skill_id),
                "learner_id": learner_id,
                "plan": json.dumps(plan.model_dump(mode="json")),
            }
        )
        if planned.generate:
            key = f"today.prepare:{trigger_seq}:{item.skill_id}:generate"
            requests.append(
                NewEvent(
                    type="agent.requested",
                    payload=AgentRequested(
                        request_id=uuid5(learner_id, key),
                        kind="task_generation",
                        params=AgentParamsRecord(
                            skill_id=item.skill_id, level=levels[item.skill_id]
                        ),
                        needed_in_session=False,
                    ),
                    actor=prompt,
                    idempotency_key=key,
                    domain_id=shown.domain_id,
                    causation_seq=trigger_seq,
                )
            )
    async with ctx.db.transaction():  # law 7: the plans and the requests together
        for params in plans:
            await ctx.db.execute(_PREPARED, params)
        if requests:
            await append_events(ctx.db, learner_id, requests)
    return len(plans)


@register_job_handler("today.prepare")
async def handle_today_prepare(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None:
        raise ValueError("today.prepare needs learner_id")
    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=job.learner_id)
    await prepare_today(
        ctx, reader, learner_id=job.learner_id, trigger_seq=int(str(job.payload["trigger_seq"]))
    )
