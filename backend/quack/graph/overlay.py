"""Session overlay: unapplied events of the current session over a projected slice (Т-§5.5,
Т-§2.6). Nothing is written to Neo4j."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from quack.config import FoldParams
from quack.events.catalog import OVERLAY_EVENT_TYPES
from quack.events.models import StoredEvent
from quack.events.reader import read_session_tail
from quack.graph.memory import InMemoryGraph
from quack.graph.projector import project_events


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


async def apply_overlay_events(
    learner_id: UUID,
    applied_seq: int,
    events: Sequence[StoredEvent],
    base_slice: InMemoryGraph,
    params: FoldParams,
) -> SessionOverlay:
    """Pure part: apply phases A–C to a copy of `base_slice` and summarize the tail."""
    graph = base_slice.copy()
    tail = sorted((e for e in events if e.seq > applied_seq), key=lambda e: e.seq)
    overlay = SessionOverlay(
        applied_seq=applied_seq,
        overlay_seq=tail[-1].seq if tail else applied_seq,
        events=tail,
        graph=graph,
    )
    if not tail:
        return overlay
    result = await project_events(learner_id, tail, graph, params, with_watermark=False)
    await graph.apply(result.mutations)
    for event in tail:
        data = event.payload
        if event.type in ("turn.student_replied", "turn.teacher_replied"):
            overlay.last_turn_index = max(overlay.last_turn_index, int(data["turn_index"]))
            continue
        if "task_id" not in data:
            continue
        task_id = UUID(data["task_id"])
        if event.type == "task.assigned" and task_id not in overlay.assigned_task_ids:
            overlay.assigned_task_ids.append(task_id)
        elif event.type == "task.submitted" and task_id not in overlay.submitted_task_ids:
            overlay.submitted_task_ids.append(task_id)
        elif event.type == "task.hint_given":
            overlay.hints.setdefault(task_id, []).append(
                HintView(task_id, event.seq, int(data["hint_level"]), str(data["hint_text"]))
            )
        elif event.type == "task.evaluated":
            overlay.evaluations[task_id] = TaskEvaluationView(
                task_id=task_id,
                seq=event.seq,
                is_correct=bool(data["is_correct"]),
                score=float(data["score"]),
                evaluator=str(data["evaluator"]),
                feedback=str(data["feedback"]),
            )
    return overlay


async def build_session_overlay(
    session: AsyncSession,
    learner_id: UUID,
    session_id: UUID,
    applied_seq: int,
    base_slice: InMemoryGraph,
    params: FoldParams,
) -> SessionOverlay:
    """Read session events with seq > applied_seq from core.events and overlay them."""
    events = await read_session_tail(
        session, learner_id, session_id, after_seq=applied_seq, types=OVERLAY_EVENT_TYPES
    )
    return await apply_overlay_events(learner_id, applied_seq, events, base_slice, params)
