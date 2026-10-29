"""Session overlay (Т-§5.5, Т-§2.6, spec §8.2) and read_consistent_slice (spec §8.2)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis

from quack.config import FoldParams
from quack.db.postgres import SessionFactory
from quack.events import append_events
from quack.events.models import NewEvent, StoredEvent
from quack.graph.memory import InMemoryGraph
from quack.graph.overlay import build_session_overlay
from quack.graph.projector import run_projector_batch
from quack.graph.slice import SliceDeps, read_consistent_slice
from tests.factories import new_event

P = FoldParams()


def _structure(domain: UUID, topic: UUID, skill: UUID) -> list[NewEvent]:
    d = str(domain)
    return [
        new_event("domain.created", {"domain_id": d, "name": "Calc", "slug": "calc"}),
        new_event(
            "structure.proposed",
            {
                "domain_id": d,
                "structure_version": 1,
                "topics": [{"topic_id": str(topic), "name": "Derivatives"}],
                "skills": [{"skill_id": str(skill), "topic_id": str(topic), "name": "Power rule"}],
                "prereqs": [],
                "skill_deps": [],
            },
        ),
        new_event("structure.confirmed", {"domain_id": d, "structure_version": 1}),
    ]


def _task(task: UUID, domain: UUID, topic: UUID, skill: UUID, family: str) -> NewEvent:
    return new_event(
        "task.generated",
        {
            "task_id": str(task),
            "domain_id": str(domain),
            "topic_id": str(topic),
            "family_id": family,
            "difficulty": 0.5,
            "kind": "math",
            "prompt_text": "d/dx x^3",
            "reference_solution": {"sympy": "3*x**2"},
            "target_skills": [{"skill": {"skill_id": str(skill)}, "weight": 1.0}],
        },
    )


async def _append(db: SessionFactory, learner: UUID, events: list[NewEvent]) -> list[StoredEvent]:
    async with db() as session, session.begin():
        return await append_events(session, learner, events, publish_wakeup=False)


async def test_overlay_shows_fresh_session_events_over_lagging_projection(
    db: SessionFactory,
) -> None:
    learner, domain, topic, skill, task, task2 = (uuid4() for _ in range(6))
    sid, other_sid = uuid4(), uuid4()
    s, turn = str(sid), str(uuid4())

    head = await _append(
        db,
        learner,
        [
            *_structure(domain, topic, skill),
            _task(task, domain, topic, skill, "fa"),
            _task(task2, domain, topic, skill, "fb"),
            new_event(
                "session.started",
                {
                    "session_id": s,
                    "domain_id": str(domain),
                    "topic_id": str(topic),
                    "mode": "practice",
                    "goal": None,
                    "plan": [],
                },
            ),
        ],
    )
    # The projector has applied everything up to session.started only.
    base = InMemoryGraph()
    outcome = await run_projector_batch(learner, head, base, P)
    applied_seq = outcome.applied_seq
    assert applied_seq == head[-1].seq
    base_before = base.snapshot()

    tail = await _append(
        db,
        learner,
        [
            new_event(
                "turn.student_replied",
                {"session_id": s, "turn_id": turn, "turn_index": 0, "text": "ready"},
            ),
            new_event(
                "task.assigned",
                {"session_id": s, "turn_id": turn, "task_id": str(task), "topic_id": str(topic)},
            ),
            new_event(
                "turn.teacher_replied",
                {
                    "session_id": s,
                    "turn_id": turn,
                    "turn_index": 1,
                    "text": "Solve d/dx x^3",
                    "pedagogical_move": "assign_task",
                },
            ),
            new_event(
                "task.hint_given",
                {
                    "session_id": s,
                    "turn_id": turn,
                    "task_id": str(task),
                    "hint_level": 2,
                    "hint_text": "power rule",
                    "independence_penalty": 0.5,
                },
            ),
            new_event(
                "task.submitted",
                {
                    "session_id": s,
                    "turn_id": turn,
                    "task_id": str(task),
                    "answer": "3x^2",
                    "hints_used": 1,
                },
            ),
            new_event(
                "task.evaluated",
                {
                    "session_id": s,
                    "turn_id": turn,
                    "task_id": str(task),
                    "evaluator": "compute",
                    "is_correct": True,
                    "score": 1.0,
                    "independence": 0.5,
                    "feedback": "correct",
                    "skill_scores": [{"skill_id": str(skill), "score": 1.0, "tested_depth": 0.5}],
                },
            ),
            new_event(
                "task.assigned",
                {
                    "session_id": s,
                    "turn_id": str(uuid4()),
                    "task_id": str(task2),
                    "topic_id": str(topic),
                },
            ),
        ],
    )
    # An event of another session must not leak into this overlay.
    await _append(
        db,
        learner,
        [
            new_event(
                "turn.student_replied",
                {"session_id": str(other_sid), "turn_id": turn, "turn_index": 9, "text": "x"},
            )
        ],
    )

    async with db() as session:
        overlay = await build_session_overlay(session, learner, sid, applied_seq, base, P)

    assert overlay.applied_seq == applied_seq
    assert overlay.overlay_seq == tail[-1].seq
    assert [e.seq for e in overlay.events] == [e.seq for e in tail]
    assert overlay.assigned_task_ids == [task, task2]
    assert overlay.submitted_task_ids == [task]
    assert overlay.evaluations[task].is_correct is True
    assert overlay.evaluations[task].score == 1.0
    assert [h.hint_level for h in overlay.hints[task]] == [2]
    assert overlay.last_turn_index == 1

    task_node = overlay.graph.get("Task", str(task))
    assert task_node is not None and task_node["status"] == "evaluated"
    assigned = overlay.graph.get("Task", str(task2))
    assert assigned is not None and assigned["status"] == "assigned"
    skill_node = overlay.graph.get("Skill", str(skill))
    base_skill = base.get("Skill", str(skill))
    assert skill_node is not None and base_skill is not None
    assert skill_node["evidenceCount"] == 1
    assert float(skill_node["pMastery"]) > float(base_skill["pMastery"])  # type: ignore[arg-type]
    # The overlay never writes into the projected slice.
    assert base.snapshot() == base_before


async def test_overlay_is_empty_when_projection_caught_up(db: SessionFactory) -> None:
    learner, domain, topic, skill = (uuid4() for _ in range(4))
    sid = uuid4()
    events = await _append(
        db,
        learner,
        [
            *_structure(domain, topic, skill),
            new_event(
                "session.started",
                {
                    "session_id": str(sid),
                    "domain_id": str(domain),
                    "topic_id": None,
                    "mode": "learn",
                    "goal": None,
                    "plan": [],
                },
            ),
        ],
    )
    base = InMemoryGraph()
    await run_projector_batch(learner, events, base, P)
    async with db() as session:
        overlay = await build_session_overlay(session, learner, sid, events[-1].seq, base, P)
    assert overlay.events == []
    assert overlay.assigned_task_ids == []
    assert overlay.overlay_seq == events[-1].seq
    assert overlay.graph.snapshot() == base.snapshot()


# --- read_consistent_slice -------------------------------------------------------------


def _deps(db: SessionFactory, redis: Redis | None, applied: dict[str, int]) -> SliceDeps:
    async def reader(learner_id: UUID) -> int:
        return applied.get(str(learner_id), 0)

    return SliceDeps(session_factory=db, redis=redis, applied_seq_reader=reader)


async def test_slice_returns_immediately_when_caught_up(db: SessionFactory, redis: Redis) -> None:
    learner = uuid4()
    applied = {str(learner): 5}
    calls: list[int] = []

    async def loader() -> dict[str, Any]:
        calls.append(1)
        return {"ok": True}

    result = await read_consistent_slice(learner, 5, loader, 2000, deps=_deps(db, redis, applied))
    assert result.data == {"ok": True}
    assert result.lag_degraded is False and result.stable is True
    assert result.applied_seq == 5 and len(calls) == 1


async def test_slice_uses_last_seq_when_min_seq_is_none(db: SessionFactory, redis: Redis) -> None:
    learner = uuid4()
    await _append(
        db,
        learner,
        [new_event("domain.created", {"domain_id": str(uuid4()), "name": "a", "slug": "a"})],
    )

    async def loader() -> str:
        return "slice"

    result = await read_consistent_slice(
        learner, None, loader, 200, deps=_deps(db, redis, {str(learner): 1})
    )
    assert result.min_seq == 1 and result.lag_degraded is False


async def test_slice_waits_for_projector_applied_signal(db: SessionFactory, redis: Redis) -> None:
    learner = uuid4()
    applied = {str(learner): 3}

    async def loader() -> int:
        return applied[str(learner)]

    async def projector_catches_up() -> None:
        await asyncio.sleep(0.2)
        applied[str(learner)] = 7
        await redis.publish(f"projector:applied:{learner}", json.dumps({"applied_seq": 7}))

    started = time.monotonic()
    task = asyncio.create_task(projector_catches_up())
    result = await read_consistent_slice(learner, 7, loader, 3000, deps=_deps(db, redis, applied))
    await task
    assert result.lag_degraded is False
    assert result.data == 7 and result.applied_seq == 7
    assert time.monotonic() - started < 2.5


async def test_slice_degrades_after_timeout(db: SessionFactory, redis: Redis) -> None:
    learner = uuid4()

    async def loader() -> str:
        return "stale"

    started = time.monotonic()
    result = await read_consistent_slice(
        learner, 10, loader, 300, deps=_deps(db, redis, {str(learner): 2})
    )
    elapsed = time.monotonic() - started
    assert result.lag_degraded is True
    assert result.data == "stale" and result.applied_seq == 2
    assert 0.25 <= elapsed < 2.0


async def test_slice_degrades_without_redis(db: SessionFactory) -> None:
    learner = uuid4()

    async def loader() -> str:
        return "stale"

    result = await read_consistent_slice(
        learner, 10, loader, 200, deps=_deps(db, None, {str(learner): 2})
    )
    assert result.lag_degraded is True


async def test_slice_retries_until_watermark_is_stable(db: SessionFactory, redis: Redis) -> None:
    learner = uuid4()
    applied = {str(learner): 5}

    async def loader() -> str:
        # The projector commits while we read: the watermark moves once.
        if applied[str(learner)] == 5:
            applied[str(learner)] = 6
        return f"slice@{applied[str(learner)]}"

    result = await read_consistent_slice(learner, 5, loader, 2000, deps=_deps(db, redis, applied))
    assert result.stable is True
    assert result.data == "slice@6" and result.applied_seq == 6
