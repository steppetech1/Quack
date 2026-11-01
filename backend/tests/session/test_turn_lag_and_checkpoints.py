"""Lagging projector and checkpoint enqueuing tests (ТЗ §2.3.2, §2.3.5, §2.3.6.3)."""

from __future__ import annotations

from uuid import UUID

import httpx
from neo4j import AsyncDriver
from sqlalchemy import text

from quack.config import FoldParams, Settings
from quack.db.postgres import SessionFactory
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import run_projector_batch
from quack.session.schemas import TeacherOutput
from tests.session.fixtures import SeededDomain
from tests.session.helpers import parse_sse, task_label
from tests.support.dispatch import dispatch

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


async def test_second_turn_over_lagging_projector_does_not_reassign_task(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    settings: Settings,
    fake_llm,
) -> None:
    """ТЗ turns test 4: With appliedSeq = 0, turn 2 does not re-assign task and returns lag."""
    # Set small projector lag timeout so tests don't wait 2 seconds
    settings.projector_lag_timeout_ms = 50

    # 1. Start an active session
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-turn-lag",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])

    # Force Neo4j appliedSeq = 0 so projector is lagging behind PostgreSQL event log
    async with neo4j.session() as s:
        await s.run(
            "MATCH (l:Learner {id: $id}) SET l.appliedSeq = 0",
            {"id": str(seeded_domain.learner_id)},
        )

    # 2. First turn: LLM assigns first math task
    first_task_id = seeded_domain.math_task_ids[0]
    # WP14.1: labels live for one call; turn 2 repeats the label of turn 1, which no longer
    # names the first task (it is not offered again).
    first_label = await task_label(
        db, seeded_domain.learner_id, seeded_domain.topic_id, session_id, first_task_id
    )
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Here is your first problem.",
            pedagogical_move="assign_task",
            assign_task_label=first_label,
        ),
    )

    resp1 = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "I want to start", "idempotency_key": "turn-lag-1"},
        headers=auth_headers,
    )
    assert resp1.status_code == 200, resp1.text
    events1 = parse_sse(resp1.text)
    event_dict1 = {name: data for name, data in events1}

    assert "task_assigned" in event_dict1
    assert UUID(event_dict1["task_assigned"]["task_id"]) == first_task_id
    assert event_dict1["turn_completed"]["degraded_mode"] == "projector_lag"

    # Verify Neo4j still has appliedSeq = 0
    async with neo4j.session() as s:
        result = await s.run(
            "MATCH (l:Learner {id: $id}) RETURN l.appliedSeq AS seq",
            {"id": str(seeded_domain.learner_id)},
        )
        rec = await result.single()
        assert rec is not None and rec["seq"] == 0

    # 3. Second turn: LLM tries to assign the same task again
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Let's do another problem.",
            pedagogical_move="assign_task",
            assign_task_label=first_label,
        ),
    )

    resp2 = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Give me another problem", "idempotency_key": "turn-lag-2"},
        headers=auth_headers,
    )
    assert resp2.status_code == 200, resp2.text
    events2 = parse_sse(resp2.text)
    event_dict2 = {name: data for name, data in events2}

    assert "task_assigned" in event_dict2
    # Crucial assertion: the second turn must NOT re-assign the task of the first turn!
    assigned_id_2 = UUID(event_dict2["task_assigned"]["task_id"])
    assert assigned_id_2 != first_task_id
    # And degraded_mode must be projector_lag
    assert event_dict2["turn_completed"]["degraded_mode"] == "projector_lag"

    # 4. Check core.events: second task.assigned has assigned_id_2
    async with db() as session:
        assigned_events = (
            (
                await session.execute(
                    text(
                        "SELECT payload->>'task_id' as tid FROM core.events "
                        "WHERE learner_id = :l AND session_id = :s AND type = 'task.assigned' "
                        "ORDER BY seq"
                    ),
                    {"l": seeded_domain.learner_id, "s": session_id},
                )
            )
            .scalars()
            .all()
        )
    assert [UUID(tid) for tid in assigned_events] == [first_task_id, assigned_id_2]


