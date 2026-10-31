"""Session lifecycle: start, active, get, heartbeat, finish and the inactivity sweep (ТЗ §2.2)."""

from __future__ import annotations

import contextlib
from typing import Any
from uuid import UUID, uuid4

import neo4j
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from quack.api.app import AppResources
from quack.api.errors import ApiError
from quack.config import Settings
from quack.db.neo4j import cypher
from quack.db.redis import create_redis, presence_key
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.graph.neo4j_store import read_applied_seq
from quack.graph.slice import SliceDeps, read_consistent_slice
from quack.jobs import kinds
from quack.jobs.queue import ClaimedJob, enqueue_job
from quack.jobs.runner import JobContext, register_job_handler
from quack.session.context import TURN_EVENT_TYPES, last_checkpoint
from quack.session.schemas import (
    ActiveSession,
    CheckpointView,
    FinishResponse,
    FinishSessionRequest,
    HeartbeatResponse,
    SessionDetail,
    SessionStarted,
    StartSessionRequest,
    TurnView,
)

MIN_PLAN_STEPS = 3
MAX_PLAN_STEPS = 5
_PLAN_STATES = ("available", "in_progress", "rusty")
_SESSION_COLUMNS = (
    "id, domain_id, topic_id, mode, status, started_at, last_activity_at, "
    "last_turn_index, last_checkpoint_turn"
)

_RELEASE_PRESENCE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

_TOPIC_QUERY = """
MATCH (t:Topic) WHERE t.learnerId = $l AND t.domainId = $d
  AND ($t IS NULL OR t.id = $t) AND NOT t.status IN ['archived', 'merged', 'skipped']
OPTIONAL MATCH (s:Skill)-[:PART_OF]->(t) WHERE NOT s.status IN ['archived', 'merged']
WITH t, s ORDER BY s.id
WITH t, collect(CASE WHEN s IS NULL THEN NULL ELSE {id: s.id, name: s.name} END) AS skills
RETURN t.id AS id, t.name AS name, skills
ORDER BY CASE WHEN t.displayState IN $states THEN 0 ELSE 1 END, t.name, t.id LIMIT 1
"""


