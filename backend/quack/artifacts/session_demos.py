"""Mapping finished session visualizations (`viz`) to topic notes and hub cheat sheets.

A finished demo is an `agent.requested {kind: viz}` matched with an `agent.completed {kind: viz,
ok: true}` for the same `request_id`. Each demo belongs to a topic:

1. If the demo targets a plan step (`params.step_id`), look up that step in the session's
   `turn.committed` plan updates and use its `landmark`:
   - `landmark.kind == "topic"` -> `landmark.id` (canonicalized through merges);
   - `landmark.kind == "skill"` -> the skill's home topic.
2. Otherwise (free chat or a step without a landmark): use the topic of the latest placement
   event (`skill.placed`, `skill.created`, `topic.created`, `topic.proposed`) in that session
   before `agent.requested.seq`; if none, use `params.topic_id` / `params.skill_id`; if still
   none, use `topic_id` from `session.started`, or the home topic of the latest skill touched in
   that session before `agent.requested.seq`.

For a hub cheat sheet, demos of all topics in the hub, of sessions targeting the hub, and of the
requesting session (when `session_id` is given) are included.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from quack.artifacts.documents import Block, SystemBlock
from quack.db import Db
from quack.events.records import PlanStepRecord, PlanUpdateRecord
from quack.graph.ids import deterministic_id

DEMO_BLOCK_HEADING = "### Демо"
DEMO_LINK_PREFIX = "/v1/viz/"

_STRUCTURE_TYPES: tuple[str, ...] = (
    "skill.created",
    "skill.placed",
    "skill.split",
    "skill.merged",
    "topic.split",
    "topic.merged",
)
_SESSION_EVENT_TYPES: tuple[str, ...] = (
    "session.started",
    "turn.committed",
    "skill.placed",
    "skill.created",
    "topic.created",
    "topic.proposed",
    "observation.extracted",
)
_PLACEMENT_TYPES: frozenset[str] = frozenset(
    {"skill.placed", "skill.created", "topic.created", "topic.proposed"}
)

_READY_VIZ = (
    "SELECT r.seq, r.session_id, r.domain_id, r.payload "
    "FROM core.events r "
    "WHERE r.learner_id = %(learner_id)s "
    "AND r.type = 'agent.requested' "
    "AND r.payload ->> 'kind' = 'viz' "
    "AND EXISTS ("
    "SELECT 1 FROM core.events c "
    "WHERE c.learner_id = r.learner_id "
    "AND c.type = 'agent.completed' "
    "AND c.payload ->> 'kind' = 'viz' "
    "AND c.payload ->> 'request_id' = r.payload ->> 'request_id' "
    "AND c.payload ->> 'ok' = 'true'"
    ") "
    "ORDER BY r.seq"
)
_STRUCTURE_EVENTS = (
    "SELECT type, payload FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = ANY(%(types)s) ORDER BY seq"
)
_SESSION_EVENTS = (
    "SELECT seq, session_id, type, payload FROM core.events "
    "WHERE learner_id = %(learner_id)s AND session_id = ANY(%(session_ids)s) "
    "AND type = ANY(%(types)s) ORDER BY seq"
)
_HUB_CHEATSHEET_SESSIONS = (
    "SELECT DISTINCT session_id FROM core.events "
    "WHERE learner_id = %(learner_id)s AND session_id IS NOT NULL "
    "AND type = 'agent.requested' AND payload ->> 'kind' = 'artifact' "
    "AND payload -> 'params' ->> 'artifact' = 'cheatsheet' "
    "AND payload -> 'params' ->> 'hub_id' = %(hub_id)s"
)
_IS_DOMAIN_ALL_HUB = (
    "SELECT 1 FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'hub.created' AND payload ->> 'hub_id' = %(hub_id)s "
    "AND payload ->> 'kind' = 'domain_all' LIMIT 1"
)
_DOMAIN_CREATED = (
    "SELECT seq FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'domain.created' AND payload ->> 'domain_id' = %(domain_id)s ORDER BY seq"
)


@dataclass(frozen=True)
class ReadyDemo:
    """A finished `viz` agent request."""

    request_id: UUID
    seq: int
    session_id: UUID | None
    domain_id: UUID | None
    step_id: str | None
    title: str
    param_topic_id: UUID | None = None
    param_skill_id: UUID | None = None


@dataclass(frozen=True)
class SessionEvent:
    """Minimal projection of a session event for pure demo-topic resolution."""

    seq: int
    type: str
    payload: Mapping[str, Any]


def _opt_uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _clean_text(text: object, limit: int | None = None) -> str:
    """One line of text; `limit` — at most that many characters (`None` — all of it)."""
    if not isinstance(text, str):
        return ""
    return " ".join(text.split())[:limit].strip()


def _sanitize_link_title(title: str) -> str:
    cleaned = _clean_text(title)
    return cleaned.replace("[", "(").replace("]", ")") or "Демо"


class SkillTopicIndex:
    """Canonical `skill_id -> topic_id` and `topic_id -> live_topic_id` index from events."""

    def __init__(self) -> None:
        self._skill_topic: dict[UUID, UUID] = {}
        self._skill_alias: dict[UUID, UUID] = {}
        self._topic_alias: dict[UUID, UUID] = {}

    def apply(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if event_type in ("skill.created", "skill.placed"):
            sid = _opt_uuid(payload.get("skill_id"))
            tid = _opt_uuid(payload.get("topic_id"))
            if sid is not None and tid is not None:
                self._skill_topic[sid] = tid
        elif event_type == "skill.split":
            for part in payload.get("into") or ():
                if not isinstance(part, Mapping):
                    continue
                sid = _opt_uuid(part.get("skill_id"))
                tid = _opt_uuid(part.get("topic_id"))
                if sid is not None and tid is not None:
                    self._skill_topic[sid] = tid
        elif event_type == "skill.merged":
            from_id = _opt_uuid(payload.get("from_id"))
            into_id = _opt_uuid(payload.get("into_id"))
            if from_id is not None and into_id is not None:
                self._skill_alias[from_id] = into_id
        elif event_type == "topic.split":
            for part in payload.get("into") or ():
                if not isinstance(part, Mapping):
                    continue
                tid = _opt_uuid(part.get("topic_id"))
                if tid is None:
                    continue
                for raw_sid in part.get("skill_ids") or ():
                    sid = _opt_uuid(raw_sid)
                    if sid is not None:
                        self._skill_topic[sid] = tid
        elif event_type == "topic.merged":
            from_id = _opt_uuid(payload.get("from_id"))
            into_id = _opt_uuid(payload.get("into_id"))
            if from_id is not None and into_id is not None:
                self._topic_alias[from_id] = into_id

    @staticmethod
    def _follow(start: UUID, aliases: Mapping[UUID, UUID]) -> UUID:
        seen = {start}
        current = start
        while current in aliases and aliases[current] not in seen:
            current = aliases[current]
            seen.add(current)
        return current

    def canonical_topic(self, topic_id: UUID) -> UUID:
        return self._follow(topic_id, self._topic_alias)

    def topic_of_skill(self, skill_id: UUID) -> UUID | None:
        live_skill = self._follow(skill_id, self._skill_alias)
        topic = self._skill_topic.get(live_skill)
        if topic is None:
            topic = self._skill_topic.get(skill_id)
        return None if topic is None else self.canonical_topic(topic)


def _matching_plan_steps(
    session_events: Sequence[SessionEvent], step_id: str, before_seq: int
) -> list[tuple[int, PlanStepRecord]]:
    """Matching plan steps from `turn.committed`, prioritized around `before_seq`."""
    before: list[tuple[int, PlanStepRecord]] = []
    after: list[tuple[int, PlanStepRecord]] = []
    for ev in session_events:
        if ev.type != "turn.committed":
            continue
        markup = ev.payload.get("markup")
        if not isinstance(markup, Mapping) or "plan_update" not in markup:
            continue
        try:
            update = PlanUpdateRecord.model_validate(markup["plan_update"])
        except ValidationError:
            continue
        candidates: list[PlanStepRecord] = []
        if update.current_step is not None and update.current_step.id == step_id:
            candidates.append(update.current_step)
        if update.next_steps:
            candidates.extend(s for s in update.next_steps if s.id == step_id)
        target = before if ev.seq <= before_seq else after
        for step in candidates:
            target.append((ev.seq, step))
    # Prefer the latest turn at/before the request, then earliest after.
    return [*reversed(before), *after]


def _skill_topic(
    skill_id: UUID,
    index: SkillTopicIndex,
    skill_topic_fallback: Mapping[UUID, UUID] | None,
) -> UUID | None:
    topic = index.topic_of_skill(skill_id)
    if topic is not None:
        return topic
    if skill_topic_fallback and (fallback := skill_topic_fallback.get(skill_id)) is not None:
        return index.canonical_topic(fallback)
    return None


def _touched_skill_ids(ev: SessionEvent) -> list[UUID]:
    ids: list[UUID] = []
    if ev.type == "turn.committed":
        markup = ev.payload.get("markup")
        if not isinstance(markup, Mapping):
            return ids
        for exp in markup.get("exposures") or ():
            if isinstance(exp, Mapping) and (sid := _opt_uuid(exp.get("skill_id"))) is not None:
                ids.append(sid)
        for task in markup.get("tasks") or ():
            if not isinstance(task, Mapping):
                continue
            for test in task.get("tests") or ():
                if (
                    isinstance(test, Mapping)
                    and (sid := _opt_uuid(test.get("skill_id"))) is not None
                ):
                    ids.append(sid)
    elif ev.type == "observation.extracted":
        for el in ev.payload.get("elements") or ():
            if isinstance(el, Mapping) and (sid := _opt_uuid(el.get("skill_id"))) is not None:
                ids.append(sid)
    return ids


def resolve_demo_topic(
    demo: ReadyDemo,
    session_events: Sequence[SessionEvent],
    index: SkillTopicIndex,
    *,
    skill_topic_fallback: Mapping[UUID, UUID] | None = None,
) -> UUID | None:
    """Resolve which topic a finished demo belongs to."""
    if demo.session_id is not None:
        if demo.step_id is not None:
            for _, step in _matching_plan_steps(session_events, demo.step_id, demo.seq):
                if step.landmark is None:
                    continue
                if step.landmark.kind == "topic":
                    return index.canonical_topic(step.landmark.id)
                resolved = _skill_topic(step.landmark.id, index, skill_topic_fallback)
                if resolved is not None:
                    return resolved

        for ev in reversed(session_events):
            if ev.seq >= demo.seq:
                continue
            if ev.type in _PLACEMENT_TYPES:
                tid = _opt_uuid(ev.payload.get("topic_id"))
                if tid is not None:
                    return index.canonical_topic(tid)

        if demo.param_topic_id is not None:
            return index.canonical_topic(demo.param_topic_id)
        if demo.param_skill_id is not None:
            resolved = _skill_topic(demo.param_skill_id, index, skill_topic_fallback)
            if resolved is not None:
                return resolved

        for ev in session_events:
            if ev.type == "session.started":
                tid = _opt_uuid(ev.payload.get("topic_id"))
                if tid is not None:
                    return index.canonical_topic(tid)

        for ev in reversed(session_events):
            if ev.seq >= demo.seq:
                continue
            for sid in _touched_skill_ids(ev):
                resolved = _skill_topic(sid, index, skill_topic_fallback)
                if resolved is not None:
                    return resolved
        return None

    if demo.param_topic_id is not None:
        return index.canonical_topic(demo.param_topic_id)
    if demo.param_skill_id is not None:
        return _skill_topic(demo.param_skill_id, index, skill_topic_fallback)
    return None


def format_demo_links_content(demos: Sequence[ReadyDemo]) -> str:
    """Markdown block content with clickable `/v1/viz/{request_id}` demo links."""
    lines = [
        f"- [Демо: {_sanitize_link_title(demo.title)}]({DEMO_LINK_PREFIX}{demo.request_id})"
        for demo in demos
    ]
    return f"{DEMO_BLOCK_HEADING}\n\n" + "\n".join(lines)


def _is_system_demo_block(block: Block) -> bool:
    return (
        block.owner == "system"
        and block.supplement_to is None
        and block.kind == "links"
        and DEMO_LINK_PREFIX in block.content
    )


def _as_system_block(block: Block) -> SystemBlock:
    return SystemBlock(
        block_id=block.block_id,
        kind=block.kind,
        content=block.content,
        sources=list(block.sources),
        skills=list(block.skills),
        generated_by=block.generated_by,
        basis=block.basis,
        unverified=block.unverified,
    )


def merge_demo_links_block(
    blocks: Sequence[SystemBlock],
    current: Sequence[Block],
    demos: Sequence[ReadyDemo],
) -> list[SystemBlock]:
    """Attach the deterministic demo `links` system block without clobbering learner edits."""
    existing_demo = next((b for b in current if _is_system_demo_block(b)), None)
    if not blocks:
        # The writer returned nothing: `write_system_blocks` drops the system blocks that no block
        # names, so the current system part is named again, unchanged, and only the demo block
        # is added or refreshed.
        blocks = [
            _as_system_block(b)
            for b in current
            if b.owner == "system" and b.supplement_to is None and not _is_system_demo_block(b)
        ]
    filtered = [
        b
        for b in blocks
        if existing_demo is None or b.block_id is None or b.block_id != existing_demo.block_id
    ]
    if not demos:
        return filtered
    # Keep block placement consistent across outbox replays: if all writer blocks are appended
    # (`block_id is None`), append the demo block too so `write_system_blocks` preserves order.
    reuse_id = existing_demo is not None and (
        not filtered or any(b.block_id is not None for b in filtered)
    )
    demo_block = SystemBlock(
        block_id=existing_demo.block_id if reuse_id and existing_demo is not None else None,
        kind="links",
        content=format_demo_links_content(demos),
        sources=[],
        skills=[],
        basis="system",
    )
    return [*filtered, demo_block]


def _build_ready_demo(
    row: Mapping[str, Any],
    events_by_session: Mapping[UUID, list[SessionEvent]],
    title_chars: int | None,
) -> ReadyDemo | None:
    payload = row["payload"]
    if not isinstance(payload, Mapping):
        return None
    request_id = _opt_uuid(payload.get("request_id"))
    if request_id is None:
        return None
    params = payload.get("params")
    params_map: Mapping[str, Any] = params if isinstance(params, Mapping) else {}
    raw_step = params_map.get("step_id")
    step_id = raw_step if isinstance(raw_step, str) and raw_step else None
    seq = int(row["seq"])
    session_id = _opt_uuid(row["session_id"])
    title = _clean_text(params_map.get("title"), title_chars)
    if not title and session_id is not None and step_id is not None:
        for _, step in _matching_plan_steps(events_by_session.get(session_id, ()), step_id, seq):
            if step_title := _clean_text(step.text, title_chars):
                title = step_title
                break
    if not title:
        title = _clean_text(params_map.get("description"), title_chars)
    if not title:
        title = step_id or "Демо"
    return ReadyDemo(
        request_id=request_id,
        seq=seq,
        session_id=session_id,
        domain_id=_opt_uuid(row["domain_id"]),
        step_id=step_id,
        title=title,
        param_topic_id=_opt_uuid(params_map.get("topic_id")),
        param_skill_id=_opt_uuid(params_map.get("skill_id")),
    )


async def _load_demo_context(
    db: Db, learner_id: UUID, title_chars: int | None
) -> tuple[list[ReadyDemo], dict[UUID, list[SessionEvent]], SkillTopicIndex]:
    viz_rows = await db.fetch_all(_READY_VIZ, {"learner_id": learner_id})
    if not viz_rows:
        return [], {}, SkillTopicIndex()

    session_ids = sorted(
        {sid for r in viz_rows if (sid := _opt_uuid(r["session_id"])) is not None},
        key=str,
    )
    events_by_session: dict[UUID, list[SessionEvent]] = {}
    if session_ids:
        ev_rows = await db.fetch_all(
            _SESSION_EVENTS,
            {
                "learner_id": learner_id,
                "session_ids": session_ids,
                "types": list(_SESSION_EVENT_TYPES),
            },
        )
        for ev in ev_rows:
            sid = _opt_uuid(ev["session_id"])
            if sid is None:
                continue
            payload = ev["payload"]
            if isinstance(payload, Mapping):
                events_by_session.setdefault(sid, []).append(
                    SessionEvent(seq=int(ev["seq"]), type=str(ev["type"]), payload=payload)
                )

    index = SkillTopicIndex()
    struct_rows = await db.fetch_all(
        _STRUCTURE_EVENTS, {"learner_id": learner_id, "types": list(_STRUCTURE_TYPES)}
    )
    for row in struct_rows:
        if isinstance(row["payload"], Mapping):
            index.apply(str(row["type"]), row["payload"])

    demos: list[ReadyDemo] = []
    seen: set[UUID] = set()
    for row in viz_rows:
        demo = _build_ready_demo(row, events_by_session, title_chars)
        if demo is None or demo.request_id in seen:
            continue
        seen.add(demo.request_id)
        demos.append(demo)
    return demos, events_by_session, index


async def topic_ready_demos(
    db: Db,
    *,
    learner_id: UUID,
    topic_id: UUID,
    skill_topic_fallback: Mapping[UUID, UUID] | None = None,
    title_chars: int | None = None,
) -> list[ReadyDemo]:
    """All finished `viz` demos of the learner that map to `topic_id`, in chronological order."""
    demos, events_by_session, index = await _load_demo_context(db, learner_id, title_chars)
    if not demos:
        return []
    target = index.canonical_topic(topic_id)
    matched: list[ReadyDemo] = []
    for demo in demos:
        resolved = resolve_demo_topic(
            demo,
            events_by_session.get(demo.session_id, ()) if demo.session_id is not None else (),
            index,
            skill_topic_fallback=skill_topic_fallback,
        )
        if resolved is not None and resolved == target:
            matched.append(demo)
    return matched


async def _is_domain_all(db: Db, learner_id: UUID, hub_id: UUID, domain_id: UUID) -> bool:
    if (
        await db.fetch_one(_IS_DOMAIN_ALL_HUB, {"learner_id": learner_id, "hub_id": str(hub_id)})
        is not None
    ):
        return True
    rows = await db.fetch_all(
        _DOMAIN_CREATED, {"learner_id": learner_id, "domain_id": str(domain_id)}
    )
    return any(UUID(deterministic_id(learner_id, int(row["seq"]), 0)) == hub_id for row in rows)


def _session_hub_and_domain(
    events: Sequence[SessionEvent],
) -> tuple[UUID | None, UUID | None]:
    for ev in events:
        if ev.type == "session.started":
            return _opt_uuid(ev.payload.get("hub_id")), _opt_uuid(ev.payload.get("domain_id"))
    return None, None


async def hub_ready_demos(
    db: Db,
    *,
    learner_id: UUID,
    hub_id: UUID,
    domain_id: UUID,
    hub_topic_ids: Iterable[UUID] = (),
    is_domain_all: bool | None = None,
    session_id: UUID | None = None,
    skill_topic_fallback: Mapping[UUID, UUID] | None = None,
    title_chars: int | None = None,
) -> list[ReadyDemo]:
    """All finished `viz` demos belonging to `hub_id`: topics, hub sessions, and `session_id`."""
    demos, events_by_session, index = await _load_demo_context(db, learner_id, title_chars)
    if not demos:
        return []
    domain_all = (
        await _is_domain_all(db, learner_id, hub_id, domain_id)
        if is_domain_all is None
        else is_domain_all
    )
    canonical_topics = {index.canonical_topic(t) for t in hub_topic_ids}
    req_rows = await db.fetch_all(
        _HUB_CHEATSHEET_SESSIONS, {"learner_id": learner_id, "hub_id": str(hub_id)}
    )
    hub_sessions = {sid for r in req_rows if (sid := _opt_uuid(r["session_id"])) is not None}
    if session_id is not None:
        hub_sessions.add(session_id)

    matched: list[ReadyDemo] = []
    for demo in demos:
        sess_events = (
            events_by_session.get(demo.session_id, ()) if demo.session_id is not None else ()
        )
        sess_hub, sess_domain = _session_hub_and_domain(sess_events)
        if demo.session_id is not None and (demo.session_id in hub_sessions or sess_hub == hub_id):
            matched.append(demo)
            continue
        if domain_all and (demo.domain_id == domain_id or sess_domain == domain_id):
            matched.append(demo)
            continue
        resolved = resolve_demo_topic(
            demo, sess_events, index, skill_topic_fallback=skill_topic_fallback
        )
        if resolved is not None and resolved in canonical_topics:
            matched.append(demo)
    return matched
