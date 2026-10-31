"""Tests for T-028 (S-4.1, S-4.2, S-4.5): response_time_sec, compute_independence, session_id."""

from __future__ import annotations

from uuid import UUID, uuid4

import httpx
from neo4j import AsyncDriver
from sqlalchemy import text

from quack.config import FoldParams
from quack.db.postgres import SessionFactory
from quack.events import append_events
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import run_projector_batch
from quack.practice.evaluation import independence_from_hints
from quack.session.schemas import TeacherOutput
from tests.factories import new_event
from tests.session.fixtures import FakeComputeClient, FakeLLMProvider, SeededDomain
from tests.session.helpers import parse_sse

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


def test_independence_counts_the_highest_logged_hint() -> None:
    """S-4.2 under WP6.2 (D-B-006): one rule for the turn and practice — the highest
    logged hint level counts, not the sum (Т-§7.4, ТЗ-П3 decision 1)."""
    assert independence_from_hints([]) == 1.0
    assert independence_from_hints([0.3]) == 0.7
    assert independence_from_hints([0.25, 0.5]) == 0.5
    assert independence_from_hints([0.5, 0.25]) == 0.5
    assert independence_from_hints([0.25, 0.5, 0.75]) == 0.25
    # Not the sum: two hints never cost more than the higher of them.
    assert independence_from_hints([0.6, 0.5]) == 0.4


async def test_turn_math_submission_writes_response_time_sec_and_independence(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm: FakeLLMProvider,
    fake_compute: FakeComputeClient,
) -> None:
    """S-4.1 & S-4.2: Turn submitting a math task with response_time_sec=12.5 and hints_used=2."""
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-sub-fields-1",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    math_task_id = seeded_domain.math_task_ids[0]

    fake_compute.push_result(True)
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Correct derivative!",
            pedagogical_move="give_feedback",
            focus_skill_ids=[seeded_domain.skill_ids[0]],
        ),
    )

    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={
            "text": "3*x^2",
            "related_task_id": str(math_task_id),
            "hints_used": 2,
            "response_time_sec": 12.5,
            "idempotency_key": "turn-sub-fields-1",
        },
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200, turn_resp.text

    sse_events = parse_sse(turn_resp.text)
    turn_id = UUID(sse_events[0][1]["turn_id"])

    async with db() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT type, payload FROM core.events "
                    "WHERE learner_id = :l AND session_id = :s AND turn_id = :t ORDER BY seq"
                ),
                {"l": seeded_domain.learner_id, "s": session_id, "t": turn_id},
            )
        ).all()

    by_type = {r.type: r.payload for r in rows}
    assert "task.submitted" in by_type
    assert "task.evaluated" in by_type

    assert by_type["task.submitted"]["response_time_sec"] == 12.5
    assert by_type["task.submitted"]["hints_used"] == 2
    # D-B-006: client-counted hints without a `task.hint_given` event cost nothing.
    assert by_type["task.evaluated"]["independence"] == 1.0


async def test_turn_math_submission_combines_overlay_hint_penalties_and_hints_used(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm: FakeLLMProvider,
    fake_compute: FakeComputeClient,
) -> None:
    """S-4.1 & S-4.2: Overlay hint penalty 0.3 + hints_used=3 yields independence=0.2."""
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-sub-fields-2",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    math_task_id = seeded_domain.math_task_ids[0]
    hint_turn_id = uuid4()

    # Append unprojected task.hint_given in this session so it sits in context.overlay.hints
    async with db() as session, session.begin():
        await append_events(
            session,
            seeded_domain.learner_id,
            [
                new_event(
                    "task.hint_given",
                    {
                        "session_id": str(session_id),
                        "turn_id": str(hint_turn_id),
                        "task_id": str(math_task_id),
                        "hint_level": 1,
                        "hint_text": "Use the power rule n*x^(n-1)",
                        "independence_penalty": 0.3,
                    },
                    session_id=session_id,
                    turn_id=hint_turn_id,
                    domain_id=seeded_domain.domain_id,
                )
            ],
            publish_wakeup=False,
        )

    fake_compute.push_result(True)
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Good job working through the hints!",
            pedagogical_move="give_feedback",
            focus_skill_ids=[seeded_domain.skill_ids[0]],
        ),
    )

    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={
            "text": "3*x^2",
            "related_task_id": str(math_task_id),
            "hints_used": 3,
            "response_time_sec": 9.75,
            "idempotency_key": "turn-sub-fields-2",
        },
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200, turn_resp.text

    sse_events = parse_sse(turn_resp.text)
    turn_id = UUID(sse_events[0][1]["turn_id"])

    async with db() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT type, payload FROM core.events "
                    "WHERE learner_id = :l AND session_id = :s AND turn_id = :t ORDER BY seq"
                ),
                {"l": seeded_domain.learner_id, "s": session_id, "t": turn_id},
            )
        ).all()

    by_type = {r.type: r.payload for r in rows}
    assert by_type["task.submitted"]["response_time_sec"] == 9.75
    assert by_type["task.submitted"]["hints_used"] == 3
    # D-B-006: the logged hint (penalty 0.3) counts; client-counted hints do not.
    assert by_type["task.evaluated"]["independence"] == 0.7


