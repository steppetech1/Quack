"""Tests for T-030 (S-4.4): every task.submitted gets a task.evaluated."""

from __future__ import annotations

from uuid import UUID

import httpx
from neo4j import AsyncDriver
from sqlalchemy import text

from quack.compute_client.client import ComputeUnavailableError
from quack.config import FoldParams
from quack.db.postgres import SessionFactory
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import run_projector_batch
from quack.session.schemas import TeacherOutput
from tests.session.fixtures import SeededDomain, _task_generated
from tests.session.helpers import parse_sse

pytest_plugins = ["tests.session.fixtures"]


async def _project_all(neo4j: AsyncDriver, db: SessionFactory, learner_id: UUID) -> None:
    async with db() as session:
        result = await session.execute(
            text(f"SELECT {EVENT_COLUMNS} FROM core.events WHERE learner_id = :l ORDER BY seq"),
            {"l": learner_id},
        )
        events = [row_to_event(r) for r in result.all()]
    store = Neo4jGraphStore(neo4j, str(learner_id))
    outcome = await run_projector_batch(learner_id, events, store, FoldParams())
    assert outcome.applied_seq == events[-1].seq


async def test_open_task_evaluated_via_llm_teacher_fields(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm,
) -> None:
    # Seed an open task
    open_task_id = UUID("11111111-2222-3333-4444-555555555555")
    async with db() as session, session.begin():
        await append_events(
            session,
            seeded_domain.learner_id,
            [
                NewEvent(
                    type="task.generated",
                    payload=_task_generated(
                        seeded_domain,
                        open_task_id,
                        "open",
                        "open-fam-1",
                        seeded_domain.skill_ids[0],
                        prompt_text="Explain why derivative of e^x is e^x",
                        reference_solution={"notes": "any valid explanation"},
                    ),
                    actor="code",
                    idempotency_key="open-task-gen-1",
                    domain_id=seeded_domain.domain_id,
                )
            ],
            publish_wakeup=False,
        )

    # Start session
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-open-eval-1",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    # Teacher returns evaluated_score and evaluated_is_correct
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Great explanation!",
            pedagogical_move="give_feedback",
            evaluated_score=0.9,
            evaluated_is_correct=True,
            evaluated_feedback="Well reasoned answer",
            focus_skill_ids=[seeded_domain.skill_ids[0]],
        ),
    )

    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={
            "text": "Because the slope matches the function value at all points.",
            "related_task_id": str(open_task_id),
            "idempotency_key": "turn-open-eval-1",
        },
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200

    sse_events = parse_sse(turn_resp.text)
    event_names = [n for n, _ in sse_events]
    assert "task_evaluated" in event_names
    task_eval_sse = next(d for n, d in sse_events if n == "task_evaluated")
    assert task_eval_sse["task_id"] == str(open_task_id)
    assert task_eval_sse["is_correct"] is True
    assert task_eval_sse["score"] == 0.9

    async with db() as session:
        eval_row = (
            await session.execute(
                text(
                    "SELECT payload, actor FROM core.events "
                    "WHERE session_id = :s AND type = 'task.evaluated'"
                ),
                {"s": session_id},
            )
        ).one_or_none()
    assert eval_row is not None
    assert eval_row.payload["evaluator"] == "llm"
    assert eval_row.payload["score"] == 0.9
    assert eval_row.payload["is_correct"] is True


async def test_compute_outage_fallback_evaluates_task(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm,
    fake_compute,
) -> None:
    math_task_id = seeded_domain.math_task_ids[0]

    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-compute-outage-1",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    fake_compute.push(ComputeUnavailableError("compute server down"))
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Got your answer.",
            pedagogical_move="give_feedback",
            # evaluated_* left as None
        ),
    )

    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={
            "text": "3*x^2",
            "related_task_id": str(math_task_id),
            "idempotency_key": "turn-compute-outage-1",
        },
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200

    sse_events = parse_sse(turn_resp.text)
    event_names = [n for n, _ in sse_events]
    assert "task_evaluated" in event_names

    async with db() as session:
        eval_rows = (
            await session.execute(
                text(
                    "SELECT payload, actor FROM core.events "
                    "WHERE session_id = :s AND type = 'task.evaluated'"
                ),
                {"s": session_id},
            )
        ).all()
    assert len(eval_rows) == 1
    assert eval_rows[0].payload["evaluator"] == "compute"


async def test_diagnostic_shape_choice_task_evaluated_correct(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm,
) -> None:
    diag_choice_id = UUID("22222222-3333-4444-5555-666666666666")
    async with db() as session, session.begin():
        await append_events(
            session,
            seeded_domain.learner_id,
            [
                NewEvent(
                    type="task.generated",
                    payload=_task_generated(
                        seeded_domain,
                        diag_choice_id,
                        "choice",
                        "diag-choice-fam-1",
                        seeded_domain.skill_ids[0],
                        prompt_text="Choose B",
                        reference_solution={
                            "option": "B",
                            "answer": "B",
                            "options": ["A", "B", "C"],
                        },
                        difficulty=0.3,
                    ),
                    actor="code",
                    idempotency_key="diag-choice-gen-1",
                    domain_id=seeded_domain.domain_id,
                )
            ],
            publish_wakeup=False,
        )

    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-diag-choice-eval-1",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="You picked b.",
            pedagogical_move="give_feedback",
        ),
    )

    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={
            "text": "b",
            "related_task_id": str(diag_choice_id),
            "idempotency_key": "turn-diag-choice-1",
        },
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200

    sse_events = parse_sse(turn_resp.text)
    task_eval_sse = next(d for n, d in sse_events if n == "task_evaluated")
    assert task_eval_sse["is_correct"] is True
    assert task_eval_sse["score"] == 1.0
