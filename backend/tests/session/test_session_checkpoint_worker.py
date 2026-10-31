"""Worker tests for `session.checkpoint` (ТЗ §2.4.2, §5 worker test 4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text

from quack.config import FoldParams, Settings
from quack.db.postgres import SessionFactory
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import run_projector_batch
from quack.jobs import kinds
from quack.jobs.queue import ClaimedJob, claim_next_job, complete_job, enqueue_job
from quack.jobs.runner import JOB_HANDLERS, JobContext
from quack.session.schemas import CheckpointerOutput, TeacherOutput
from quack.session.workers import handle_session_checkpoint
from tests.support.resources import build_job_context

if TYPE_CHECKING:
    from tests.session.fixtures import FakeLLMProvider, SeededDomain

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


async def _invoke_checkpoint(
    job: ClaimedJob,
    ctx: JobContext,
    fake_llm: FakeLLMProvider,
) -> None:
    """Invoke the registered `session.checkpoint` handler; `ctx.llm` is `fake_llm.gateway`."""
    assert ctx.llm is fake_llm.gateway
    await handle_session_checkpoint(job, ctx)


async def test_session_checkpoint_worker_writes_checkpointed_and_updates_session(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
    settings: Settings,
    fake_llm: FakeLLMProvider,
) -> None:
    """ТЗ worker test 4: `session.checkpoint` folds replies up to `up_to_turn_index` via
    `checkpointer@1` (`idempotency_key=f"checkpoint:{session_id}:{up_to_turn_index}"`),
    appends `session.checkpointed` and updates `core.sessions.last_checkpoint_turn`, and on a
    subsequent checkpoint reads the previous `session.checkpointed`."""
    assert JOB_HANDLERS.get("session.checkpoint") is handle_session_checkpoint

    # 1. Start session
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-cp-worker-test",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])
    await _project_all(neo4j, db, seeded_domain.learner_id)

    # 2. Run 3 turns (6 replies: turn_index 0..5) so a session.checkpoint job is enqueued
    exchanges = [
        ("Student turn 1: what is the power rule?", "Teacher turn 1: d/dx x^n = n*x^(n-1)."),
        ("Student turn 2: so x^3 becomes 3x^2?", "Teacher turn 2: yes, exactly 3x^2."),
        ("Student turn 3: what about constants?", "Teacher turn 3: the derivative of c is 0."),
    ]
    for idx, (s_text, t_text) in enumerate(exchanges, start=1):
        fake_llm.push(
            "teacher",
            TeacherOutput(reply_text=t_text, pedagogical_move="explain"),
        )
        resp = await client.post(
            f"/v1/sessions/{session_id}/turns",
            json={"text": s_text, "idempotency_key": f"cp-worker-turn-{idx}"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        await _project_all(neo4j, db, seeded_domain.learner_id)

    # Claim the enqueued session.checkpoint job (up_to_turn_index == 5)
    async with db() as session, session.begin():
        cp_job_1 = await claim_next_job(session, "test-cp-worker", ["session.checkpoint"])
    assert cp_job_1 is not None
    assert cp_job_1.kind == "session.checkpoint"
    assert UUID(str(cp_job_1.payload["session_id"])) == session_id
    assert int(cp_job_1.payload["up_to_turn_index"]) == 5

    # 3. Run Turn 4 (turn_index 6 and 7) BEFORE running cp_job_1 to verify that
    # cp_job_1 only folds replies up to up_to_turn_index=5 and ignores replies 6..7.
    turn4_student = "Student turn 4 beyond first checkpoint boundary: chain rule question"
    turn4_teacher = "Teacher turn 4 beyond first checkpoint boundary: multiply by inner derivative"
    fake_llm.push(
        "teacher",
        TeacherOutput(reply_text=turn4_teacher, pedagogical_move="explain"),
    )
    resp4 = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": turn4_student, "idempotency_key": "cp-worker-turn-4"},
        headers=auth_headers,
    )
    assert resp4.status_code == 200, resp4.text

    # Before running the worker, last_checkpoint_turn is still -1
    async with db() as session:
        before_cp_turn = (
            await session.execute(
                text("SELECT last_checkpoint_turn FROM core.sessions WHERE id = :s"),
                {"s": session_id},
            )
        ).scalar_one()
    assert int(before_cp_turn) == -1

    # 4. Queue checkpointer@1 output for cp_job_1 (up_to_turn_index=5) and run the handler
    cp_out_1 = CheckpointerOutput(
        rolling_summary="Covered the power rule (x^3 -> 3x^2) and constant rule.",
        open_threads=["Practice power rule with negative exponents"],
        key_observations=["Correctly differentiated x^3", "Asked about constants"],
    )
    fake_llm.push("checkpointer", cp_out_1)

    ctx = build_job_context(settings, db, neo4j, redis, llm=fake_llm.gateway)
    await _invoke_checkpoint(cp_job_1, ctx, fake_llm)
    async with db() as session, session.begin():
        await complete_job(session, cp_job_1.id, "test-cp-worker")

    # Verify checkpointer@1 prompt received replies 0..5 and NOT replies 6..7
    cp_calls = fake_llm.calls_for("checkpointer")
    assert len(cp_calls) == 1
    prompt_1 = cp_calls[0].prompt
    for s_text, t_text in exchanges:
        assert s_text in prompt_1
        assert t_text in prompt_1
    assert turn4_student not in prompt_1
    assert turn4_teacher not in prompt_1

    # Verify LLM outbox recorded idempotency_key = f"checkpoint:{session_id}:5"
    async with db() as session:
        outbox_keys = (
            (
                await session.execute(
                    text(
                        "SELECT idempotency_key FROM core.llm_outbox "
                        "WHERE learner_id = :l AND role = 'checkpointer' ORDER BY created_at"
                    ),
                    {"l": seeded_domain.learner_id},
                )
            )
            .scalars()
            .all()
        )
    assert outbox_keys == [f"checkpoint:{session_id}:5"]

    # Verify session.checkpointed event written in core.events and core.sessions updated
    async with db() as session:
        cp_events = (
            await session.execute(
                text(
                    "SELECT seq, type, session_id, payload FROM core.events "
                    "WHERE learner_id = :l AND session_id = :s AND type = 'session.checkpointed' "
                    "ORDER BY seq"
                ),
                {"l": seeded_domain.learner_id, "s": session_id},
            )
        ).all()
        sess_row = (
            await session.execute(
                text(
                    "SELECT last_turn_index, last_checkpoint_turn FROM core.sessions WHERE id = :s"
                ),
                {"s": session_id},
            )
        ).one()

    assert len(cp_events) == 1
    ev1: Any = cp_events[0]
    assert ev1.session_id == session_id
    assert ev1.payload == {
        "session_id": str(session_id),
        "up_to_turn_index": 5,
        "rolling_summary": cp_out_1.rolling_summary,
        "open_threads": cp_out_1.open_threads,
        "key_observations": cp_out_1.key_observations,
    }
    assert int(sess_row.last_turn_index) == 7
    assert int(sess_row.last_checkpoint_turn) == 5

    # Verify GET /v1/sessions/{session_id} exposes the checkpoint and updated last_checkpoint_turn
    detail_resp = await client.get(f"/v1/sessions/{session_id}", headers=auth_headers)
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert detail["last_checkpoint_turn"] == 5
    assert detail["checkpoint"] == {
        "up_to_turn_index": 5,
        "rolling_summary": cp_out_1.rolling_summary,
        "open_threads": cp_out_1.open_threads,
        "key_observations": cp_out_1.key_observations,
    }

    # 5. Run a second checkpoint (up_to_turn_index = 7) to verify reading previous checkpoint
    async with db() as session, session.begin():
        await enqueue_job(
            session,
            kinds.session_checkpoint(
                seeded_domain.learner_id,
                session_id,
                {"session_id": str(session_id), "up_to_turn_index": 7},
            ),
        )
        cp_job_2 = await claim_next_job(session, "test-cp-worker", ["session.checkpoint"])
    assert cp_job_2 is not None
    assert int(cp_job_2.payload["up_to_turn_index"]) == 7

    cp_out_2 = CheckpointerOutput(
        rolling_summary="Covered power rule, constants, and started chain rule.",
        open_threads=["Chain rule practice problem"],
        key_observations=[
            "Correctly differentiated x^3",
            "Asked how chain rule applies to composite functions",
        ],
    )
    fake_llm.push("checkpointer", cp_out_2)

    await _invoke_checkpoint(cp_job_2, ctx, fake_llm)
    async with db() as session, session.begin():
        await complete_job(session, cp_job_2.id, "test-cp-worker")

    # Verify second checkpointer@1 call received previous_summary, previous_open_threads, and turn 4
    cp_calls = fake_llm.calls_for("checkpointer")
    assert len(cp_calls) == 2
    prompt_2 = cp_calls[1].prompt
    assert cp_out_1.rolling_summary in prompt_2
    assert "Practice power rule with negative exponents" in prompt_2
    assert turn4_student in prompt_2
    assert turn4_teacher in prompt_2

    # Verify second outbox key and second session.checkpointed event + updated last_checkpoint_turn
    async with db() as session:
        outbox_keys = (
            (
                await session.execute(
                    text(
                        "SELECT idempotency_key FROM core.llm_outbox "
                        "WHERE learner_id = :l AND role = 'checkpointer' ORDER BY created_at"
                    ),
                    {"l": seeded_domain.learner_id},
                )
            )
            .scalars()
            .all()
        )
        cp_events_after = (
            await session.execute(
                text(
                    "SELECT seq, payload FROM core.events "
                    "WHERE learner_id = :l AND session_id = :s AND type = 'session.checkpointed' "
                    "ORDER BY seq"
                ),
                {"l": seeded_domain.learner_id, "s": session_id},
            )
        ).all()
        last_cp_after = (
            await session.execute(
                text("SELECT last_checkpoint_turn FROM core.sessions WHERE id = :s"),
                {"s": session_id},
            )
        ).scalar_one()

    assert set(outbox_keys) == {
        f"checkpoint:{session_id}:5",
        f"checkpoint:{session_id}:7",
    }
    assert len(cp_events_after) == 2
    assert cp_events_after[1].seq > cp_events_after[0].seq
    assert cp_events_after[1].payload == {
        "session_id": str(session_id),
        "up_to_turn_index": 7,
        "rolling_summary": cp_out_2.rolling_summary,
        "open_threads": cp_out_2.open_threads,
        "key_observations": cp_out_2.key_observations,
    }
    assert int(last_cp_after) == 7
