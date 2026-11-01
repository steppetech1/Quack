"""Dispatcher rules "event -> job" (ТЗ Фазы 3 WP11.2, seam S11).

The table below is filled from the WP0 audit of `enqueue_job` calls (PHASE3_NOTES.md, "Вызовы
`enqueue_job`"). Each rule must yield exactly the job the event's own transaction enqueues
today: same kind, priority, lock_key, coalesce_key, so that both schemes coexist without
duplicates until the switch (WP11.4).

`rules_for` is a pure function of the event and of `EventFacts`, the relational data the
dispatcher loads for it (`needs` below); it never reads the graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from quack.events.models import StoredEvent
from quack.jobs import kinds
from quack.jobs.queue import JobSpec


@dataclass(frozen=True)
class DispatcherRule:
    """One row of the table. `needs` names data the rule reads beyond the event itself."""

    event_type: str
    job_kind: str
    required_seq: str  # "event_seq" = seq of the trigger event, "null" = the job reads no graph
    condition: str
    needs: str
    replaces: str  # the enqueue_job call that WP11.4 removes


# Periodic jobs (`session.sweep_inactive`, `account.purge_deleted`) belong to the scheduler
# (WP3.3), `POST /v1/topics/{id}/cheatsheet/refresh` enqueues directly (WP11.2 exception).
DISPATCHER_RULES: tuple[DispatcherRule, ...] = (
    DispatcherRule(
        event_type="turn.teacher_replied",
        job_kind="turn.extract",
        required_seq="event_seq",
        condition="always: the turn pair is the unit of extraction",
        needs="seq of the turn.student_replied with the same turn_id (payload student_seq)",
        replaces="session/turns.py:554",
    ),
    DispatcherRule(
        event_type="turn.teacher_replied",
        job_kind="session.checkpoint",
        required_seq="null",
        condition="turn_index - last_checkpoint_turn >= checkpoint_every_turns",
        needs="last_checkpoint_turn of the session (see D-A-003)",
        replaces="session/turns.py:569",
    ),
    DispatcherRule(
        event_type="session.completed",
        job_kind="session.finish_artifacts",
        required_seq="event_seq",
        condition="always",
        needs="topic_id of the session (not in the event payload, read from the session row)",
        replaces="session/lifecycle.py:459",
    ),
    DispatcherRule(
        event_type="session.completed",
        job_kind="graph.curate",
        required_seq="event_seq",
        condition="always",
        needs="domain_id from the event payload",
        replaces="session/lifecycle.py:470",
    ),
    DispatcherRule(
        event_type="session.digest_created",
        job_kind="artifact.refresh_cheatsheet",
        required_seq="event_seq",
        condition="payload.topic_id is not null",
        needs="domain_id, session_id, topic_id from the event payload",
        replaces="artifacts/cheatsheets.py:250",
    ),
)


# Event types whose rules need `EventFacts` (the session row, the student reply of the turn).
_NEEDS_FACTS = frozenset({"turn.teacher_replied", "session.completed"})


@dataclass(frozen=True)
class EventFacts:
    """Relational data a rule reads beyond the event (`DispatcherRule.needs`)."""

    session_found: bool = False
    topic_id: UUID | None = None
    last_checkpoint_turn: int | None = None
    student_seq: int | None = None


def needs_facts(event: StoredEvent) -> bool:
    return event.type in _NEEDS_FACTS and event.payload.get("session_id") is not None


def _teacher_replied(
    event: StoredEvent, facts: EventFacts, checkpoint_every_turns: int
) -> list[JobSpec]:
    session_id = UUID(str(event.payload["session_id"]))
    turn_index = int(event.payload["turn_index"])
    specs = [
        kinds.turn_extract(
            event.learner_id,
            session_id,
            {
                "session_id": str(session_id),
                "turn_id": str(event.turn_id or event.payload["turn_id"]),
                "student_seq": facts.student_seq,
                "teacher_seq": event.seq,
            },
            required_seq=event.seq,
        )
    ]
    last = -1 if facts.last_checkpoint_turn is None else facts.last_checkpoint_turn
    if turn_index - last >= checkpoint_every_turns:
        specs.append(
            kinds.session_checkpoint(
                event.learner_id,
                session_id,
                {"session_id": str(session_id), "up_to_turn_index": turn_index},
            )
        )
    return specs


def _session_completed(event: StoredEvent, facts: EventFacts) -> list[JobSpec]:
    session_id = UUID(str(event.payload["session_id"]))
    domain_id = UUID(str(event.payload["domain_id"]))
    return [
        kinds.session_finish_artifacts(
            event.learner_id,
            session_id,
            # WP6.4 п.2: the payload of `finish_session` → `session.finish_artifacts`.
            {
                "session_id": str(session_id),
                "domain_id": str(domain_id),
                "topic_id": None if facts.topic_id is None else str(facts.topic_id),
            },
            required_seq=event.seq,
        ),
        kinds.graph_curate(
            event.learner_id, domain_id, {"domain_id": str(domain_id)}, required_seq=event.seq
        ),
    ]


def _digest_created(event: StoredEvent) -> list[JobSpec]:
    topic = event.payload.get("topic_id")
    if topic is None:
        return []
    return [
        kinds.artifact_refresh_cheatsheet(
            event.learner_id,
            UUID(str(topic)),
            {
                "domain_id": str(event.payload["domain_id"]),
                "session_id": str(event.payload["session_id"]),
            },
            required_seq=event.seq,
        )
    ]


def rules_for(
    event: StoredEvent, facts: EventFacts | None = None, *, checkpoint_every_turns: int
) -> list[JobSpec]:
    """Jobs the event triggers; event types without a rule yield an empty list (WP11.3)."""
    facts = facts or EventFacts()
    if event.type == "turn.teacher_replied":
        return _teacher_replied(event, facts, checkpoint_every_turns)
    if event.type == "session.completed":
        return _session_completed(event, facts)
    if event.type == "session.digest_created":
        return _digest_created(event)
    return []
