"""Worker test for `turn.extract` (ТЗ §2.4.1, §5 worker test 3)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import orjson
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import session_events_channel
from quack.jobs import kinds
from quack.jobs.queue import claim_next_job, complete_job, enqueue_job
from quack.session.schemas import ExtractorEntry, ExtractorOutputV2, TeacherOutput
from quack.session.workers import run_turn_extract
from tests.session.helpers import parse_sse, skill_label
from tests.support.dispatch import project_and_dispatch
from tests.support.resources import build_job_context

pytest_plugins = ["tests.session.fixtures"]


async def test_turn_extract_worker_writes_event_publishes_and_supersedes_on_rerun(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: Any,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
    settings: Settings,
    fake_llm: Any,
    pg_engine: AsyncEngine,
) -> None:
    """ТЗ worker test 3: turn.extract writes turn.extracted, publishes extractor_done to
    session_events_channel(session_id), and on rerun emits evidence.superseded on the old seq."""
    # 1. Start session and run a turn to produce turn replies and turn.extract job
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-extract-test",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])

    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Remember to multiply by the exponent and subtract 1.",
            pedagogical_move="explain",
            focus_skill_labels=[skill_label(seeded_domain.skill_ids, seeded_domain.skill_ids[0])],
        ),
    )

    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={
            "text": "The derivative of x^3 is 3x^3 because I bring down the 3.",
            "idempotency_key": "turn-extract-1",
        },
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200, turn_resp.text
    sse_events = dict(parse_sse(turn_resp.text))
    turn_id = UUID(sse_events["turn_started"]["turn_id"])

    await project_and_dispatch(settings, db, pg_engine, neo4j, seeded_domain.learner_id)  # §8.9 п.7
    # 2. Claim the enqueued turn.extract job
    async with db() as session, session.begin():
        job = await claim_next_job(session, "test-worker", ["turn.extract"])
    assert job is not None
    assert job.kind == "turn.extract"
    assert UUID(job.payload["turn_id"]) == turn_id

    # Subscribe to session_events_channel before running the worker
    channel = session_events_channel(session_id)
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    await pubsub.get_message(timeout=1.0)

    # 3. Queue extractor@2 output and run the worker (§8.9 п.9: categorical elements)
    student_seq = int(sse_events["turn_started"]["user_seq"])
    power = skill_label(seeded_domain.skill_ids, seeded_domain.skill_ids[0])
    output = ExtractorOutputV2(
        entries=[
            ExtractorEntry(
                kind="skill_observation",
                skill=power,
                misconception=None,
                level=3,
                outcome="failure",
                help="none",
                gap_type="execution_error",
                quote="The derivative of x^3 is 3x^3",
                events=[student_seq],
            ),
            ExtractorEntry(
                kind="misconception_suspicion",
                skill=power,
                misconception="M1",
                level=None,
                outcome=None,
                help=None,
                gap_type=None,
                quote="3x^3 because I bring down the 3",
                events=[student_seq],
            ),
        ]
    )
    fake_llm.push("extractor", output)

    assert fake_llm.gateway is not None
    ctx = build_job_context(settings, db, neo4j, redis, llm=fake_llm.gateway)
    first_seq = await run_turn_extract(job, ctx, gateway=fake_llm.gateway, redis=redis, neo4j=neo4j)
    async with db() as session, session.begin():
        await complete_job(session, job.id, "test-worker")

    # The prompt carries the numbered episode, candidate skills and known misconceptions by
    # labels (Т-§4.5: no ids), the learner's and the teacher's text.
    extractor_calls = fake_llm.calls_for("extractor")
    assert len(extractor_calls) == 1
    prompt_text = extractor_calls[0].prompt
    assert str(seeded_domain.topic_id) not in prompt_text
    assert str(seeded_domain.skill_ids[0]) not in prompt_text
    assert f"{power}: Power rule" in prompt_text
    assert f"M1 (skill {power}): Forgets to lower the exponent" in prompt_text
    assert f"[{student_seq}] learner: The derivative of x^3 is 3x^3" in prompt_text
    assert "teacher: Remember to multiply by the exponent" in prompt_text

    # Verify LLM outbox used idempotency_key = f"extract:{turn_id}:v2"
    async with db() as session:
        outbox_key = (
            await session.execute(
                text(
                    "SELECT idempotency_key FROM core.llm_outbox "
                    "WHERE learner_id = :l AND role = 'extractor'"
                ),
                {"l": seeded_domain.learner_id},
            )
        ).scalar_one()
    assert outbox_key == f"extract:{turn_id}:v2"

    # 4. Verify turn.extracted v2 written in core.events
    async with db() as session:
        extracted_rows = (
            await session.execute(
                text(
                    "SELECT seq, type, actor, schema_version, session_id, turn_id, payload "
                    "FROM core.events WHERE learner_id = :l "
                    "AND type IN ('turn.extracted', 'evidence.superseded') ORDER BY seq"
                ),
                {"l": seeded_domain.learner_id},
            )
        ).all()
    assert len(extracted_rows) == 1
    first_extracted = extracted_rows[0]
    assert first_extracted.type == "turn.extracted"
    assert first_extracted.seq == first_seq
    assert first_extracted.actor == "extractor@2"
    assert first_extracted.schema_version == 2
    assert first_extracted.session_id == session_id
    assert first_extracted.turn_id == turn_id
    assert first_extracted.payload["turn_id"] == str(turn_id)
    elements = first_extracted.payload["elements"]
    assert [e["kind"] for e in elements] == ["evidence", "misconception_hypothesis"]
    assert elements[0]["outcome"] == "incorrect"
    assert elements[0]["skill"]["skill_id"] == str(seeded_domain.skill_ids[0])
    assert elements[1]["misconception_id"] == str(seeded_domain.misconception_id)
    assert all(e["rejected_reason"] is None for e in elements)

    # 5. Verify extractor_done published on session_events_channel(session_id)
    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2.0)
    assert msg is not None
    published = orjson.loads(msg["data"])
    assert published == {
        "event": "extractor_done",
        "data": {"turn_id": str(turn_id), "seq": first_seq},
    }

    # 6. Rerun turn.extract for the same turn_id -> emits evidence.superseded(first_seq)
    # and a new turn.extracted in the same append_events call
    async with db() as session, session.begin():
        await enqueue_job(
            session,
            kinds.turn_extract(
                seeded_domain.learner_id,
                session_id,
                dict(job.payload),
                required_seq=None,
            ),
        )
        rerun_job = await claim_next_job(session, "test-worker", ["turn.extract"])
    assert rerun_job is not None

    second_seq = await run_turn_extract(
        rerun_job, ctx, gateway=fake_llm.gateway, redis=redis, neo4j=neo4j
    )
    assert second_seq > first_seq

    async with db() as session:
        after_rows = (
            await session.execute(
                text(
                    "SELECT seq, type, payload FROM core.events "
                    "WHERE learner_id = :l AND seq >= :s ORDER BY seq"
                ),
                {"l": seeded_domain.learner_id, "s": first_seq},
            )
        ).all()
    assert [r.type for r in after_rows] == [
        "turn.extracted",
        "evidence.superseded",
        "turn.extracted",
    ]
    superseded_row = after_rows[1]
    new_extracted_row = after_rows[2]
    assert superseded_row.seq == second_seq - 1
    assert new_extracted_row.seq == second_seq
    assert superseded_row.payload["target_event_seq"] == first_seq
    assert superseded_row.payload["reason"] == "re-extracted"
    assert new_extracted_row.payload["turn_id"] == str(turn_id)

    # Channel also receives the new extractor_done notification
    msg2 = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2.0)
    assert msg2 is not None
    assert orjson.loads(msg2["data"]) == {
        "event": "extractor_done",
        "data": {"turn_id": str(turn_id), "seq": second_seq},
    }
    await pubsub.aclose()
