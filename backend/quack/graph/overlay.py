"""Session overlay: unapplied events of the current session over a projected slice (Т-§5.5,
Т-§2.6). Nothing is written to Neo4j.

Only the result shape lives here: the readers take a `SessionOverlay`. It is built on `rules@3`
(handlers over the in-memory slice) in `quack.graph.overlay_build` (WP2.3 / WP3.4, D-A-239); the
builder imports the projector, which imports the readers, so it cannot live in this module."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from quack.events.log import StoredEvent
from quack.graph.memory import InMemoryGraph


@dataclass(frozen=True)
class TaskEvaluationView:
    task_id: UUID
    seq: int
    is_correct: bool
    score: float
    evaluator: str
    feedback: str


@dataclass(frozen=True)
class HintView:
    task_id: UUID
    seq: int
    hint_level: int
    hint_text: str


@dataclass
class SessionOverlay:
    applied_seq: int
    overlay_seq: int
    events: list[StoredEvent]
    graph: InMemoryGraph
    assigned_task_ids: list[UUID] = field(default_factory=list)
    submitted_task_ids: list[UUID] = field(default_factory=list)
    evaluations: dict[UUID, TaskEvaluationView] = field(default_factory=dict)
    hints: dict[UUID, list[HintView]] = field(default_factory=dict)
    last_turn_index: int = -1
