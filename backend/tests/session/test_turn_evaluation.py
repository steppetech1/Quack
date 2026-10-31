"""In-turn task evaluation tests (ТЗ §2.3.4, §5 turns test 2)."""

from __future__ import annotations

from uuid import UUID

import httpx
from neo4j import AsyncDriver
from sqlalchemy import text

from quack.config import FoldParams
from quack.db.postgres import SessionFactory
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import run_projector_batch
from quack.practice.evaluation import FEEDBACK_CORRECT
from quack.session.schemas import TeacherOutput
from tests.session.fixtures import SeededDomain
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


async def test_math_task_evaluated_in_turn_via_compute(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm,
    fake_compute,
) -> None:
    """ТЗ turns test 2: Math task evaluated through fake_compute with SSE task_evaluated."""
    # 1. Start an active session
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-turn-eval",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    math_task_id = seeded_domain.math_task_ids[0]

    # 2. Configure fake_compute and fake_llm
    fake_compute.push_result(True)
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Spot on! 3x^2 is the derivative of x^3.",
            pedagogical_move="give_feedback",
            focus_skill_ids=[seeded_domain.skill_ids[0]],
        ),
    )

    # 3. Post turn answering the math task
    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={
            "text": "3*x^2",
            "related_task_id": str(math_task_id),
            "hints_used": 1,
            "idempotency_key": "turn-eval-1",
        },
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200, turn_resp.text

    # 4. Verify compute client was called with expected expression
    assert len(fake_compute.requests) == 1
    assert fake_compute.requests[0].student_expr == "3*x^2"
    assert fake_compute.requests[0].reference_expr == "3*x**2"

    # 5. Verify teacher prompt received task_evaluation_json
    teacher_calls = fake_llm.calls_for("teacher")
    assert len(teacher_calls) == 1
    assert str(math_task_id) in teacher_calls[0].prompt
    assert '"evaluator": "compute"' in teacher_calls[0].prompt
    assert '"is_correct": true' in teacher_calls[0].prompt

    # 6. Check SSE sequence and task_evaluated payload
    sse_events = parse_sse(turn_resp.text)
    event_names = [name for name, _ in sse_events]
    assert event_names == ["turn_started", "token", "task_evaluated", "turn_completed", "done"]

    turn_started = sse_events[0][1]
    turn_id = UUID(turn_started["turn_id"])

    task_eval_sse = sse_events[2][1]
    # D-B-006: feedback of a code verdict comes from the one cascade of quack.practice.
    assert task_eval_sse == {
        "task_id": str(math_task_id),
        "is_correct": True,
        "score": 1.0,
        "feedback": FEEDBACK_CORRECT,
    }

    # 7. Check core.events has task.submitted and task.evaluated with evaluator="compute"
    async with db() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT seq, type, actor, payload FROM core.events "
                    "WHERE learner_id = :l AND session_id = :s AND turn_id = :t ORDER BY seq"
                ),
                {"l": seeded_domain.learner_id, "s": session_id, "t": turn_id},
            )
        ).all()

    types = [r.type for r in rows]
    assert types == [
        "turn.student_replied",
        "task.submitted",
        "task.evaluated",
        "turn.teacher_replied",
    ]

    submitted_row = rows[1]
    assert submitted_row.actor == "learner"
    assert UUID(submitted_row.payload["task_id"]) == math_task_id
    assert submitted_row.payload["answer"] == "3*x^2"
    assert submitted_row.payload["hints_used"] == 1

    evaluated_row = rows[2]
    assert evaluated_row.actor == "code"
    assert UUID(evaluated_row.payload["task_id"]) == math_task_id
    assert evaluated_row.payload["evaluator"] == "compute"
    assert evaluated_row.payload["is_correct"] is True
    assert evaluated_row.payload["score"] == 1.0
    # D-B-006: `hints_used` from the client without a `task.hint_given` costs nothing.
    assert evaluated_row.payload["independence"] == 1.0
    assert evaluated_row.payload["feedback"] == FEEDBACK_CORRECT
    assert len(evaluated_row.payload["skill_scores"]) == 1
    assert UUID(evaluated_row.payload["skill_scores"][0]["skill_id"]) == seeded_domain.skill_ids[0]
    assert evaluated_row.payload["skill_scores"][0]["score"] == 1.0
