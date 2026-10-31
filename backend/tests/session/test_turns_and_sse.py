"""Turns pipeline and SSE streaming tests (ТЗ §2.3, §3, §5 `test_turns_and_sse.py`)."""

from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
from neo4j import AsyncDriver
from sqlalchemy import text

from quack.config import FoldParams
from quack.db.postgres import SessionFactory
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import run_projector_batch
from quack.session.schemas import ConceptExplainedDraft, TeacherOutput
from tests.session.fixtures import SeededDomain
from tests.session.helpers import parse_sse, skill_label, task_label

pytest_plugins = ["tests.session.fixtures"]


async def _project_all(neo4j: AsyncDriver, db: SessionFactory, learner_id: UUID) -> None:
    """Project all events for learner up to the newest seq so projector is not lagging."""
    async with db() as session:
        result = await session.execute(
            text(f"SELECT {EVENT_COLUMNS} FROM core.events WHERE learner_id = :l ORDER BY seq"),
            {"l": learner_id},
        )
        events = [row_to_event(r) for r in result.all()]
    store = Neo4jGraphStore(neo4j, str(learner_id))
    outcome = await run_projector_batch(learner_id, events, store, FoldParams())
    assert outcome.applied_seq == events[-1].seq


async def test_full_turn_sse_order_format_events_and_extract_job(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm,
) -> None:
    """ТЗ turns test 1: Full turn pipeline: SSE order/format, events, and turn.extract job."""
    # 1. Start an active session
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-turn-1",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])

    # Project session.started into Neo4j so projector is not lagging
    await _project_all(neo4j, db, seeded_domain.learner_id)

    # 2. Queue teacher LLM response with concept explained and task assigned
    # WP14.1: the teacher answers with the labels of its context (topic T1, skills S…, tasks K…).
    power = skill_label(seeded_domain.skill_ids, seeded_domain.skill_ids[0])
    concept_draft = ConceptExplainedDraft(
        topic_label="T1",
        explained_skill_labels=[power],
        depth=0.7,
        summary="Power rule explanation",
    )
    first_task = await task_label(
        db,
        seeded_domain.learner_id,
        seeded_domain.topic_id,
        session_id,
        seeded_domain.math_task_ids[0],
    )
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Let's practice the power rule.",
            pedagogical_move="assign_task",
            focus_skill_labels=[power],
            concept_explained=concept_draft,
            assign_task_label=first_task,
        ),
    )

    # 3. Post turn
    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "I want to learn calculus", "idempotency_key": "turn-test-1"},
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200, turn_resp.text
    assert turn_resp.headers["content-type"].startswith("text/event-stream")

    # 4. Check SSE sequence and format
    # format: turn_started → token → task_assigned → turn_completed → done
    sse_events = parse_sse(turn_resp.text)
    event_names = [name for name, _ in sse_events]
    assert event_names == ["turn_started", "token", "task_assigned", "turn_completed", "done"]

    turn_started = sse_events[0][1]
    turn_id = UUID(turn_started["turn_id"])
    assert turn_started["turn_index"] == 0
    assert isinstance(turn_started["user_seq"], int)
    user_seq = turn_started["user_seq"]

    token_event = sse_events[1][1]
    assert token_event["delta"] == "Let's practice the power rule."

    task_assigned = sse_events[2][1]
    assert UUID(task_assigned["task_id"]) == seeded_domain.math_task_ids[0]
    assert task_assigned["kind"] == "math"
    assert task_assigned["prompt_text"] == "Find d/dx x^3"

    turn_completed = sse_events[3][1]
    assert UUID(turn_completed["turn_id"]) == turn_id
    assert isinstance(turn_completed["committed_seq"], int)
    assert turn_completed["committed_seq"] > user_seq
    assert turn_completed["degraded_mode"] is None

    assert sse_events[4][1] == {}

    # 5. Check events recorded in core.events
    async with db() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT seq, type, session_id, turn_id, actor, payload FROM core.events "
                    "WHERE learner_id = :l AND session_id = :s ORDER BY seq"
                ),
                {"l": seeded_domain.learner_id, "s": session_id},
            )
        ).all()

    turn_events = [r for r in rows if r.turn_id == turn_id]
    assert len(turn_events) == 4
    types = [r.type for r in turn_events]
    assert types == [
        "turn.student_replied",
        "teacher.concept_explained",
        "task.assigned",
        "turn.teacher_replied",
    ]

    student_event = turn_events[0]
    assert student_event.seq == user_seq
    assert student_event.payload["turn_index"] == 0
    assert student_event.payload["text"] == "I want to learn calculus"
    assert student_event.actor == "learner"

    concept_event = turn_events[1]
    assert concept_event.payload["summary"] == "Power rule explanation"
    assert concept_event.payload["depth"] == 0.7
    assert concept_event.actor == "teacher@1"

    task_event = turn_events[2]
    assert UUID(task_event.payload["task_id"]) == seeded_domain.math_task_ids[0]
    assert task_event.actor == "teacher@1"

    teacher_event = turn_events[3]
    assert teacher_event.seq == turn_completed["committed_seq"]
    assert teacher_event.payload["turn_index"] == 1
    assert teacher_event.payload["text"] == "Let's practice the power rule."
    assert teacher_event.payload["pedagogical_move"] == "assign_task"
    assert teacher_event.actor == "teacher@1"
    assert teacher_event.payload["degraded_mode"] is None

    # 6. Check turn.extract job enqueued in ops.jobs
    async with db() as session:
        job = (
            await session.execute(
                text(
                    "SELECT id, kind, status, priority, lock_key, payload FROM ops.jobs "
                    "WHERE learner_id = :l AND kind = 'turn.extract' AND payload->>'turn_id' = :t"
                ),
                {"l": seeded_domain.learner_id, "t": str(turn_id)},
            )
        ).one_or_none()
    assert job is not None
    assert job.kind == "turn.extract"
    assert job.status == "queued"
    assert job.priority == 10
    assert job.lock_key == f"session:{session_id}:extract"
    assert job.payload["session_id"] == str(session_id)
    assert job.payload["turn_id"] == str(turn_id)
    assert job.payload["student_seq"] == user_seq
    assert job.payload["teacher_seq"] == teacher_event.seq


