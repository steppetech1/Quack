"""Tests for T-031 (S-5.6): actor='code' in events during deterministic fallback."""

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
from quack.llm.errors import LLMUnavailableError
from quack.session.schemas import TeacherOutput
from tests.session.fixtures import SeededDomain

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


async def test_degraded_llm_mode_uses_actor_code(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm,
) -> None:
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-degraded-actor-1",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    # Provider raises LLMUnavailableError
    fake_llm.push("teacher", LLMUnavailableError("simulated provider downtime"))

    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={
            "text": "Hello, need help.",
            "idempotency_key": "turn-degraded-actor-1",
        },
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200

    async with db() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT type, actor, payload FROM core.events "
                    "WHERE session_id = :s ORDER BY seq"
                ),
                {"s": session_id},
            )
        ).all()

    teacher_replied = next(r for r in rows if r.type == "turn.teacher_replied")
    assert teacher_replied.actor == "code"
    assert teacher_replied.payload["degraded_mode"] == "llm_unavailable"

    task_assigned = next((r for r in rows if r.type == "task.assigned"), None)
    if task_assigned is not None:
        assert task_assigned.actor == "code"


async def test_normal_mode_keeps_actor_teacher_1(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm,
) -> None:
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-normal-actor-1",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Normal response.",
            pedagogical_move="explain",
            focus_skill_ids=[seeded_domain.skill_ids[0]],
        ),
    )

    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={
            "text": "Explain derivative",
            "idempotency_key": "turn-normal-actor-1",
        },
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200

    async with db() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT type, actor, payload FROM core.events "
                    "WHERE session_id = :s ORDER BY seq"
                ),
                {"s": session_id},
            )
        ).all()

    teacher_replied = next(r for r in rows if r.type == "turn.teacher_replied")
    assert teacher_replied.actor == "teacher@1"
    assert teacher_replied.payload.get("degraded_mode") is None
