"""The frontier analyst, WP16.1 (ТЗ Ф4; Т-§13.5, Т-§13.2, Т-§9.5; `T-AG1`). D-B-176.

- Roles: `analyst` (Spark); `analyst_deep` (Wonder) at the end of the session and after a digest
  with `plan_divergence`; both render `analyst@1`.
- Input: structure only (law 10) — the target skills and their prerequisites, misconceptions,
  evidence of the recent episodes, the session plan, goals, the profile; the previous digest and
  manner are never an input (`T-AG1`). The memory blocks are rendered by the caller from
  `quack.graph.read`; the evidence block is rendered here from `observation.extracted`.
- Output `AnalystOutput` with labels; a label the call did not give goes back once (Т-§4.5), an
  item still wrong after that is dropped.
- Write: `digest.written {domain_id, text, as_of_seq, structured, plan_divergence, manner}` and
  one `agent.requested` from actor `agent:analyst` per forecast item (Т-§13.2), in one
  transaction (law 7), with keys from the trigger (law 5).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

from quack.agents.schemas import AnalystOutput
from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import AgentRequested, DigestWritten, ObservationExtracted
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.events.records import (
    AgentParamsRecord,
    DigestStructuredRecord,
    FrontierItemRecord,
    GapHypothesisRecord,
    PreparationRecord,
    SuggestedMoveRecord,
    UncertaintyRecord,
)
from quack.graph.read import (
    GraphReader,
    HubGoal,
    MemorySlice,
    ProfileFacts,
    SliceFocus,
    create_graph_reader,
)
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap
from quack.practice.repo import event_by_seq, session_events_up_to

ANALYST_ROLE = "analyst"
DEEP_ROLE = "analyst_deep"
ANALYST_ACTOR = "agent:analyst"
NONE_LINE = "(none)"

_OUTCOME = {"correct": "correct", "incorrect": "incorrect", "partial": "partly correct"}
_HELP = {"none": "on their own", "hint": "after a hint", "shown_solution": "after the solution"}


@dataclass(frozen=True)
class AnalystInput:
    """The blocks of `analyst@1` rendered by the caller, and the labels they use."""

    domain_id: UUID
    session_id: UUID | None
    as_of_seq: int
    skills_state: str
    misconceptions: str
    session_plan: str
    goals: str
    profile: str
    labels: LabelMap


def render_evidence(observations: Sequence[StoredEvent], labels: LabelMap) -> str:
    """Accepted elements of `observation.extracted`, one line each: skill, level, outcome, help,
    gap type, quote. Skills without a label are not in the input."""
    lines: list[str] = []
    for event in observations:
        for element in ObservationExtracted.model_validate(event.payload).elements:
            skill = labels.label("skill", element.skill_id) if element.skill_id else None
            if skill is None or element.kind != "evidence":
                continue
            outcome = _OUTCOME.get(element.outcome or "", "not judged")
            level = f" level {element.level}" if element.level is not None else ""
            gap = f", gap: {element.gap_type}" if element.gap_type != "none" else ""
            lines.append(
                f'{skill}{level}: {outcome}, {_HELP[element.help]}{gap}; "{element.quote}"'
            )
    return "\n".join(lines) or NONE_LINE


def _skill_ok(labels: LabelMap, label: str | None) -> bool:
    return label is None or label in labels.labels("skill")


def _misconception_ok(labels: LabelMap, label: str | None) -> bool:
    return label is None or label in labels.labels("misconception")


def analyst_errors(output: AnalystOutput, labels: LabelMap) -> str | None:
    """Т-§4.5: labels of the frontier, the hypotheses and the forecast must be given ones."""
    unknown = [
        *(f.skill for f in output.frontier if not _skill_ok(labels, f.skill)),
        *(g.skill for g in output.gap_hypotheses if not _skill_ok(labels, g.skill)),
        *(p.skill for p in output.forecast if p.skill and not _skill_ok(labels, p.skill)),
        *(
            p.misconception
            for p in output.forecast
            if p.misconception and not _misconception_ok(labels, p.misconception)
        ),
    ]
    if not unknown:
        return None
    return "unknown labels: " + ", ".join(dict.fromkeys(unknown)) + "; use only the given labels"


def digest_structured(output: AnalystOutput, labels: LabelMap) -> DigestStructuredRecord:
    """Labels become ids; an item with an unknown label is dropped."""
    skills = labels.labels("skill")
    misconceptions = labels.labels("misconception")
    forecast = [
        PreparationRecord(
            kind=p.kind,
            skill_id=skills[p.skill] if p.skill else None,
            level=p.level,
            misconception_id=misconceptions[p.misconception] if p.misconception else None,
            description=p.description,
        )
        for p in output.forecast
        if _skill_ok(labels, p.skill) and _misconception_ok(labels, p.misconception)
    ]
    return DigestStructuredRecord(
        frontier=[
            FrontierItemRecord(skill_id=skills[f.skill], level=f.level, state=f.state)
            for f in output.frontier
            if f.skill in skills
        ],
        gap_hypotheses=[
            GapHypothesisRecord(skill_id=skills[g.skill], gap_type=g.gap_type, reason=g.reason)
            for g in output.gap_hypotheses
            if g.skill in skills
        ],
        uncertainty=UncertaintyRecord(
            question=output.uncertainty.question, resolving_task=output.uncertainty.resolving_task
        )
        if output.uncertainty
        else None,
        moves=[SuggestedMoveRecord(move=m.move, reason=m.reason) for m in output.moves],
        forecast=forecast,
    )


def digest_key(domain_id: UUID, trigger_seq: int, role: str) -> str:
    return f"digest:{domain_id}:{trigger_seq}:{role}"


async def run_analyst(
    db: Db,
    gateway: LLMGateway,
    settings: Settings,
    *,
    learner_id: UUID,
    trigger_seq: int,
    deep: bool,
    facts: AnalystInput,
    observations: Sequence[StoredEvent],
) -> list[StoredEvent]:
    """One call of `analyst` or `analyst_deep`; the digest and the forecast requests."""
    role = DEEP_ROLE if deep else ANALYST_ROLE
    prompt = settings.llm_roles[role].prompt
    if prompt is None:
        raise ValueError(f"role {role!r} has no prompt")
    role_of_prompt, version = prompt.rsplit("@", 1)
    key = digest_key(facts.domain_id, trigger_seq, role)
    result = await gateway.generate_structured(
        role,
        int(version),
        AnalystOutput,
        {
            "skills_state": facts.skills_state or NONE_LINE,
            "misconceptions": facts.misconceptions or NONE_LINE,
            "evidence": render_evidence(observations, facts.labels),
            "session_plan": facts.session_plan or NONE_LINE,
            "goals": facts.goals or NONE_LINE,
            "profile": facts.profile or NONE_LINE,
        },
        learner_id,
        facts.session_id,
        None,
        key,
        prompt_role=role_of_prompt,
        check=lambda output: analyst_errors(output, facts.labels),
    )
    output = result.value
    structured = digest_structured(output, facts.labels)
    digest = NewEvent(
        type="digest.written",
        payload=DigestWritten(
            domain_id=facts.domain_id,
            text=output.text,
            as_of_seq=facts.as_of_seq,
            structured=structured,
            plan_divergence=output.plan_divergence,
            manner=output.manner,
        ),
        actor=prompt,
        idempotency_key=key,
        domain_id=facts.domain_id,
        session_id=facts.session_id,
        causation_seq=trigger_seq,
    )
    namespace = uuid5(facts.domain_id, key)
    requests = [
        NewEvent(
            type="agent.requested",
            payload=AgentRequested(
                request_id=uuid5(namespace, str(index)),
                kind=item.kind,
                params=AgentParamsRecord(
                    skill_id=item.skill_id,
                    level=item.level,
                    misconception_id=item.misconception_id,
                    description=item.description,
                ),
                needed_in_session=False,
            ),
            actor=ANALYST_ACTOR,
            idempotency_key=f"{key}:request:{index}",
            domain_id=facts.domain_id,
            session_id=facts.session_id,
            causation_seq=trigger_seq,
        )
        for index, item in enumerate(structured.forecast)
    ]
    async with db.transaction():
        return await append_events(db, learner_id, [digest, *requests])


# --- the job: blocks of `analyst@1` from memory (S4) ------------------------------------------
# `agents` may not import `session` (§4.1), so the blocks are rendered here, plainly.

_PLAN = (
    "SELECT plan FROM content.session_plans "
    "WHERE session_id = %(session_id)s AND learner_id = %(learner_id)s"
)


def _skills_block(found: MemorySlice, labels: LabelMap) -> str:
    lines = []
    for skill in found.skills:
        label = labels.label("skill", skill.skill_id)
        if label is None:
            continue
        levels = ", ".join(f"level {lv.level} {lv.status}" for lv in skill.levels) or "no data"
        lines.append(f"{label} {skill.name or ''}: {levels}".replace("  ", " "))
    return "\n".join(lines)


def _misconceptions_block(found: MemorySlice, labels: LabelMap) -> str:
    lines = []
    for item in found.misconceptions:
        label = labels.label("misconception", item.misconception_id)
        skill = labels.label("skill", item.skill_id)
        if label is not None and skill is not None:
            lines.append(f"{label} on {skill} ({item.status}): {item.statement}")
    return "\n".join(lines)


def _goals_block(hubs: Sequence[HubGoal]) -> str:
    lines = []
    for hub in hubs:
        when = (
            datetime.fromtimestamp(hub.deadline_at_ms / 1000, UTC).strftime("%Y-%m-%d")
            if hub.deadline_at_ms is not None
            else "no deadline"
        )
        topics = "; ".join(
            f"{t.name} levels {','.join(map(str, t.required_levels))}" for t in hub.topics
        )
        lines.append(f'Hub "{hub.name}" ({hub.control_type}, {when}): {topics}')
    return "\n".join(lines)


def _profile_block(facts: ProfileFacts) -> str:
    lines = [f"Preference {k}: {v}" for k, v in sorted(facts.prefs.items())]
    learned = {
        "helpful approach": facts.learned_helpful_approach,
        "confidence bias": facts.learned_confidence_bias,
        "branch rate": facts.learned_branch_rate,
        "reasoning share": facts.learned_reasoning_share,
    }
    lines.extend(f"Learned {k}: {'no data yet' if v is None else v}" for k, v in learned.items())
    return "\n".join(lines)


async def analyst_job(
    job: ClaimedJob, ctx: JobContext, reader: GraphReader, *, learner_id: UUID, deep: bool
) -> list[StoredEvent]:
    """`analyst.run` / `analyst.deep` (WP16.1): the trigger gives the domain and the session;
    the focus is the skills the session's observations judged; a trigger outside a session of a
    domain has nothing to analyse."""
    trigger_seq = int(str(job.payload["trigger_seq"]))
    trigger = await event_by_seq(ctx.db, learner_id=learner_id, seq=trigger_seq)
    if trigger is None or trigger.domain_id is None or trigger.session_id is None:
        return []
    session_id = trigger.session_id
    plan = ""
    events = await session_events_up_to(
        ctx.db, learner_id=learner_id, session_id=session_id, to_seq=trigger_seq
    )
    observations = [e for e in events if e.type == "observation.extracted"]
    row = await ctx.db.fetch_one(_PLAN, {"learner_id": learner_id, "session_id": session_id})
    if row is not None:
        doc = row["plan"]
        plan = f"Current step: {doc.get('current_step', '')}; next: " + "; ".join(
            doc.get("next_steps", [])
        )
    skill_ids = list(
        dict.fromkeys(
            el.skill_id
            for e in observations
            for el in ObservationExtracted.model_validate(e.payload).elements
            if el.skill_id is not None
        )
    )
    focus = SliceFocus(skill_ids=skill_ids, domain_id=trigger.domain_id)
    found = await reader.slice_for_turn(learner_id, session_id, focus)
    hubs = await reader.domain_goals(trigger.domain_id)
    profile = await reader.profile(learner_id)
    labels = LabelMap.build(
        ctx.settings,
        skills=list(dict.fromkeys([*skill_ids, *(s.skill_id for s in found.skills)])),
        misconceptions=[m.misconception_id for m in found.misconceptions],
        hubs=[hub.hub_id for hub in hubs],
    )
    facts = AnalystInput(
        domain_id=trigger.domain_id,
        session_id=session_id,
        as_of_seq=trigger_seq,
        skills_state=_skills_block(found, labels),
        misconceptions=_misconceptions_block(found, labels),
        session_plan=plan,
        goals=_goals_block(hubs),
        profile=_profile_block(profile),
        labels=labels,
    )
    return await run_analyst(
        ctx.db,
        ctx.llm,
        ctx.settings,
        learner_id=learner_id,
        trigger_seq=trigger_seq,
        deep=deep,
        facts=facts,
        observations=observations,
    )


@register_job_handler("analyst.run")
async def handle_analyst(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None:
        raise ValueError("analyst.run needs learner_id")
    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=job.learner_id)
    await analyst_job(job, ctx, reader, learner_id=job.learner_id, deep=False)


@register_job_handler("analyst.deep")
async def handle_analyst_deep(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None:
        raise ValueError("analyst.deep needs learner_id")
    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=job.learner_id)
    await analyst_job(job, ctx, reader, learner_id=job.learner_id, deep=True)
