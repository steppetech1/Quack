"""Dispatcher rules "event -> job" (ТЗ Фазы 3 WP11.2, seam S11).

The table below is filled from the WP0 audit of `enqueue_job` calls (PHASE3_NOTES.md, "Вызовы
`enqueue_job`"). Each rule must yield exactly the job the event's own transaction enqueues
today: same kind, priority, lock_key, coalesce_key, so that both schemes coexist without
duplicates until the switch (WP11.4). `rules_for` is implemented in stage III.
"""

from __future__ import annotations

from dataclasses import dataclass

from quack.events.models import StoredEvent
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


def rules_for(event: StoredEvent) -> list[JobSpec]:
    """Jobs the event triggers; unknown event types yield an empty list (WP11.3)."""
    raise NotImplementedError("WP11.2: stage III")