async def test_turn_without_concept_or_task(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm,
) -> None:
    """Turn where teacher asks a question: no concept_explained, no task.assigned."""
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-turn-simple",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="What do you think d/dx x^2 is?",
            pedagogical_move="socratic_question",
        ),
    )

    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Hello teacher", "idempotency_key": "turn-simple-1"},
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200

    sse_events = parse_sse(turn_resp.text)
    event_names = [name for name, _ in sse_events]
    assert event_names == ["turn_started", "token", "turn_completed", "done"]

    async with db() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT type FROM core.events WHERE learner_id = :l "
                        "AND session_id = :s AND type LIKE 'turn.%' ORDER BY seq"
                    ),
                    {"l": seeded_domain.learner_id, "s": session_id},
                )
            )
            .scalars()
            .all()
        )
    assert list(rows) == ["turn.student_replied", "turn.teacher_replied"]


async def test_practice_mode_assigns_task_even_if_move_is_not_assign_task(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm,
) -> None:
    """In practice mode, a task is assigned even when pedagogical_move is explain."""
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "mode": "practice",
            "idempotency_key": "start-practice",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Let's practice now.",
            pedagogical_move="explain",
        ),
    )

    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Ready for practice", "idempotency_key": "turn-practice-1"},
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200

    sse_events = parse_sse(turn_resp.text)
    event_names = [name for name, _ in sse_events]
    assert event_names == ["turn_started", "token", "task_assigned", "turn_completed", "done"]


async def test_parallel_turn_on_same_session_gets_409_turn_locked(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    fake_llm,
) -> None:
    """ТЗ turns test 3: Parallel turn on the same session gets 409 TURN_LOCKED."""
    # 1. Start an active session
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-turn-parallel",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])

    # 2. Synchronize turn 1 so it holds lock while turn 2 is sent
    turn1_started = asyncio.Event()
    turn2_proceed = asyncio.Event()

    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="First reply",
            pedagogical_move="explain",
        ),
        TeacherOutput(
            reply_text="Third reply",
            pedagogical_move="explain",
        ),
    )

    orig_complete = fake_llm.complete_structured

    async def slow_complete(req):
        turn1_started.set()
        await turn2_proceed.wait()
        return await orig_complete(req)

    fake_llm.complete_structured = slow_complete

    # 3. Fire turn 1 in the background
    t1 = asyncio.create_task(
        client.post(
            f"/v1/sessions/{session_id}/turns",
            json={"text": "First turn text", "idempotency_key": "turn-p1"},
            headers=auth_headers,
        )
    )

    # 4. Wait until turn 1 has acquired the lock and entered slow_complete
    await asyncio.wait_for(turn1_started.wait(), timeout=5.0)

    # 5. Fire turn 2 while turn 1 is actively holding the lock
    resp2 = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Concurrent turn text", "idempotency_key": "turn-p2"},
        headers=auth_headers,
    )
    assert resp2.status_code == 409
    data2 = resp2.json()
    assert data2["error"]["code"] == "TURN_LOCKED"

    # 6. Let turn 1 complete
    turn2_proceed.set()
    resp1 = await t1
    assert resp1.status_code == 200

    # 7. Verify lock is released and a subsequent turn succeeds
    fake_llm.complete_structured = orig_complete

    resp3 = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Subsequent turn text", "idempotency_key": "turn-p3"},
        headers=auth_headers,
    )
    assert resp3.status_code == 200
