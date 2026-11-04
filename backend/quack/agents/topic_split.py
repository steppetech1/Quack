"""`agent.topic_split` — the split of a topic by a phrase in the chat (ТЗ Ф4 WP8.2, §6.4;
Т-§4.11; issue #245). D-B-213.

The teacher marks the intent `split_topic` with the label of the topic; `turn_commit` writes
`agent.requested {kind: topic_split, topic_id}` and the turn does not wait (law 9). This job
answers it: one `builder` call in the Split mode spreads the skills of the topic over two or more
named parts; code checks that every skill is in exactly one part (labels, Т-§4.5) and writes:

- a topic of several skills: `topic.split`; the part with the most skills keeps the id of the
  topic (hubs and plans that name it stay valid), the others are new topics;
- a topic of one skill (Т-§4.11): `topic.split` first — the first part keeps the id of the topic
  and the old skill, the other parts are new empty topics — then `skill.split` of that skill into
  one narrower skill per part, each in its topic (D-A-226: the projector skips a skill part whose
  topic is not there yet). The old skill keeps its evidence and the dispatcher re-extracts its
  episodes (Т-§2.8).

Ids of new topics and skills are `uuid5` of the normalized name (Т-§4.4). Keys of the events
follow the request, so a repeat writes nothing even if the model words the parts differently.
A split that does not pass the check (one retry through the gateway) writes nothing to the
graph and the request is answered `ok: false` (law 7). The lock `domain:{id}` is the job's.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from quack.agents.delivery import completed_key, deliver
from quack.agents.reconcile import BUILDER_ROLE, node_id, normalize
from quack.agents.schemas import SplitOutput
from quack.config import Settings
from quack.events.event_types import AgentRequested, SkillPart, SkillSplit, TopicPart, TopicSplit
from quack.events.log import NewEvent, append_events
from quack.graph.read import GraphReader, create_graph_reader
from quack.graph.read.models import SkillFact
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob, JobFailedPermanently
from quack.llm.labels import LabelMap, UnknownLabelError
from quack.logging import get_logger
from quack.practice.repo import agent_request, event_by_key

log = get_logger(__name__)

KIND = "topic_split"
SPLIT_MODE = "Split"


@dataclass(frozen=True)
class PartPlan:
    name: str
    skill_ids: tuple[UUID, ...]  # the skills of the topic the part takes (empty for one skill)


def split_problem(output: SplitOutput, labels: LabelMap, skill_count: int) -> str | None:
    """What is wrong with the answer, in words for the model; `None` — it can be written. Two
    or more parts with different non-empty names; a topic of several skills: every label given
    once in exactly one part, none unknown; a topic of one skill: no labels (D-B-213)."""
    parts = output.parts
    names = [normalize(p.name) for p in parts]
    if len(parts) < 2 or not all(names) or len(set(names)) != len(names):
        return "give two or more parts with different non-empty names"
    seen: list[UUID] = []
    for part in parts:
        for label in part.skills:
            try:
                seen.append(labels.resolve("skill", label))
            except UnknownLabelError:
                return f"unknown skill label {label}; use only the given labels"
    if skill_count == 1:
        return None if not seen else "the topic has one skill: leave `skills` empty"
    if len(set(seen)) != len(seen):
        return "a skill is in more than one part; put every skill into exactly one part"
    if len(seen) != skill_count or any(not p.skills for p in parts):
        return "every skill must be in a part and no part may be empty"
    return None


def plan_parts(output: SplitOutput, labels: LabelMap) -> list[PartPlan]:
    return [
        PartPlan(" ".join(p.name.split()), tuple(labels.resolve("skill", s) for s in p.skills))
        for p in output.parts
    ]


def build_events(
    *,
    learner_id: UUID,
    domain_id: UUID,
    topic_id: UUID,
    skill_ids: Sequence[UUID],
    unnamed: Sequence[UUID],
    parts: Sequence[PartPlan],
    request_id: UUID,
    actor: str,
    session_id: UUID | None,
    causation_seq: int,
) -> list[NewEvent]:
    """`topic.split` (for a topic of one skill, then `skill.split`), keyed by the request."""

    def new(event_type: str, payload: SkillSplit | TopicSplit) -> NewEvent:
        return NewEvent(
            type=event_type,
            payload=payload,
            actor=actor,
            idempotency_key=f"topic_split:{request_id}:{event_type}",
            domain_id=domain_id,
            session_id=session_id,
            causation_seq=causation_seq,
        )

    events: list[NewEvent] = []
    if len(skill_ids) == 1:
        # D-A-226 (#245): `skill.split` skips a part whose topic does not exist yet, and
        # `topic.split` cannot cover a skill that is not there — so the topics come first:
        # the first part keeps the id of the topic and the old skill, the others are empty new
        # topics; then the skill is split into one narrower skill per part, each in its topic.
        part_topics = [
            topic_id if i == 0 else node_id(learner_id, domain_id, "topic", p.name)
            for i, p in enumerate(parts)
        ]
        topic_parts = [
            TopicPart(
                topic_id=part_topics[i],
                name=p.name,
                skill_ids=list(skill_ids) if i == 0 else [],
            )
            for i, p in enumerate(parts)
        ]
        events.append(new("topic.split", TopicSplit(topic_id=topic_id, into=topic_parts)))
        events.append(
            new(
                "skill.split",
                SkillSplit(
                    skill_id=skill_ids[0],
                    into=[
                        SkillPart(
                            skill_id=node_id(learner_id, domain_id, "skill", p.name),
                            name=p.name,
                            topic_id=part_topics[i],
                        )
                        for i, p in enumerate(parts)
                    ],
                ),
            )
        )
        return events
    keeper = max(range(len(parts)), key=lambda i: (len(parts[i].skill_ids), -i))
    topic_parts = [
        TopicPart(
            topic_id=topic_id if i == keeper else node_id(learner_id, domain_id, "topic", p.name),
            name=p.name,
            skill_ids=[*p.skill_ids, *(unnamed if i == keeper else ())],
        )
        for i, p in enumerate(parts)
    ]
    events.append(new("topic.split", TopicSplit(topic_id=topic_id, into=topic_parts)))
    return events


def _clean(text: str) -> str:
    return " ".join(text.split())


def _skill_lines(skills: Sequence[SkillFact], labels: LabelMap) -> str:
    return "\n".join(f"{labels.label('skill', s.skill_id)}: {_clean(s.name or '')}" for s in skills)


async def run_split(
    ctx: JobContext, reader: GraphReader, *, learner_id: UUID, request_id: UUID
) -> list[str]:
    """Answers one request `topic_split`; returns the types of the events written."""
    if await event_by_key(ctx.db, learner_id=learner_id, key=completed_key(request_id)):
        return []  # answered already: a retry pays for no second call
    requested = await agent_request(ctx.db, learner_id=learner_id, request_id=request_id)
    settings: Settings = ctx.settings
    prompt = settings.llm_roles[BUILDER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'builder' has no prompt")
    topic_id = AgentRequested.model_validate(requested.payload).params.topic_id
    if topic_id is None:
        raise JobFailedPermanently("topic_split needs topic_id")
    memories = await reader.topic_memory([topic_id])  # a reader bound to the learner (law 1)
    memory = memories[0] if memories else None
    domain_id = (memory.domain_id if memory else None) or requested.domain_id
    named = [s for s in memory.skills if s.name] if memory else []
    error = (
        "topic_not_found"
        if memory is None or domain_id is None
        else None
        if named
        else "topic_without_skills"
    )
    written: list[str] = []
    if error is None and memory is not None and domain_id is not None:
        labels = LabelMap.build(
            settings, skills=[s.skill_id for s in named], topics=[memory.topic_id]
        )
        answer = await ctx.llm.generate_structured(
            BUILDER_ROLE,
            int(prompt.rsplit("@", 1)[1]),
            SplitOutput,
            {
                "mode": SPLIT_MODE,
                "subjects": f"{labels.label('topic', memory.topic_id)}: {_clean(memory.name)}",
                "candidates": _skill_lines(named, labels),
            },
            learner_id,
            requested.session_id,
            None,
            f"graph.topic_split:{request_id}",
            check=lambda out: split_problem(out, labels, len(named)),
        )
        if split_problem(answer.value, labels, len(named)) is not None:
            error = "split_not_valid"
            log.warning("topic_split_dropped", request_id=str(request_id))
        else:
            events = build_events(
                learner_id=learner_id,
                domain_id=domain_id,
                topic_id=memory.topic_id,
                skill_ids=[s.skill_id for s in named],
                unnamed=[s.skill_id for s in memory.skills if not s.name],
                parts=plan_parts(answer.value, labels),
                request_id=request_id,
                actor=prompt,
                session_id=requested.session_id,
                causation_seq=requested.seq,
            )
            await append_events(ctx.db, learner_id, events)
            written = [e.type for e in events]
    await deliver(
        ctx.db,
        learner_id=learner_id,
        request_id=request_id,
        job_key=None,
        kind=KIND,
        ok=error is None,
        actor=prompt,
        error=error,
        domain_id=domain_id,
        session_id=requested.session_id,
        causation_seq=requested.seq,
    )
    return written


@register_job_handler("agent.topic_split")
async def handle_agent_topic_split(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None:
        raise ValueError(f"{job.kind} needs learner_id")
    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=job.learner_id)
    await run_split(
        ctx, reader, learner_id=job.learner_id, request_id=UUID(str(job.payload["request_id"]))
    )
