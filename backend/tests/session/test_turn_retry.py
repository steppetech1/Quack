"""Tests for S-1: idempotent retry of POST /v1/sessions/{id}/turns without IDEMPOTENCY_CONFLICT."""

from __future__ import annotations

from uuid import UUID, uuid5

import httpx
from neo4j import AsyncDriver
from sqlalchemy import text

from quack.config import FoldParams
from quack.db.postgres import SessionFactory
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.ids import QUACK_UUID_NS
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import run_projector_batch
from quack.session.schemas import TeacherOutput
from tests.session.fixtures import SeededDomain
from tests.session.helpers import parse_sse, skill_label

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


async def test_turn_idempotent_retry_returns_200_and_calls_llm_once(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm,
) -> None:
    # 1. Start active session
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-turn-retry-1",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    # 2. Push fake teacher reply
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Hello, student! Let's explore derivatives.",
            pedagogical_move="explain",
            focus_skill_labels=[skill_label(seeded_domain.skill_ids, seeded_domain.skill_ids[0])],
        ),
    )

    turn_key = "retry-turn-key-1"
    turn_body = {
        "text": "What is a derivative?",
        "idempotency_key": turn_key,
    }

    # 3. First turn request
    resp1 = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json=turn_body,
        headers=auth_headers,
    )
    assert resp1.status_code == 200
    events1 = parse_sse(resp1.text)
    event_names1 = [n for n, _ in events1]
    turn_started1 = events1[0][1]
    turn_id1 = turn_started1["turn_id"]

    expected_turn_id = str(
        uuid5(QUACK_UUID_NS, f"turn:{seeded_domain.learner_id}:{session_id}:{turn_key}")
    )
    assert turn_id1 == expected_turn_id
    assert len(fake_llm.calls_for("teacher")) == 1

    # Count events and jobs
    async with db() as session:
        events_cnt_1 = (
            await session.execute(
                text("SELECT count(*) FROM core.events WHERE session_id = :s"),
                {"s": session_id},
            )
        ).scalar()
        jobs_cnt_1 = (
            await session.execute(
                text("SELECT count(*) FROM ops.jobs WHERE kind = 'turn.extract'"),
            )
        ).scalar()

    # 4. Repeated turn request with identical body and idempotency key
    resp2 = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json=turn_body,
        headers=auth_headers,
    )
    assert resp2.status_code == 200
    events2 = parse_sse(resp2.text)
    event_names2 = [n for n, _ in events2]
    turn_started2 = events2[0][1]
    turn_id2 = turn_started2["turn_id"]

    assert turn_id2 == turn_id1
    assert event_names2 == event_names1
    # LLM not called a second time
    assert len(fake_llm.calls_for("teacher")) == 1

    # Event count and jobs count unchanged
    async with db() as session:
        events_cnt_2 = (
            await session.execute(
                text("SELECT count(*) FROM core.events WHERE session_id = :s"),
                {"s": session_id},
            )
        ).scalar()
        jobs_cnt_2 = (
            await session.execute(
                text("SELECT count(*) FROM ops.jobs WHERE kind = 'turn.extract'"),
            )
        ).scalar()

    assert events_cnt_2 == events_cnt_1
    assert jobs_cnt_2 == jobs_cnt_1

    # 5. Repeated turn request with different text -> 409 IDEMPOTENCY_CONFLICT
    conflict_body = {
        "text": "Different question?",
        "idempotency_key": turn_key,
    }
    resp3 = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json=conflict_body,
        headers=auth_headers,
    )
    assert resp3.status_code == 409
    assert resp3.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
