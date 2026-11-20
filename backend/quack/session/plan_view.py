"""The action graph of a session, the read side (`GET /v1/sessions/{id}/plan`).

Three layers, each with its own author (owner decision 2026-11-19):

1. the **goal** — one line the teacher writes once (the plan document);
2. the **landmarks** — 3 to `plan_landmarks_max` big nodes, skills or topics. **Code** builds
   them from memory (law 2): the goal hub of the session (`GraphReader.domain_goals`) or its
   topic, the skills of those topics (`topic_memory`), the order from `REQUIRES`, "done" from the
   levels of the skills. The teacher may rarely add or remove a landmark (`landmark_edits`);
3. the **headlights** — the current step and the next ones, written by the teacher into the
   plan document only when they change.

The route writes nothing. A graph that cannot be read (Neo4j down) gives no landmarks, the goal
and the steps from Postgres still come (law 9: the hot path never fails because of memory).

The plan document is parsed by `quack.practice.session_plan.lift_plan` (shared with the teacher
side and tolerant of malformed parts).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError

from quack.api.errors import NEO4J_OUTAGE_ERRORS
from quack.config import Settings
from quack.db.db import Db
from quack.events.records import LandmarkRefRecord, PlanStepRecord, PlanUpdateRecord
from quack.graph.read import GraphReader, HubGoal, SkillFact, SliceFocus, TopicMemory
from quack.logging import get_logger
from quack.practice.session_plan import PlanDoc, lift_plan
from quack.session.schemas import PlanDemo, PlanLandmark, PlanProgress, PlanStep, SessionPlanView
from quack.session.sessions import SessionView

log = get_logger(__name__)

type Kind = Literal["skill", "topic"]
type Ref = tuple[Kind, UUID]

_PLAN = (
    "SELECT plan, updated_at FROM content.session_plans "
    "WHERE session_id = %(session_id)s AND learner_id = %(learner_id)s"
)
_TURN_PLANS = (
    "SELECT payload FROM core.events "
    "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s "
    "AND type = 'turn.committed' ORDER BY seq"
)
_DEMOS = (
    "SELECT r.payload ->> 'request_id' AS request_id, "
    "r.payload -> 'params' ->> 'step_id' AS step_id, r.payload -> 'params' ->> 'title' AS title, "
    "(SELECT c.payload ->> 'ok' FROM core.events c WHERE c.learner_id = r.learner_id "
    "AND c.type = 'agent.completed' AND c.payload ->> 'kind' = 'viz' "
    "AND c.payload ->> 'request_id' = r.payload ->> 'request_id' "
    "ORDER BY c.seq DESC LIMIT 1) AS ok "
    "FROM core.events r WHERE r.learner_id = %(learner_id)s AND r.session_id = %(session_id)s "
    "AND r.type = 'agent.requested' AND r.payload ->> 'kind' = 'viz' "
    "AND r.payload -> 'params' ->> 'step_id' IS NOT NULL ORDER BY r.seq"
)
_REACHED = ("proven", "implied")


def landmark_ref(landmark: LandmarkRefRecord | None) -> Ref | None:
    """Convert a plan landmark record to a `(kind, id)` key, or `None`."""
    return None if landmark is None else (landmark.kind, landmark.id)


# --- landmarks from memory ---------------------------------------------------------------


@dataclass(frozen=True)
class Point:
    """A landmark: a skill, or a topic with the skills that stand for it."""

    kind: Kind
    id: UUID
    title: str
    reached: bool
    skill_ids: tuple[UUID, ...]
    by_teacher: bool = False
    topic_id: UUID | None = None


@dataclass(frozen=True)
class TopicSource:
    """A topic of the goal with the levels the goal requires of it and its skills."""

    topic_id: UUID
    name: str
    levels: Sequence[int]
    skills: Sequence[SkillFact]


def skill_reached(skill: SkillFact, levels: Sequence[int]) -> bool:
    """Every required level is proven or implied (Т-§8.5, the same rule as `frontier`)."""
    by_level = {fact.level: fact.status for fact in skill.levels}
    return bool(levels) and all(by_level.get(level) in _REACHED for level in levels)


def _topological[T](items: Sequence[T], key: Any, needs: Mapping[Any, set[Any]]) -> list[T]:
    """Prerequisites first; the rest keeps its order. The links are authored by a model, so a
    cycle is possible: the earliest remaining item is taken to break it."""
    placed: set[Any] = set()
    remaining = list(items)
    out: list[T] = []
    while remaining:
        ready = [i for i in remaining if needs.get(key(i), set()) <= placed]
        item = ready[0] if ready else remaining[0]
        remaining.remove(item)
        placed.add(key(item))
        out.append(item)
    return out


def _skill_needs(skills: Sequence[SkillFact], min_confidence: float) -> dict[UUID, set[UUID]]:
    """`(s)-[:REQUIRES]->(p)`: `p` is the prerequisite of `s` and comes first."""
    known = {s.skill_id for s in skills}
    return {
        s.skill_id: {
            link.skill_id
            for link in s.requires
            if link.confidence >= min_confidence and link.skill_id in known
        }
        - {s.skill_id}
        for s in skills
    }


def holds(point: Point, ref: Ref) -> bool:
    """The point is the node itself, a topic point holding the skill, or a skill of the topic."""
    kind, node_id = ref
    return (
        (point.kind == kind and point.id == node_id)
        or (kind == "skill" and point.kind == "topic" and node_id in point.skill_ids)
        or (kind == "topic" and point.kind == "skill" and point.topic_id == node_id)
    )


def _current_index(points: Sequence[Point], current: Ref | None) -> int | None:
    if current is None:
        return None
    kind, node_id = current
    if kind == "topic":
        exact = next(
            (i for i, p in enumerate(points) if p.kind == "topic" and p.id == node_id), None
        )
        if exact is not None:
            return exact
        matching = [
            (i, p) for i, p in enumerate(points) if p.kind == "skill" and p.topic_id == node_id
        ]
        if matching:
            return next((i for i, p in matching if not p.reached), matching[0][0])
        return None
    return next((i for i, p in enumerate(points) if holds(p, current)), None)


def _nearest_unreached(points: list[Point], limit: int, current: Ref | None) -> list[Point]:
    """More points than fit: the nearest ones not reached yet, in order; the point of the
    current step stays even when it is reached (the picture must show where we are)."""
    if len(points) <= limit:
        return points
    chosen = [i for i, p in enumerate(points) if not p.reached][:limit] or list(range(limit))
    at = _current_index(points, current)
    if at is not None and at not in chosen:
        chosen = [*chosen[: limit - 1], at]
    return [points[i] for i in sorted(chosen)]


def build_points(
    topics: Sequence[TopicSource],
    *,
    group_by_topic: bool,
    settings: Settings,
    current: Ref | None = None,
) -> list[Point]:
    """The landmarks of a goal. Skills in the order of `REQUIRES`; if there are more than
    `plan_landmarks_max` of them and the goal is a hub (`group_by_topic`) — the topics instead,
    in the order of the links between their skills; still more — the nearest not reached."""
    limit = settings.plan_landmarks_max
    min_confidence = settings.requires_min_confidence
    skills = [(t, s) for t in topics for s in t.skills]
    if not (group_by_topic and len(skills) > limit):
        ordered = _topological(
            skills,
            lambda pair: pair[1].skill_id,
            _skill_needs([s for _, s in skills], min_confidence),
        )
        return _nearest_unreached(
            [
                Point(
                    "skill",
                    s.skill_id,
                    s.name or "",
                    skill_reached(s, t.levels),
                    (s.skill_id,),
                    topic_id=t.topic_id,
                )
                for t, s in ordered
            ],
            limit,
            current,
        )
    topic_of = {s.skill_id: t.topic_id for t, s in skills}
    skill_needs = _skill_needs([s for _, s in skills], min_confidence)
    topic_needs: dict[UUID, set[UUID]] = {t.topic_id: set() for t in topics}
    for skill_id, needed in skill_needs.items():
        for other in needed:
            if topic_of[other] != topic_of[skill_id]:
                topic_needs[topic_of[skill_id]].add(topic_of[other])
    ordered_topics = _topological(topics, lambda t: t.topic_id, topic_needs)
    return _nearest_unreached(
        [
            Point(
                "topic",
                t.topic_id,
                t.name,
                bool(t.skills) and all(skill_reached(s, t.levels) for s in t.skills),
                tuple(s.skill_id for s in t.skills),
                topic_id=t.topic_id,
            )
            for t in ordered_topics
        ],
        limit,
        current,
    )


def apply_edits(points: list[Point], doc: PlanDoc, resolved: Mapping[Ref, Point]) -> list[Point]:
    """The teacher's edits over the computed points: removed ones go, added ones come in before
    the named landmark (or at the end). An added landmark memory does not know is dropped."""
    gone = {(r.kind, r.id) for r in doc.landmark_edits.removed}
    out = [p for p in points if (p.kind, p.id) not in gone]
    for add in doc.landmark_edits.added:
        ref: Ref = (add.kind, add.id)
        point = resolved.get(ref)
        if point is None or any((p.kind, p.id) == ref for p in out):
            continue
        before = (
            (add.before_kind, add.before_id)
            if add.before_kind is not None and add.before_id is not None
            else None
        )
        at = next((i for i, p in enumerate(out) if before == (p.kind, p.id)), len(out))
        out.insert(at, replace(point, by_teacher=True))
    return out


def visited_landmarks(payloads: Sequence[Mapping[str, Any]]) -> set[Ref]:
    """Landmarks named in `current_step.landmark` across `turn.committed` payloads."""
    found: set[Ref] = set()
    for payload in payloads:
        markup = payload.get("markup")
        if not isinstance(markup, Mapping) or "plan_update" not in markup:
            continue
        try:
            plan_update = PlanUpdateRecord.model_validate(markup["plan_update"])
        except ValidationError:
            continue
        if plan_update.current_step is not None and plan_update.current_step.landmark is not None:
            lm = plan_update.current_step.landmark
            found.add((lm.kind, lm.id))
    return found


def view_of(
    points: Sequence[Point],
    current: Ref | None,
    visited: Collection[Ref] = (),
) -> list[PlanLandmark]:
    """The statuses: the point of the current step is `current`, even when its levels are
    reached already; then `done` by the levels or if visited earlier in the session, else
    `upcoming`. A topic step finds a topic point or the first unreached skill of that topic;
    a skill step finds the skill or the topic point that holds it."""
    chosen = _current_index(points, current)
    visited_set = set(visited)
    visited_idxs = {idx for ref in visited_set if (idx := _current_index(points, ref)) is not None}
    return [
        PlanLandmark(
            kind=p.kind,
            id=p.id,
            title=p.title,
            status=(
                "current"
                if i == chosen
                else "done"
                if (p.reached or (p.kind, p.id) in visited_set or i in visited_idxs)
                else "upcoming"
            ),
            added_by_teacher=p.by_teacher,
        )
        for i, p in enumerate(points)
    ]


# --- reading memory ----------------------------------------------------------------------


def _topic_source(
    memory: TopicMemory,
    levels: Sequence[int],
    fresh_skills: Mapping[UUID, SkillFact] | None = None,
) -> TopicSource:
    skills = (
        memory.skills
        if not fresh_skills
        else [
            s.model_copy(update={"levels": fresh.levels})
            if (fresh := fresh_skills.get(s.skill_id)) is not None
            else s
            for s in memory.skills
        ]
    )
    return TopicSource(memory.topic_id, memory.name, levels, skills)


def points_from_memory(
    hub_id: UUID | None,
    topic_id: UUID | None,
    hubs: Sequence[HubGoal],
    memories: Mapping[UUID, TopicMemory],
    settings: Settings,
    *,
    current: Ref | None = None,
    fresh_skills: Mapping[UUID, SkillFact] | None = None,
) -> list[Point]:
    """Compute goal landmarks from pre-fetched `hubs`, `memories`, and optional fresh skills."""
    default = settings.control_type_levels["no_goal"]
    if hub_id is not None:
        hub = next((g for g in hubs if g.hub_id == hub_id), None)
        if hub is None:
            return []
        topics = [
            _topic_source(memories[t.topic_id], t.required_levels or default, fresh_skills)
            for t in hub.topics
            if t.topic_id in memories
        ]
        return build_points(topics, group_by_topic=True, settings=settings, current=current)
    if topic_id is not None and topic_id in memories:
        topics = [_topic_source(memories[topic_id], default, fresh_skills)]
        return build_points(topics, group_by_topic=False, settings=settings, current=current)
    return []


def resolve_added_points(
    doc: PlanDoc,
    topic_memories: Mapping[UUID, TopicMemory],
    skills: Mapping[UUID, SkillFact],
    settings: Settings,
) -> dict[Ref, Point]:
    """Build `Point` records for `doc.landmark_edits.added` from pre-fetched memories and skills."""
    default = settings.control_type_levels["no_goal"]
    out: dict[Ref, Point] = {}
    for add in doc.landmark_edits.added:
        ref: Ref = (add.kind, add.id)
        if ref in out:
            continue
        if add.kind == "topic" and (memory := topic_memories.get(add.id)) is not None:
            source = _topic_source(memory, default, skills)
            reached = bool(source.skills) and all(skill_reached(s, default) for s in source.skills)
            out[ref] = Point(
                "topic",
                memory.topic_id,
                memory.name,
                reached,
                tuple(s.skill_id for s in memory.skills),
                topic_id=memory.topic_id,
            )
        elif add.kind == "skill" and (skill := skills.get(add.id)) is not None:
            out[ref] = Point(
                "skill",
                skill.skill_id,
                skill.name or "",
                skill_reached(skill, default),
                (skill.skill_id,),
                topic_id=skill.topic_id,
            )
    return out


async def _goal_memory(
    reader: GraphReader, session: SessionView
) -> tuple[list[HubGoal], dict[UUID, TopicMemory]]:
    if session.hub_id is not None:
        hubs = await reader.domain_goals(session.domain_id)
        hub = next((g for g in hubs if g.hub_id == session.hub_id), None)
        if hub is None or not hub.topics:
            return hubs, {}
        topic_ids = [t.topic_id for t in hub.topics]
        memories = {m.topic_id: m for m in await reader.topic_memory(topic_ids)}
        return hubs, memories
    if session.topic_id is not None:
        memories = {m.topic_id: m for m in await reader.topic_memory([session.topic_id])}
        return [], memories
    return [], {}


async def _added_points(
    reader: GraphReader,
    settings: Settings,
    doc: PlanDoc,
    *,
    learner_id: UUID,
    session_id: UUID,
) -> dict[Ref, Point]:
    """Titles and levels of the landmarks the teacher added, from memory."""
    topic_ids = list(dict.fromkeys(a.id for a in doc.landmark_edits.added if a.kind == "topic"))
    skill_ids = list(dict.fromkeys(a.id for a in doc.landmark_edits.added if a.kind == "skill"))
    topic_memories = (
        {m.topic_id: m for m in await reader.topic_memory(topic_ids)} if topic_ids else {}
    )
    skills: dict[UUID, SkillFact] = {}
    if skill_ids:
        # No journal wait: the reader of this route has no `db`, a read of a live graph.
        found = await reader.slice_for_turn(learner_id, session_id, SliceFocus(skill_ids=skill_ids))
        skills = {s.skill_id: s for s in found.skills}
    return resolve_added_points(doc, topic_memories, skills, settings)


async def landmarks_of(
    reader: GraphReader,
    settings: Settings,
    session: SessionView,
    doc: PlanDoc,
    *,
    learner_id: UUID,
) -> list[Point]:
    hubs, memories = await _goal_memory(reader, session)
    current = None if doc.current_step is None else landmark_ref(doc.current_step.landmark)
    points = points_from_memory(
        session.hub_id, session.topic_id, hubs, memories, settings, current=current
    )
    edits = doc.landmark_edits
    if not edits.added and not edits.removed:
        return points
    resolved = (
        await _added_points(
            reader, settings, doc, learner_id=learner_id, session_id=session.session_id
        )
        if edits.added
        else {}
    )
    return apply_edits(points, doc, resolved)


def _step_view(step: PlanStepRecord) -> PlanStep:
    return PlanStep(
        id=step.id, text=step.text, landmark_id=None if step.landmark is None else step.landmark.id
    )


async def session_demos(db: Db, *, learner_id: UUID, session_id: UUID) -> list[PlanDemo]:
    """The demos ordered ahead for the steps of the session, with where each one stands: the pair of
    events of its request (`agent.requested` and, when the job finished, `agent.completed`)."""
    rows = await db.fetch_all(_DEMOS, {"learner_id": learner_id, "session_id": session_id})
    demos: list[PlanDemo] = []
    for row in rows:
        status: Literal["preparing", "ready", "failed"] = (
            "preparing" if row["ok"] is None else "ready" if row["ok"] == "true" else "failed"
        )
        demos.append(
            PlanDemo(
                request_id=UUID(row["request_id"]),
                step_id=row["step_id"],
                title=row["title"] or row["step_id"],
                status=status,
            )
        )
    return demos


async def session_plan_view(
    db: Db,
    reader: GraphReader,
    settings: Settings,
    *,
    learner_id: UUID,
    session: SessionView,
) -> SessionPlanView:
    """The plan of a session of the learner. The document is read from Postgres; the landmarks
    from memory, and only a Neo4j outage is swallowed (the plan comes without them)."""
    row = await db.fetch_one(_PLAN, {"learner_id": learner_id, "session_id": session.session_id})
    doc = lift_plan(None if row is None else row["plan"])
    updated_at: datetime | None = None if row is None else row["updated_at"]
    landmarks: list[PlanLandmark] = []
    progress: PlanProgress | None = None
    try:
        points = await landmarks_of(reader, settings, session, doc, learner_id=learner_id)
    except NEO4J_OUTAGE_ERRORS as exc:
        log.warning("session_plan_memory_unavailable", error=repr(exc))
        points = []
    if points:
        current = None if doc.current_step is None else landmark_ref(doc.current_step.landmark)
        rows = await db.fetch_all(
            _TURN_PLANS, {"learner_id": learner_id, "session_id": session.session_id}
        )
        visited = visited_landmarks([r["payload"] for r in rows])
        landmarks = view_of(points, current, visited=visited)
        progress = PlanProgress(
            done=sum(m.status == "done" for m in landmarks),
            total=len(landmarks),
        )
    return SessionPlanView(
        goal=doc.goal,
        landmarks=landmarks,
        current_step=None if doc.current_step is None else _step_view(doc.current_step),
        next_steps=[_step_view(s) for s in doc.next_steps],
        progress=progress,
        updated_at=updated_at,
        demos=await session_demos(db, learner_id=learner_id, session_id=session.session_id),
    )