async def test_turns_pass_session_id_to_get_available_tasks_and_pick_task(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm: FakeLLMProvider,
) -> None:
    """S-4.5: get_available_tasks and pick_task in turns.py receive session_id=session_id."""
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-sub-session-id",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])

    math1, math2 = seeded_domain.math_task_ids
    prior_turn_id = uuid4()

    # Record task.assigned and task.submitted for math1 in this session and project them.
    # Once projected, Task(math1) in Neo4j has status='submitted' (not 'assigned'/'evaluated')
    # and overlay tail is empty, so only passing session_id=session_id excludes math1.
    async with db() as session, session.begin():
        await append_events(
            session,
            seeded_domain.learner_id,
            [
                new_event(
                    "task.assigned",
                    {
                        "session_id": str(session_id),
                        "turn_id": str(prior_turn_id),
                        "task_id": str(math1),
                        "topic_id": str(seeded_domain.topic_id),
                    },
                    session_id=session_id,
                    turn_id=prior_turn_id,
                    domain_id=seeded_domain.domain_id,
                ),
                new_event(
                    "task.submitted",
                    {
                        "session_id": str(session_id),
                        "turn_id": str(prior_turn_id),
                        "task_id": str(math1),
                        "answer": "x^2",
                        "hints_used": 0,
                    },
                    session_id=session_id,
                    turn_id=prior_turn_id,
                    domain_id=seeded_domain.domain_id,
                ),
            ],
            publish_wakeup=False,
        )
    await _project_all(neo4j, db, seeded_domain.learner_id)

    # 1. Normal turn with pedagogical_move="assign_task" and assign_task_id=None:
    # get_available_tasks must exclude math1 from teacher prompt, and pick_task must pick math2.
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Here is your next task.",
            pedagogical_move="assign_task",
            assign_task_id=None,
        ),
    )

    turn1_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Give me another task", "idempotency_key": "turn-sess-id-1"},
        headers=auth_headers,
    )
    assert turn1_resp.status_code == 200, turn1_resp.text

    teacher_calls = fake_llm.calls_for("teacher")
    assert len(teacher_calls) == 1
    assert str(math1) not in teacher_calls[0].prompt
    assert str(math2) in teacher_calls[0].prompt

    sse1 = dict(parse_sse(turn1_resp.text))
    assert UUID(sse1["task_assigned"]["task_id"]) == math2

    # Submit math2 as well and project so both math1 and math2 have status='submitted' in Neo4j,
    # leaving only choice_task_ids[0] unassigned in this session for degraded pick_task.
    choice_task_id = seeded_domain.choice_task_ids[0]
    turn1_id = UUID(sse1["turn_started"]["turn_id"])
    async with db() as session, session.begin():
        await append_events(
            session,
            seeded_domain.learner_id,
            [
                new_event(
                    "task.submitted",
                    {
                        "session_id": str(session_id),
                        "turn_id": str(turn1_id),
                        "task_id": str(math2),
                        "answer": "2*cos(2*x)",
                        "hints_used": 0,
                    },
                    session_id=session_id,
                    turn_id=turn1_id,
                    domain_id=seeded_domain.domain_id,
                ),
            ],
            publish_wakeup=False,
        )
    await _project_all(neo4j, db, seeded_domain.learner_id)

    # 2. Degraded turn (LLM unavailable): fallback pick_task must also pass session_id=session_id
    # and therefore skip math1 and math2, picking choice_task_id.
    fake_llm.unavailable = True
    turn2_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Another task please", "idempotency_key": "turn-sess-id-2"},
        headers=auth_headers,
    )
    assert turn2_resp.status_code == 200, turn2_resp.text
    sse2 = dict(parse_sse(turn2_resp.text))
    assert UUID(sse2["task_assigned"]["task_id"]) == choice_task_id
