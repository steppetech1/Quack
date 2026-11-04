"""Types of a job shared by the queue, the executor and the handlers (WP1.3, D-A-216).

They outlived the Phase 3 queue (`quack/jobs/queue.py`, removed in WP1.3, D-A-234): handlers of
every stream import them from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


class JobDeferred(Exception):
    """Raised by a handler: run again in `delay_sec`, without spending an attempt (WP3.2)."""

    def __init__(self, delay_sec: float, reason: str) -> None:
        super().__init__(reason)
        self.delay_sec = delay_sec
        self.reason = reason


class JobFailedPermanently(Exception):
    """Raised by a handler or the runner: failed, into ops.dlq, attempts are not spent (WP3.4)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class JobSpec:
    kind: str
    lock_key: str
    priority: int
    coalesce_key: str | None = None
    learner_id: UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    max_attempts: int = 5
    run_after: datetime | None = None
    # WP3.1 (Т-§13.1): seq of the trigger event; None = the job does not read the graph.
    required_seq: int | None = None


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    kind: str
    learner_id: UUID | None
    lock_key: str
    coalesce_key: str | None
    priority: int
    payload: dict[str, Any]
    attempt: int
    max_attempts: int
    required_seq: int | None = None
    # WP1.3 (D-A-200, #210): the key and the session of a job of the Phase 4 queue.
    job_key: str | None = None
    session_id: UUID | None = None