async def test_checkpoint_job_enqueued_on_sixth_reply_and_coalesced(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm,
    settings: Settings,
) -> None:
    """ТЗ turns test 5: Checkpoint job on 6th reply and coalesced to one queued row in ops.jobs."""
    # 1. Start an active session
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-turn-cp",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    # 2. Run Turn 1 (replies 0 and 1) -> 2 replies
    fake_llm.push(
        "teacher",
        TeacherOutput(reply_text="Reply 1", pedagogical_move="explain"),
    )
    resp1 = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Turn 1 text", "idempotency_key": "turn-cp-1"},
        headers=auth_headers,
    )
    assert resp1.status_code == 200
    await _project_all(neo4j, db, seeded_domain.learner_id)

    async with db() as session:
        jobs = (
            await session.execute(
                text(
                    "SELECT id, kind, status FROM ops.jobs "
                    "WHERE learner_id = :l AND kind = 'session.checkpoint'"
                ),
                {"l": seeded_domain.learner_id},
            )
        ).all()
    assert len(jobs) == 0

    # 3. Run Turn 2 (replies 2 and 3) -> 4 replies
    fake_llm.push(
        "teacher",
        TeacherOutput(reply_text="Reply 2", pedagogical_move="explain"),
    )
    resp2 = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Turn 2 text", "idempotency_key": "turn-cp-2"},
        headers=auth_headers,
    )
    assert resp2.status_code == 200
    await _project_all(neo4j, db, seeded_domain.learner_id)

    async with db() as session:
        jobs = (
            await session.execute(
                text(
                    "SELECT id, kind, status FROM ops.jobs "
                    "WHERE learner_id = :l AND kind = 'session.checkpoint'"
                ),
                {"l": seeded_domain.learner_id},
            )
        ).all()
    assert len(jobs) == 0

    # 4. Run Turn 3 (replies 4 and 5) -> 6 replies! Checkpoint should be enqueued!
    fake_llm.push(
        "teacher",
        TeacherOutput(reply_text="Reply 3", pedagogical_move="explain"),
    )
    resp3 = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Turn 3 text", "idempotency_key": "turn-cp-3"},
        headers=auth_headers,
    )
    assert resp3.status_code == 200
    await _project_all(neo4j, db, seeded_domain.learner_id)

    await dispatch(settings, db, seeded_domain.learner_id)  # §8.9 п.7 (WP11.4)
    async with db() as session:
        jobs = (
            await session.execute(
                text(
                    "SELECT id, kind, status, priority, lock_key, coalesce_key, payload "
                    "FROM ops.jobs WHERE learner_id = :l AND kind = 'session.checkpoint'"
                ),
                {"l": seeded_domain.learner_id},
            )
        ).all()
    assert len(jobs) == 1
    cp_job = jobs[0]
    assert cp_job.status == "queued"
    assert cp_job.priority == 20
    assert cp_job.coalesce_key == f"checkpoint:{session_id}"
    assert cp_job.lock_key == f"session:{session_id}:checkpoint"
    assert cp_job.payload["session_id"] == str(session_id)
    assert cp_job.payload["up_to_turn_index"] == 5

    # 5. Run Turn 4 (replies 6 and 7) -> 8 replies, without the checkpoint worker running.
    # The new checkpoint job should coalesce with the existing queued job into ONE row.
    fake_llm.push(
        "teacher",
        TeacherOutput(reply_text="Reply 4", pedagogical_move="explain"),
    )
    resp4 = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Turn 4 text", "idempotency_key": "turn-cp-4"},
        headers=auth_headers,
    )
    assert resp4.status_code == 200
    await dispatch(settings, db, seeded_domain.learner_id)  # §8.9 п.7 (WP11.4)

    async with db() as session:
        jobs_after = (
            await session.execute(
                text(
                    "SELECT id, kind, status, priority, coalesce_key, payload FROM ops.jobs "
                    "WHERE learner_id = :l AND kind = 'session.checkpoint'"
                ),
                {"l": seeded_domain.learner_id},
            )
        ).all()
    assert len(jobs_after) == 1
    updated_cp_job = jobs_after[0]
    assert updated_cp_job.id == cp_job.id
    assert updated_cp_job.status == "queued"
    assert updated_cp_job.payload["up_to_turn_index"] == 7
