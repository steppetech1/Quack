"""`agent.completed` for the jobs of artifacts (WP23.2, WP23.7; D-C-230): the same event under the
same key `agent.completed:{ref}` as `quack.agents.delivery` (stream B, D-B-191), so a repeat of a
finished job finds it and writes nothing (law 5). The package `artifacts` may not import
`agents` (ТЗ §4.1: product packages see `practice` and themselves), hence the small copy."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from quack.db.db import Db
from quack.events.event_types import AgentCompleted
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.practice.repo import event_by_key


def completed_key(ref: UUID | str) -> str:
    return f"agent.completed:{ref}"


async def deliver(
    db: Db,
    *,
    learner_id: UUID,
    request_id: UUID | None,
    job_key: str | None,
    kind: str,
    ok: bool,
    actor: str,
    task_ids: Sequence[UUID] = (),
    error: str | None = None,
    result_ref: str | None = None,
    domain_id: UUID | None = None,
    session_id: UUID | None = None,
    causation_seq: int | None = None,
) -> StoredEvent:
    """`agent.completed` of a request (`request_id`) or of a job (`job_key`); the stored one
    if the job already delivered."""
    ref = request_id or job_key
    if ref is None:
        raise ValueError("agent.completed needs a request_id or a job_key")
    key = completed_key(ref)
    done = await event_by_key(db, learner_id=learner_id, key=key)
    if done is not None:
        return done
    completed = AgentCompleted(
        request_id=request_id,
        job_key=None if request_id else job_key,
        kind=kind,
        ok=ok,
        result_ref=result_ref,
        error=error,
        task_ids=list(task_ids),
    )
    (stored,) = await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="agent.completed",
                payload=completed,
                actor=actor,
                idempotency_key=key,
                domain_id=domain_id,
                session_id=session_id,
                causation_seq=causation_seq,
            )
        ],
    )
    return stored