def _plan_from_skills(topic_name: str, skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """3-5 steps: learn each skill (at most 3), then practice and summary."""
    plan: list[dict[str, Any]] = [
        {"kind": "learn", "skill_id": s["id"], "title": s["name"]}
        for s in skills[: MAX_PLAN_STEPS - 2]
    ]
    if not plan:
        plan.append({"kind": "learn", "title": topic_name or "Warm-up"})
    plan.append({"kind": "practice", "title": f"Practice {topic_name}".strip()})
    plan.append({"kind": "summary", "title": "Summarize what you learned"})
    if len(plan) < MIN_PLAN_STEPS:
        plan.insert(1, {"kind": "learn", "title": f"Explore {topic_name}".strip()})
    return [{"step": i, **step} for i, step in enumerate(plan)]


async def _resolve_topic_and_plan(
    resources: AppResources,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    topic_id: UUID | None,
    explicit_plan: list[dict[str, Any]] | None,
) -> tuple[UUID | None, list[dict[str, Any]]]:
    """Resolve the session topic and its plan from a consistent Neo4j slice (РТЗ S-3).

    Without `topic_id` the first topic of the domain with an open `displayState` wins, else
    any active topic of the domain. `explicit_plan` is kept as is.
    """

    async def load_topic() -> Any:
        async with resources.neo4j.session(default_access_mode=neo4j.READ_ACCESS) as session:
            result = await session.run(
                cypher(_TOPIC_QUERY),
                {
                    "l": str(learner_id),
                    "d": str(domain_id),
                    "t": str(topic_id) if topic_id else None,
                    "states": list(_PLAN_STATES),
                },
            )
            return await result.single()

    async def applied_seq_reader(learner: UUID) -> int:
        return await read_applied_seq(resources.neo4j, str(learner))

    deps = SliceDeps(
        session_factory=resources.session_factory,
        redis=resources.redis,
        applied_seq_reader=applied_seq_reader,
        snapshot_read_retries=settings.snapshot_read_retries,
    )
    graph_slice = await read_consistent_slice(
        learner_id, None, load_topic, settings.projector_lag_timeout_ms, deps=deps
    )
    record = graph_slice.data
    resolved = topic_id
    topic_name = ""
    skills: list[dict[str, Any]] = []
    if record is not None:
        resolved = UUID(str(record["id"]))
        topic_name = str(record["name"] or "")
        skills = [s for s in record["skills"] if s is not None]
    if explicit_plan is not None:
        return resolved, explicit_plan
    return resolved, _plan_from_skills(topic_name, skills)


def _idempotency_conflict() -> ApiError:
    return ApiError(
        "IDEMPOTENCY_CONFLICT",
        "idempotency_key was already used with different parameters",
        status_code=409,
    )


async def _started_by_key(
    session: AsyncSession, learner_id: UUID, key: str, body: StartSessionRequest
) -> SessionStarted | None:
    row = (
        await session.execute(
            text(
                "SELECT seq, payload FROM core.events WHERE learner_id = :l "
                "AND idempotency_key = :k AND type = 'session.started'"
            ),
            {"l": learner_id, "k": key},
        )
    ).one_or_none()
    if row is None:
        return None
    p = row.payload
    stored_topic_id = UUID(p["topic_id"]) if p["topic_id"] else None
    if (
        UUID(p["domain_id"]) != body.domain_id
        or p["mode"] != body.mode
        or p.get("goal") != body.goal
        or (body.topic_id is not None and stored_topic_id != body.topic_id)
    ):
        raise _idempotency_conflict()
    session_id = UUID(p["session_id"])
    status = (
        await session.execute(
            text("SELECT status FROM core.sessions WHERE id = :id AND learner_id = :l"),
            {"id": session_id, "l": learner_id},
        )
    ).scalar_one_or_none()
    return SessionStarted(
        session_id=session_id,
        domain_id=UUID(p["domain_id"]),
        topic_id=stored_topic_id,
        mode=p["mode"],
        status=status or "active",
        plan=p["plan"],
        start_seq=int(row.seq),
    )


async def _set_presence(
    resources: AppResources, settings: Settings, learner_id: UUID, session_id: UUID
) -> None:
    # Presence is advisory; the session itself lives in Postgres. No Redis → no presence
    # (§4.2, D-C-005).
    if resources.redis is None:
        return
    with contextlib.suppress(RedisError, OSError):
        await resources.redis.set(
            presence_key(learner_id), str(session_id), ex=settings.heartbeat_ttl_sec
        )


async def _release_presence(redis: Redis | None, learner_id: UUID, session_id: UUID) -> None:
    """Delete the presence key only if it still holds `session_id` (atomic compare-and-delete)."""
    if redis is None:
        return
    with contextlib.suppress(RedisError, OSError):
        await redis.eval(  # type: ignore[misc]
            _RELEASE_PRESENCE_LUA, 1, presence_key(learner_id), str(session_id)
        )


async def start_session(
    resources: AppResources, settings: Settings, learner_id: UUID, body: StartSessionRequest
) -> SessionStarted:
    event_key = f"session.start:{body.idempotency_key}"
    async with resources.session_factory() as session:
        existing = await _started_by_key(session, learner_id, event_key, body)
    if existing is not None:
        if existing.status == "active":
            await _set_presence(resources, settings, learner_id, existing.session_id)
        return existing

    topic_id, plan = await _resolve_topic_and_plan(
        resources, settings, learner_id, body.domain_id, body.topic_id, body.plan
    )
    session_id = uuid4()
    async with resources.session_factory() as session, session.begin():
        # Serialize with other writers of the learner, then re-check the key.
        await session.execute(
            text(
                "INSERT INTO core.learner_seq (learner_id, last_seq, updated_at) "
                "VALUES (:l, 0, now()) ON CONFLICT (learner_id) DO NOTHING"
            ),
            {"l": learner_id},
        )
        await session.execute(
            text("SELECT 1 FROM core.learner_seq WHERE learner_id = :l FOR UPDATE"),
            {"l": learner_id},
        )
        existing = await _started_by_key(session, learner_id, event_key, body)
        if existing is not None:
            return existing
        old = (
            await session.execute(
                text(
                    "SELECT id, domain_id FROM core.sessions "
                    "WHERE learner_id = :l AND status = 'active'"
                ),
                {"l": learner_id},
            )
        ).one_or_none()
        events: list[NewEvent] = []
        if old is not None:
            events.append(
                NewEvent(
                    type="session.abandoned",
                    payload={
                        "session_id": str(old.id),
                        "domain_id": str(old.domain_id),
                        "reason": "superseded_by_new_session",
                    },
                    actor="system",
                    idempotency_key=f"session.abandon:{body.idempotency_key}",
                )
            )
        events.append(
            NewEvent(
                type="session.started",
                payload={
                    "session_id": str(session_id),
                    "domain_id": str(body.domain_id),
                    "topic_id": str(topic_id) if topic_id else None,
                    "mode": body.mode,
                    "goal": body.goal,
                    "plan": plan,
                },
                actor="learner",
                idempotency_key=event_key,
            )
        )
        stored = await append_events(session, learner_id, events, redis=resources.redis)
        start_seq = stored[-1].seq
    await _set_presence(resources, settings, learner_id, session_id)
    return SessionStarted(
        session_id=session_id,
        domain_id=body.domain_id,
        topic_id=topic_id,
        mode=body.mode,
        status="active",
        plan=plan,
        start_seq=start_seq,
    )


def _active_from_row(row: Any) -> ActiveSession:
    return ActiveSession(
        session_id=row.id,
        domain_id=row.domain_id,
        topic_id=row.topic_id,
        mode=str(row.mode),
        status=str(row.status),
        started_at=row.started_at,
        last_activity_at=row.last_activity_at,
        last_turn_index=int(row.last_turn_index),
        last_checkpoint_turn=int(row.last_checkpoint_turn),
    )


async def get_active_session(resources: AppResources, learner_id: UUID) -> ActiveSession | None:
    async with resources.session_factory() as session:
        row = (
            await session.execute(
                text(
                    f"SELECT {_SESSION_COLUMNS} FROM core.sessions "
                    "WHERE learner_id = :l AND status = 'active'"
                ),
                {"l": learner_id},
            )
        ).one_or_none()
    return _active_from_row(row) if row is not None else None


async def get_session(resources: AppResources, learner_id: UUID, session_id: UUID) -> SessionDetail:
    async with resources.session_factory() as session:
        row = (
            await session.execute(
                text(
                    f"SELECT {_SESSION_COLUMNS} FROM core.sessions "
                    "WHERE id = :id AND learner_id = :l"
                ),
                {"id": session_id, "l": learner_id},
            )
        ).one_or_none()
        if row is None:
            raise ApiError("NOT_FOUND", "session not found", status_code=404)
        turn_rows = (
            await session.execute(
                text(
                    "SELECT type, seq, payload FROM core.events WHERE learner_id = :l "
                    "AND session_id = :s AND type = ANY(:types) ORDER BY seq"
                ),
                {"l": learner_id, "s": session_id, "types": list(TURN_EVENT_TYPES)},
            )
        ).all()
        checkpoint = await last_checkpoint(session, learner_id, session_id)
    turns: list[TurnView] = []
    for t in turn_rows:
        p = t.payload
        teacher = t.type == "turn.teacher_replied"
        turns.append(
            TurnView(
                turn_index=int(p["turn_index"]),
                turn_id=UUID(p["turn_id"]),
                role="teacher" if teacher else "student",
                text=str(p["text"]),
                seq=int(t.seq),
                related_task_id=UUID(p["related_task_id"]) if p.get("related_task_id") else None,
                pedagogical_move=p.get("pedagogical_move") if teacher else None,
                degraded_mode=p.get("degraded_mode") if teacher else None,
            )
        )
    view = None
    if checkpoint is not None:
        c = checkpoint.payload
        view = CheckpointView(
            up_to_turn_index=int(c["up_to_turn_index"]),
            rolling_summary=str(c["rolling_summary"]),
            open_threads=list(c["open_threads"]),
            key_observations=list(c["key_observations"]),
        )
    base = _active_from_row(row)
    return SessionDetail(**base.model_dump(), turns=turns, checkpoint=view)


async def heartbeat(
    resources: AppResources, settings: Settings, learner_id: UUID, session_id: UUID
) -> HeartbeatResponse:
    async with resources.session_factory() as session, session.begin():
        updated = (
            await session.execute(
                text(
                    "UPDATE core.sessions SET last_activity_at = now() "
                    "WHERE id = :id AND learner_id = :l AND status = 'active' RETURNING id"
                ),
                {"id": session_id, "l": learner_id},
            )
        ).first()
    if updated is None:
        raise ApiError("NOT_FOUND", "session not found", status_code=404)
    await _set_presence(resources, settings, learner_id, session_id)
    return HeartbeatResponse(status="ok", ttl_sec=settings.heartbeat_ttl_sec)


async def finish_session(
    resources: AppResources, learner_id: UUID, session_id: UUID, body: FinishSessionRequest
) -> FinishResponse:
    async with resources.session_factory() as session, session.begin():
        row = (
            await session.execute(
                text(
                    "SELECT id, domain_id, topic_id, status, tokens_used, "
                    "EXTRACT(EPOCH FROM (now() - started_at)) AS duration_sec "
                    "FROM core.sessions WHERE id = :id AND learner_id = :l FOR UPDATE"
                ),
                {"id": session_id, "l": learner_id},
            )
        ).one_or_none()
        if row is None:
            raise ApiError("NOT_FOUND", "session not found", status_code=404)
        if row.status == "completed":
            completed_row = (
                await session.execute(
                    text(
                        "SELECT seq, payload FROM core.events WHERE learner_id = :l "
                        "AND session_id = :s AND type = 'session.completed' "
                        "ORDER BY seq DESC LIMIT 1"
                    ),
                    {"l": learner_id, "s": session_id},
                )
            ).one_or_none()
            if completed_row is None:
                raise ApiError("NOT_FOUND", "session not found", status_code=404)
            return FinishResponse(
                session_id=session_id,
                status="completed",
                summary_stats=dict(completed_row.payload["summary_stats"]),
                end_seq=int(completed_row.seq),
            )
        if row.status != "active":
            raise ApiError("NOT_FOUND", "session not found", status_code=404)

        turns_count = int(
            (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM core.events WHERE learner_id = :l "
                        "AND session_id = :s AND type = 'turn.student_replied'"
                    ),
                    {"l": learner_id, "s": session_id},
                )
            ).scalar_one()
        )
        summary_stats: dict[str, Any] = {
            "turns_count": turns_count,
            "tokens_used": int(row.tokens_used),
            "duration_sec": max(0, round(float(row.duration_sec))),
        }
        domain_id: UUID = row.domain_id
        topic_id: UUID | None = row.topic_id
        stored = await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="session.completed",
                    payload={
                        "session_id": str(session_id),
                        "domain_id": str(domain_id),
                        "summary_stats": summary_stats,
                    },
                    actor="learner",
                    idempotency_key=f"session.finish:{body.idempotency_key}",
                )
            ],
            redis=resources.redis,
        )
        end_seq = stored[-1].seq
        await enqueue_job(
            session,
            kinds.session_finish_artifacts(
                learner_id,
                session_id,
                {
                    "domain_id": str(domain_id),
                    "topic_id": str(topic_id) if topic_id else None,
                },
            ),
        )
        await enqueue_job(
            session,
            kinds.graph_curate(
                learner_id,
                domain_id,
                {"domain_id": str(domain_id)},
            ),
        )

    await _release_presence(resources.redis, learner_id, session_id)
    return FinishResponse(
        session_id=session_id,
        status="completed",
        summary_stats=summary_stats,
        end_seq=end_seq,
    )


