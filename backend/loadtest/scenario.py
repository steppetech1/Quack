"""Logic of the Locust scenario (ТЗ Фазы 3 WP10.3, `Д-§11`), kept apart from `locustfile.py`:
Locust patches the standard library with gevent on import, so tests import only this module.

A virtual user takes a learner of `ops seed-learners` (WP8.6), starts a session, makes a turn
and sends a heartbeat every `TURN_INTERVAL_S`, sometimes takes a task (`tasks/next`) and
answers it (`submit`), and after `TURNS_PER_SESSION` turns finishes the session.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

# `Д-§11`: a turn and a heartbeat every 30 s.
TURN_INTERVAL_S = 30.0
# D-C-031: values of the scenario that the documents do not give.
TURNS_PER_SESSION = 10
TASK_SHARE = 0.2
TASK_KIND = "choice"
TASK_ANSWER = "A"
TURN_TEXT = "Can you explain the next step?"

LEARNER_KEYS = ("learner_id", "token", "domain_id", "topic_ids")


@dataclass(frozen=True)
class Learner:
    """One row of the file of `ops seed-learners` (WP8.6)."""

    learner_id: UUID
    token: str
    domain_id: UUID
    topic_ids: tuple[UUID, ...]

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def load_learners(path: Path) -> list[Learner]:
    """The learners of the file; `ValueError` for an empty list or a row without the keys."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path}: expected a non-empty JSON list of learners")
    learners: list[Learner] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or any(k not in row for k in LEARNER_KEYS):
            raise ValueError(f"{path}: row {i} needs the keys {', '.join(LEARNER_KEYS)}")
        topics = tuple(UUID(t) for t in row["topic_ids"])
        if not topics:
            raise ValueError(f"{path}: row {i} has no topic_ids")
        learners.append(
            Learner(UUID(row["learner_id"]), str(row["token"]), UUID(row["domain_id"]), topics)
        )
    return learners


def session_start_body(domain_id: UUID, topic_id: UUID, key: str) -> dict[str, Any]:
    return {"domain_id": str(domain_id), "topic_id": str(topic_id), "idempotency_key": key}


def turn_body(text: str, key: str) -> dict[str, Any]:
    return {"text": text, "idempotency_key": key}


def next_task_body(topic_id: UUID, turn_id: str, key: str) -> dict[str, Any]:
    return {
        "topic_id": str(topic_id),
        "turn_id": turn_id,
        "kind": TASK_KIND,
        "idempotency_key": key,
    }


def submit_body(turn_id: str, key: str) -> dict[str, Any]:
    return {"turn_id": turn_id, "answer": TASK_ANSWER, "idempotency_key": key}


def key_body(key: str) -> dict[str, Any]:
    return {"idempotency_key": key}


@dataclass
class TurnStream:
    """What the scenario measures in one turn stream: time to `status` (`teacher_status_p95_ms`,
    WP6.3) and to the first `token`, both from the start of the request; `complete` — `done`
    arrived (a stream without it is cut)."""

    events: list[str] = field(default_factory=list)
    turn_id: str | None = None
    status_ms: float | None = None
    token_ms: float | None = None
    complete: bool = False


def read_turn_stream(
    lines: Iterator[bytes],
    *,
    started_at: float,
    clock: Callable[[], float] = time.monotonic,
) -> TurnStream:
    """Read SSE lines of `POST /v1/sessions/{id}/turns` as they arrive."""
    result = TurnStream()
    name: str | None = None
    data: list[str] = []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r")
        if line.startswith("event:"):
            name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data.append(line.removeprefix("data:").strip())
        elif line == "" and name is not None:
            _frame(result, name, "\n".join(data), (clock() - started_at) * 1000.0)
            name, data = None, []
    return result


def _frame(result: TurnStream, name: str, data: str, elapsed_ms: float) -> None:
    result.events.append(name)
    if name == "turn_started":
        result.turn_id = str(json.loads(data)["turn_id"]) if data else None
    elif name == "status" and result.status_ms is None:
        result.status_ms = elapsed_ms
    elif name == "token" and result.token_ms is None:
        result.token_ms = elapsed_ms
    elif name == "done":
        result.complete = True
