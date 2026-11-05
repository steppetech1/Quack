"""Hubs in the journal for the WP23.5 tests: `hub.created` and `hub.deadline_set` through the
real `append_events` (the hub must be the learner's, law 1; Т-§20.7)."""

from __future__ import annotations

from uuid import UUID, uuid4

from quack.db import Db
from quack.events.log import NewEvent, append_events

DAY_MS = 86_400_000


async def create_hub(
    db: Db, learner_id: UUID, *, name: str = "Calculus exam", domain_id: UUID | None = None
) -> tuple[UUID, UUID]:
    """A hub of the learner; returns `(hub_id, domain_id)`."""
    hub_id, domain_id = uuid4(), domain_id or uuid4()
    created = NewEvent(
        type="hub.created",
        payload={
            "hub_id": hub_id,
            "domain_id": domain_id,
            "name": name,
            "kind": "request",
            "origin": "learner",
            "control_type": "tasks",
        },
        actor="code",
        idempotency_key=f"hub:{hub_id}",
        domain_id=domain_id,
    )
    await append_events(db, learner_id, [created])
    return hub_id, domain_id


async def set_deadline(db: Db, learner_id: UUID, hub_id: UUID, deadline_at_ms: int | None) -> None:
    event = NewEvent(
        type="hub.deadline_set",
        payload={"hub_id": hub_id, "deadline_at_ms": deadline_at_ms, "source": "learner"},
        actor="code",
        idempotency_key=f"deadline:{hub_id}:{uuid4()}",
    )
    await append_events(db, learner_id, [event])