@register_job_handler("session.sweep_inactive")
async def sweep_inactive_sessions(job: ClaimedJob, ctx: JobContext) -> None:
    _ = job
    timeout_min = ctx.settings.session_inactivity_timeout_min
    async with ctx.session_factory() as session, session.begin():
        stale_rows = (
            await session.execute(
                text(
                    "SELECT id, learner_id, domain_id FROM core.sessions "
                    "WHERE status = 'active' "
                    "AND last_activity_at < now() - make_interval(mins => :mins) "
                    "ORDER BY learner_id, id"
                ),
                {"mins": timeout_min},
            )
        ).all()
        abandoned: list[tuple[UUID, UUID]] = []
        for row in stale_rows:
            abandoned.append((row.learner_id, row.id))
            await append_events(
                session,
                row.learner_id,
                [
                    NewEvent(
                        type="session.abandoned",
                        payload={
                            "session_id": str(row.id),
                            "domain_id": str(row.domain_id),
                            "reason": "inactivity_timeout",
                        },
                        actor="system",
                        idempotency_key=f"sweep:{row.id}",
                    )
                ],
                publish_wakeup=False,
            )

    if not abandoned:
        return
    redis = create_redis(ctx.settings)
    try:
        for learner_id, session_id in abandoned:
            await _release_presence(redis, learner_id, session_id)
    finally:
        with contextlib.suppress(RedisError, OSError):
            await redis.aclose()
