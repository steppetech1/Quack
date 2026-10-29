"""Synchronous relational projections updated inside append_events (spec §8.1 step 5).

core.domains, core.sessions, core.artifacts and core.task_bank never diverge from
core.events because they are written in the same transaction as the event.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from quack.events.models import StoredEvent

type Handler = Callable[[AsyncSession, StoredEvent], Awaitable[None]]


def _json(value: Any) -> str:
    return orjson.dumps(value).decode()


async def _exec(session: AsyncSession, sql: str, **params: Any) -> None:
    await session.execute(text(sql), params)


# --- core.domains ---


async def _domain_created(session: AsyncSession, e: StoredEvent) -> None:
    p = e.payload
    await _exec(
        session,
        "INSERT INTO core.domains (id, learner_id, name, slug, status, has_materials, "
        "active_structure_version, created_seq, updated_seq, created_at, updated_at) "
        "VALUES (:id, :l, :name, :slug, 'onboarding', :hm, 0, :seq, :seq, :at, :at)",
        id=p["domain_id"],
        l=e.learner_id,
        name=p["name"],
        slug=p["slug"],
        hm=p["has_materials"],
        seq=e.seq,
        at=e.occurred_at,
    )


async def _domain_update(session: AsyncSession, e: StoredEvent, sets: str, **params: Any) -> None:
    await _exec(
        session,
        f"UPDATE core.domains SET {sets}, updated_seq = :seq, updated_at = :at "
        "WHERE id = :id AND learner_id = :l",
        id=e.payload["domain_id"],
        l=e.learner_id,
        seq=e.seq,
        at=e.occurred_at,
        **params,
    )


async def _domain_archived(session: AsyncSession, e: StoredEvent) -> None:
    await _domain_update(session, e, "status = 'archived'")


async def _interview_completed(session: AsyncSession, e: StoredEvent) -> None:
    await _domain_update(session, e, "status = 'structuring'")


async def _structure_confirmed(session: AsyncSession, e: StoredEvent) -> None:
    await _domain_update(
        session,
        e,
        "status = 'active', active_structure_version = :v",
        v=e.payload["structure_version"],
    )


async def _structure_edited(session: AsyncSession, e: StoredEvent) -> None:
    await _domain_update(
        session, e, "active_structure_version = :v", v=e.payload["structure_version"]
    )


# --- core.sessions ---


async def _session_started(session: AsyncSession, e: StoredEvent) -> None:
    p = e.payload
    await _exec(
        session,
        "INSERT INTO core.sessions (id, learner_id, domain_id, topic_id, mode, status, goal, "
        "plan, started_at, last_activity_at, start_seq) VALUES (:id, :l, :d, :t, :mode, "
        "'active', :goal, CAST(:plan AS JSONB), :at, :at, :seq)",
        id=p["session_id"],
        l=e.learner_id,
        d=p["domain_id"],
        t=p["topic_id"],
        mode=p["mode"],
        goal=p["goal"],
        plan=_json(p["plan"]),
        at=e.occurred_at,
        seq=e.seq,
    )


async def _session_ended(session: AsyncSession, e: StoredEvent, status: str) -> None:
    await _exec(
        session,
        "UPDATE core.sessions SET status = :status, ended_at = :at, end_seq = :seq "
        "WHERE id = :id AND learner_id = :l",
        status=status,
        at=e.occurred_at,
        seq=e.seq,
        id=e.payload["session_id"],
        l=e.learner_id,
    )


async def _session_completed(session: AsyncSession, e: StoredEvent) -> None:
    await _session_ended(session, e, "completed")


async def _session_abandoned(session: AsyncSession, e: StoredEvent) -> None:
    await _session_ended(session, e, "abandoned")


async def _session_checkpointed(session: AsyncSession, e: StoredEvent) -> None:
    await _exec(
        session,
        "UPDATE core.sessions SET last_checkpoint_turn = GREATEST(last_checkpoint_turn, :t) "
        "WHERE id = :id AND learner_id = :l",
        t=e.payload["up_to_turn_index"],
        id=e.payload["session_id"],
        l=e.learner_id,
    )


async def _turn_replied(session: AsyncSession, e: StoredEvent) -> None:
    await _exec(
        session,
        "UPDATE core.sessions SET last_turn_index = GREATEST(last_turn_index, :t), "
        "last_activity_at = GREATEST(last_activity_at, :at) WHERE id = :id AND learner_id = :l",
        t=e.payload["turn_index"],
        at=e.occurred_at,
        id=e.payload["session_id"],
        l=e.learner_id,
    )


async def _task_submitted(session: AsyncSession, e: StoredEvent) -> None:
    await _exec(
        session,
        "UPDATE core.sessions SET last_activity_at = GREATEST(last_activity_at, :at) "
        "WHERE id = :id AND learner_id = :l",
        at=e.occurred_at,
        id=e.payload["session_id"],
        l=e.learner_id,
    )


# --- core.task_bank ---


async def _task_generated(session: AsyncSession, e: StoredEvent) -> None:
    p = e.payload
    await _exec(
        session,
        "INSERT INTO core.task_bank (id, learner_id, domain_id, topic_id, family_id, difficulty, "
        "kind, prompt_text, reference_solution, target_skills, target_misconceptions, "
        "created_seq, created_at) VALUES (:id, :l, :d, :t, :f, :diff, :kind, :prompt, "
        "CAST(:ref AS JSONB), CAST(:skills AS JSONB), CAST(:miscs AS JSONB), :seq, :at) "
        "ON CONFLICT (id) DO NOTHING",
        id=p["task_id"],
        l=e.learner_id,
        d=p["domain_id"],
        t=p["topic_id"],
        f=p["family_id"],
        diff=p["difficulty"],
        kind=p["kind"],
        prompt=p["prompt_text"],
        ref=_json(p["reference_solution"]),
        skills=_json(p["target_skills"]),
        miscs=_json(p["target_misconceptions"]),
        seq=e.seq,
        at=e.occurred_at,
    )


# --- core.artifacts ---


async def _insert_artifact(
    session: AsyncSession,
    e: StoredEvent,
    *,
    kind: str,
    domain_id: Any,
    topic_id: Any,
    session_id: Any,
    version: int,
) -> None:
    p = e.payload
    await _exec(
        session,
        "INSERT INTO core.artifacts (id, learner_id, domain_id, topic_id, session_id, kind, title, "
        "content_md, version, source_seq, created_at, updated_at) VALUES (:id, :l, :d, :t, :s, "
        ":kind, :title, :content, :v, :seq, :at, :at) "
        "ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, content_md = EXCLUDED.content_md, "
        "version = EXCLUDED.version, source_seq = EXCLUDED.source_seq, "
        "updated_at = EXCLUDED.updated_at, is_deleted = FALSE",
        id=p["artifact_id"],
        l=e.learner_id,
        d=domain_id,
        t=topic_id,
        s=session_id,
        kind=kind,
        title=p["title"],
        content=p["text"],
        v=version,
        seq=e.seq,
        at=e.occurred_at,
    )


async def _digest_created(session: AsyncSession, e: StoredEvent) -> None:
    p = e.payload
    await _insert_artifact(
        session,
        e,
        kind="digest",
        domain_id=p["domain_id"],
        topic_id=p["topic_id"],
        session_id=p["session_id"],
        version=1,
    )


async def _note_created(session: AsyncSession, e: StoredEvent) -> None:
    p = e.payload
    await _insert_artifact(
        session,
        e,
        kind="note",
        domain_id=p["domain_id"],
        topic_id=p["topic_id"],
        session_id=p["session_id"],
        version=1,
    )


async def _note_updated(session: AsyncSession, e: StoredEvent) -> None:
    p = e.payload
    await _exec(
        session,
        "UPDATE core.artifacts SET title = :title, content_md = :content, version = :v, "
        "source_seq = :seq, updated_at = :at WHERE id = :id AND learner_id = :l",
        title=p["title"],
        content=p["text"],
        v=p["version"],
        seq=e.seq,
        at=e.occurred_at,
        id=p["artifact_id"],
        l=e.learner_id,
    )


async def _note_deleted(session: AsyncSession, e: StoredEvent) -> None:
    await _exec(
        session,
        "UPDATE core.artifacts SET is_deleted = TRUE, source_seq = :seq, updated_at = :at "
        "WHERE id = :id AND learner_id = :l",
        seq=e.seq,
        at=e.occurred_at,
        id=e.payload["artifact_id"],
        l=e.learner_id,
    )


async def _cheatsheet_updated(session: AsyncSession, e: StoredEvent) -> None:
    p = e.payload
    await _insert_artifact(
        session,
        e,
        kind="cheatsheet",
        domain_id=p["domain_id"],
        topic_id=p["topic_id"],
        session_id=None,
        version=p["version"],
    )


HANDLERS: dict[str, Handler] = {
    "domain.created": _domain_created,
    "domain.archived": _domain_archived,
    "structure.interview_completed": _interview_completed,
    "structure.confirmed": _structure_confirmed,
    "structure.edited": _structure_edited,
    "session.started": _session_started,
    "session.completed": _session_completed,
    "session.abandoned": _session_abandoned,
    "session.checkpointed": _session_checkpointed,
    "turn.student_replied": _turn_replied,
    "turn.teacher_replied": _turn_replied,
    "task.submitted": _task_submitted,
    "task.generated": _task_generated,
    "session.digest_created": _digest_created,
    "artifact.note_created": _note_created,
    "artifact.note_updated": _note_updated,
    "artifact.note_deleted": _note_deleted,
    "artifact.cheatsheet_updated": _cheatsheet_updated,
}


async def apply_relational_projection(session: AsyncSession, event: StoredEvent) -> None:
    """Update core.domains / core.sessions / core.artifacts / core.task_bank for `event`."""
    handler = HANDLERS.get(event.type)
    if handler is not None:
        await handler(session, event)
