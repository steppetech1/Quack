"""The session overlay on `rules@3` (ТЗ Ф4 WP2.3, WP3.4; Т-§2.5, Т-§2.6; D-A-239).

`build_session_overlay` is the third mode of the one set of handlers and folds: it reads the
events of the session with `seq > projectedSeq` from Postgres, the slice they declared from
Neo4j (read-only), and runs the same phases A–C as the batch loop over a copy in memory
(`projection.runner.run_batch`). The overlay holds no rules of its own, and nothing is written
to Neo4j or Postgres. The events of other sessions are not applied (Т-§2.6: «события текущей
сессии»); an event the handlers cannot apply raises `EventFailed`, as in the batch loop — the
caller decides how to degrade (law 9).
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import cast
from uuid import UUID

from neo4j import AsyncDriver

from quack.config import Settings
from quack.db import Db
from quack.events.log import StoredEvent, read_session_tail
from quack.graph.memory import InMemoryGraph
from quack.graph.overlay import HintView, SessionOverlay, TaskEvaluationView
from quack.graph.projection.phase_b import FoldLoader, WholeSlice
from quack.graph.projection.runner import Neo4jFolds, load_batch_inputs, run_batch
from quack.graph.projection.store import read_watermark
from quack.graph.projection.types import SubGraph
from quack.graph.types import Props


def _graph_of(slice_: SubGraph) -> InMemoryGraph:
    """The overlay graph the readers use: every node and relationship of the slice after phases
    A–C, with `id` as in Neo4j. The watermark props of `Learner` are the projector's, not set here.
    (`InMemoryGraph` keys a relationship by ends and type, so the parallel `TESTS`
    relationships of one task and skill at several levels collapse into one; no reader
    reads them from the overlay.)"""
    graph = InMemoryGraph()
    for n in slice_.nodes:
        graph.nodes[(n.label, n.id)] = cast(Props, {"id": n.id} | copy.deepcopy(n.props))
    for r in slice_.rels:
        key = (r.from_label, r.from_id, r.rel_type, r.to_label, r.to_id)
        graph.rels[key] = cast(Props, copy.deepcopy(r.props))
    return graph


def _summarize(
    overlay: SessionOverlay, tail: Sequence[StoredEvent], known: dict[int, UUID]
) -> None:
    """The views of the session tail the teacher reads: tasks given and answered, verdicts, hints.
    `known` — task of an answer by its `seq`. Phase 4 events carry no turn index (`turn.committed`
    has `action_seq`), so `last_turn_index` stays -1."""
    for event in tail:
        data = event.payload
        if event.type == "task.created":
            _add(overlay.assigned_task_ids, UUID(data["task_id"]))
        elif event.type == "turn.committed":
            for task in data["markup"]["tasks"]:
                _add(overlay.assigned_task_ids, UUID(task["task_id"]))
        elif event.type == "answer.submitted":
            _add(overlay.submitted_task_ids, UUID(data["task_id"]))
        elif event.type == "hint.shown":
            task_id = UUID(data["task_id"])
            overlay.hints.setdefault(task_id, []).append(
                HintView(task_id, event.seq, int(data["step"]), "")
            )
        elif event.type == "verdict.recorded" and data["answer_seq"] in known:
            task_id = known[data["answer_seq"]]
            outcome = str(data["outcome"])
            overlay.evaluations[task_id] = TaskEvaluationView(
                task_id=task_id,
                seq=event.seq,
                is_correct=outcome == "correct",
                score={"correct": 1.0, "partial": 0.5}.get(outcome, 0.0),
                evaluator=str(data["verification"]),
                feedback="",
            )


def _add(ids: list[UUID], task_id: UUID) -> None:
    if task_id not in ids:
        ids.append(task_id)


def empty_overlay(applied_seq: int) -> SessionOverlay:
    """The projector is level with the session: nothing to overlay."""
    return SessionOverlay(
        applied_seq=applied_seq, overlay_seq=applied_seq, events=[], graph=InMemoryGraph()
    )


async def apply_overlay_events(
    learner_id: UUID,
    applied_seq: int,
    tail: Sequence[StoredEvent],
    base_slice: SubGraph,
    settings: Settings,
    *,
    earlier: Sequence[StoredEvent] = (),
    loader: FoldLoader | None = None,
) -> SessionOverlay:
    """The part of the overlay that touches no store: phases A–C of `tail` over a copy of
    `base_slice` (it is not changed). `earlier` — the journal events the handlers read by `seq`;
    `loader` — where phases B–C read what the slice does not hold (default: the slice is the
    whole graph)."""
    events = sorted((e for e in tail if e.seq > applied_seq), key=lambda e: e.seq)
    if not events:
        return empty_overlay(applied_seq)
    batch = await run_batch(
        learner_id, events, base_slice, earlier, settings, loader or WholeSlice([*earlier, *events])
    )
    overlay = SessionOverlay(
        applied_seq=applied_seq,
        overlay_seq=events[-1].seq,
        events=events,
        graph=_graph_of(batch.snapshot()),
    )
    answers = {
        e.seq: UUID(e.payload["task_id"])
        for e in [*earlier, *events]
        if e.type == "answer.submitted"
    }
    _summarize(overlay, events, answers)
    return overlay


async def build_session_overlay(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    learner_id: UUID,
    session_id: UUID,
    applied_seq: int | None = None,
) -> SessionOverlay:
    """Events of the session with `seq > applied_seq` (default: `Learner.projectedSeq`, 0 for a
    learner not projected yet) over the slice they declared, in memory; reads Postgres and Neo4j,
    writes nothing. No tail — an empty overlay with `overlay_seq = applied_seq`."""
    if applied_seq is None:
        watermark = await read_watermark(neo4j, str(learner_id))
        applied_seq = watermark[0] if watermark is not None else 0
    tail = await read_session_tail(db, learner_id, session_id, applied_seq)
    if not tail:
        return empty_overlay(applied_seq)
    loaded, earlier = await load_batch_inputs(db, neo4j, learner_id, tail)
    return await apply_overlay_events(
        learner_id,
        applied_seq,
        tail,
        loaded,
        settings,
        earlier=earlier,
        loader=Neo4jFolds(neo4j, db, learner_id),
    )
