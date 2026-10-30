"""Teacher context assembly (Т-§5.3): graph slice + session overlay + checkpoint + active window."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from quack.api.app import AppResources
from quack.api.errors import ApiError
from quack.config import Settings
from quack.events.models import StoredEvent
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.memory import InMemoryGraph, prop_str
from quack.graph.neo4j_store import Neo4jGraphStore, read_applied_seq
from quack.graph.overlay import SessionOverlay, build_session_overlay
from quack.graph.slice import SliceDeps, read_consistent_slice

TURN_EVENT_TYPES = ("turn.student_replied", "turn.teacher_replied")


@dataclass(frozen=True)
class TurnContext:
    topic_id: UUID | None
    topic_name: str
    mode: str
    lag_degraded: bool
    overlay: SessionOverlay
    rolling_summary: str
    open_threads: list[str]
    recent_turns: list[dict[str, Any]]
    skills_slice: list[dict[str, Any]] = field(default_factory=list)
    misconceptions: list[dict[str, Any]] = field(default_factory=list)
    domain_id: UUID | None = None
    applied_seq: int = 0
    last_checkpoint_turn: int = -1
    key_observations: list[str] = field(default_factory=list)
    checkpoint: StoredEvent | None = None


@dataclass(frozen=True)
class _SessionRow:
    domain_id: UUID
    topic_id: UUID | None
    mode: str


async def _load_session_row(
    session: AsyncSession, learner_id: UUID, session_id: UUID
) -> _SessionRow:
    row = (
        await session.execute(
            text(
                "SELECT domain_id, topic_id, mode FROM core.sessions "
                "WHERE id = :id AND learner_id = :l"
            ),
            {"id": session_id, "l": learner_id},
        )
    ).one_or_none()
    if row is None:
        raise ApiError("NOT_FOUND", "session not found", status_code=404)
    return _SessionRow(domain_id=row.domain_id, topic_id=row.topic_id, mode=str(row.mode))


async def last_checkpoint(
    session: AsyncSession, learner_id: UUID, session_id: UUID
) -> StoredEvent | None:
    """The newest `session.checkpointed` of the session."""
    result = await session.execute(
        text(
            f"SELECT {EVENT_COLUMNS} FROM core.events WHERE learner_id = :l "
            "AND session_id = :s AND type = 'session.checkpointed' ORDER BY seq DESC LIMIT 1"
        ),
        {"l": learner_id, "s": session_id},
    )
    row = result.first()
    return row_to_event(row) if row is not None else None


async def read_active_window(
    session: AsyncSession, learner_id: UUID, session_id: UUID, after_turn: int, limit: int
) -> list[StoredEvent]:
    """The last `limit` replies of the session with `turn_index > after_turn`, oldest first."""
    result = await session.execute(
        text(
            f"SELECT {EVENT_COLUMNS} FROM core.events WHERE learner_id = :l "
            "AND session_id = :s AND type = ANY(:types) "
            "AND (payload->>'turn_index')::int > :after ORDER BY seq DESC LIMIT :limit"
        ),
        {
            "l": learner_id,
            "s": session_id,
            "types": list(TURN_EVENT_TYPES),
            "after": after_turn,
            "limit": max(0, limit),
        },
    )
    return [row_to_event(row) for row in reversed(result.all())]


def _turn_view(event: StoredEvent) -> dict[str, Any]:
    data = event.payload
    role = "student" if event.type == "turn.student_replied" else "teacher"
    view: dict[str, Any] = {
        "role": role,
        "turn_index": int(data["turn_index"]),
        "turn_id": str(data["turn_id"]),
        "text": str(data["text"]),
        "seq": event.seq,
    }
    if role == "teacher":
        view["pedagogical_move"] = data.get("pedagogical_move")
    elif data.get("related_task_id") is not None:
        view["related_task_id"] = str(data["related_task_id"])
    return view


def _topic_slice(
    graph: InMemoryGraph, topic_id: UUID | None
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    if topic_id is None:
        return "", [], []
    tid = str(topic_id)
    topic_name = prop_str(graph.get("Topic", tid), "name")
    skills: list[dict[str, Any]] = []
    misconceptions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in sorted(graph.in_rels("Topic", tid, "PART_OF")):
        if key[0] != "Skill":
            continue
        skill_id = key[1]
        props = graph.get("Skill", skill_id) or {}
        skills.append({"skill_id": skill_id, **props})
        for mkey in sorted(graph.in_rels("Skill", skill_id, "ABOUT_SKILL")):
            if mkey[0] != "Misconception" or mkey[1] in seen:
                continue
            seen.add(mkey[1])
            mprops = graph.get("Misconception", mkey[1]) or {}
            misconceptions.append({"misconception_id": mkey[1], "skill_id": skill_id, **mprops})
    return topic_name, skills, misconceptions


async def build_turn_context(
    resources: AppResources, settings: Settings, learner_id: UUID, session_id: UUID
) -> TurnContext:
    """Consistent slice (<= projector_lag_timeout_ms) + overlay + last checkpoint + <= 12 turns."""
    async with resources.session_factory() as session:
        row = await _load_session_row(session, learner_id, session_id)

    store = Neo4jGraphStore(resources.neo4j, str(learner_id))

    async def applied_seq_reader(learner: UUID) -> int:
        return await read_applied_seq(resources.neo4j, str(learner))

    deps = SliceDeps(
        session_factory=resources.session_factory,
        redis=resources.redis,
        applied_seq_reader=applied_seq_reader,
        snapshot_read_retries=settings.snapshot_read_retries,
    )
    graph_slice = await read_consistent_slice(
        learner_id, None, store.dump, settings.projector_lag_timeout_ms, deps=deps
    )

    async with resources.session_factory() as session:
        overlay = await build_session_overlay(
            session,
            learner_id,
            session_id,
            graph_slice.applied_seq,
            graph_slice.data,
            settings.fold,
        )
        checkpoint = await last_checkpoint(session, learner_id, session_id)
        cp = checkpoint.payload if checkpoint is not None else {}
        last_cp_turn = int(cp.get("up_to_turn_index", -1))
        window = await read_active_window(
            session, learner_id, session_id, last_cp_turn, settings.active_window_turns
        )

    topic_name, skills, misconceptions = _topic_slice(overlay.graph, row.topic_id)
    return TurnContext(
        topic_id=row.topic_id,
        topic_name=topic_name,
        mode=row.mode,
        lag_degraded=graph_slice.lag_degraded,
        overlay=overlay,
        rolling_summary=str(cp.get("rolling_summary", "")),
        open_threads=[str(t) for t in cp.get("open_threads", [])],
        recent_turns=[_turn_view(e) for e in window],
        skills_slice=skills,
        misconceptions=misconceptions,
        domain_id=row.domain_id,
        applied_seq=graph_slice.applied_seq,
        last_checkpoint_turn=last_cp_turn,
        key_observations=[str(o) for o in cp.get("key_observations", [])],
        checkpoint=checkpoint,
    )
