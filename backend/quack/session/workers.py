"""Background handlers `turn.extract` and `session.checkpoint` (ТЗ §2.4)."""

from __future__ import annotations

import contextlib
import json
from typing import Any
from uuid import UUID

import orjson
from neo4j import AsyncDriver
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text

from quack.db.neo4j import create_driver
from quack.db.redis import create_redis, session_events_channel
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.jobs.queue import ClaimedJob
from quack.jobs.runner import JobContext, register_job_handler
from quack.llm.gateway import LLMGateway
from quack.llm.provider import HttpLLMProvider
from quack.logging import get_logger
from quack.session.context import TURN_EVENT_TYPES, _topic_slice, last_checkpoint
from quack.session.schemas import CheckpointerOutput, ExtractorOutput

log = get_logger(__name__)


async def _read_topic_context(
    neo4j: AsyncDriver, learner_id: UUID, topic_id: UUID | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if topic_id is None:
        return [], []
    store = Neo4jGraphStore(neo4j, str(learner_id))
    graph = await store.dump()
    _, skills, misconceptions = _topic_slice(graph, topic_id)
    return skills, misconceptions


async def run_turn_extract(
    job: ClaimedJob,
    ctx: JobContext,
    *,
    gateway: LLMGateway,
    redis: Redis | None = None,
    neo4j: AsyncDriver | None = None,
) -> int:
    """Execute `turn.extract` for `job`; returns the committed `seq` of `turn.extracted`."""
    payload = job.payload
    session_id = UUID(str(payload["session_id"]))
    turn_id = UUID(str(payload["turn_id"]))
    student_seq = payload.get("student_seq")
    teacher_seq = payload.get("teacher_seq")

    async with ctx.session_factory() as session:
        if job.learner_id is not None:
            learner_id = job.learner_id
            sess_row = (
                await session.execute(
                    text(
                        "SELECT domain_id, topic_id FROM core.sessions "
                        "WHERE id = :s AND learner_id = :l"
                    ),
                    {"s": session_id, "l": learner_id},
                )
            ).one()
        else:
            sess_row = (
                await session.execute(
                    text("SELECT learner_id, domain_id, topic_id FROM core.sessions WHERE id = :s"),
                    {"s": session_id},
                )
            ).one()
            learner_id = sess_row.learner_id

        domain_id: UUID = sess_row.domain_id
        topic_id: UUID | None = sess_row.topic_id

        if student_seq is not None and teacher_seq is not None:
            reply_rows = (
                await session.execute(
                    text(
                        "SELECT seq, type, payload FROM core.events "
                        "WHERE learner_id = :l AND seq IN (:s_seq, :t_seq) ORDER BY seq"
                    ),
                    {"l": learner_id, "s_seq": int(student_seq), "t_seq": int(teacher_seq)},
                )
            ).all()
        else:
            reply_rows = (
                await session.execute(
                    text(
                        "SELECT seq, type, payload FROM core.events "
                        "WHERE learner_id = :l AND session_id = :s AND turn_id = :t "
                        "AND type IN ('turn.student_replied', 'turn.teacher_replied') ORDER BY seq"
                    ),
                    {"l": learner_id, "s": session_id, "t": turn_id},
                )
            ).all()

    student_text = ""
    teacher_text = ""
    for r in reply_rows:
        if r.type == "turn.student_replied":
            student_text = str(r.payload.get("text", ""))
        elif r.type == "turn.teacher_replied":
            teacher_text = str(r.payload.get("text", ""))

    own_driver = neo4j is None
    driver = neo4j if neo4j is not None else create_driver(ctx.settings)
    try:
        skills, misconceptions = await _read_topic_context(driver, learner_id, topic_id)
    finally:
        if own_driver:
            await driver.close()

    variables = {
        "topic_id": str(topic_id) if topic_id else "",
        "known_skills_json": json.dumps(skills, ensure_ascii=False),
        "known_misconceptions_json": json.dumps(misconceptions, ensure_ascii=False),
        "student_text": student_text,
        "teacher_text": teacher_text,
    }

    llm_result = await gateway.generate_structured(
        role="extractor",
        prompt_version=1,
        response_model=ExtractorOutput,
        variables=variables,
        learner_id=learner_id,
        session_id=session_id,
        turn_id=turn_id,
        idempotency_key=f"extract:{turn_id}:v1",
    )
    output = llm_result.value

    # Law 4 (Т-§7.5-7.6): keep only items whose quote is literally in the student's reply.
    student_lower = student_text.lower()

    def _grounded(evidence_text: str) -> bool:
        quote = evidence_text.strip().lower()
        if quote in student_lower or quote == "".join(student_lower.split()):
            return True
        log.warning(
            "extractor_ungrounded_quote_dropped", turn_id=str(turn_id), evidence_text=evidence_text
        )
        return False

    observations = [o for o in output.observations if _grounded(o.evidence_text)]
    misconceptions_out = [m for m in output.misconceptions if _grounded(m.evidence_text)]

    async with ctx.session_factory() as session, session.begin():
        old_rows = (
            await session.execute(
                text(
                    "SELECT seq FROM core.events "
                    "WHERE learner_id = :l AND type = 'turn.extracted' "
                    "AND (turn_id = :t OR (payload->>'turn_id')::uuid = :t) "
                    "ORDER BY seq DESC LIMIT 1"
                ),
                {"l": learner_id, "t": turn_id},
            )
        ).first()

        events_to_append: list[NewEvent] = []
        if old_rows is not None:
            old_seq = int(old_rows.seq)
            events_to_append.append(
                NewEvent(
                    type="evidence.superseded",
                    payload={
                        "target_event_seq": old_seq,
                        "reason": "re-extracted",
                    },
                    actor="extractor@1",
                    idempotency_key=f"supersede:{turn_id}:{old_seq}",
                    session_id=session_id,
                    turn_id=turn_id,
                    domain_id=domain_id,
                )
            )
            extract_event_key = f"turn.extracted:{turn_id}:after:{old_seq}"
        else:
            extract_event_key = f"turn.extracted:{turn_id}:v1"

        events_to_append.append(
            NewEvent(
                type="turn.extracted",
                payload={
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "observations": [o.model_dump(mode="json") for o in observations],
                    "misconceptions": [m.model_dump(mode="json") for m in misconceptions_out],
                },
                actor="extractor@1",
                idempotency_key=extract_event_key,
                session_id=session_id,
                turn_id=turn_id,
                domain_id=domain_id,
            )
        )

        stored = await append_events(
            session,
            learner_id,
            events_to_append,
            publish_wakeup=True,
            redis=redis,
        )
        extracted_event = stored[-1]
        committed_seq = extracted_event.seq

    if redis is not None:
        message = orjson.dumps(
            {
                "event": "extractor_done",
                "data": {"turn_id": str(turn_id), "seq": committed_seq},
            }
        ).decode()
        with contextlib.suppress(RedisError, OSError):
            await redis.publish(session_events_channel(session_id), message)

    return committed_seq


@register_job_handler("turn.extract")
async def handle_turn_extract(job: ClaimedJob, ctx: JobContext) -> None:
    redis = create_redis(ctx.settings)
    provider = HttpLLMProvider(
        ctx.settings.llm_api_base_url,
        ctx.settings.llm_api_key.get_secret_value(),
    )
    gateway = LLMGateway(ctx.session_factory, provider, ctx.settings)
    try:
        await run_turn_extract(job, ctx, gateway=gateway, redis=redis)
    finally:
        with contextlib.suppress(Exception):
            await redis.aclose()


async def run_session_checkpoint(
    job: ClaimedJob,
    ctx: JobContext,
    *,
    gateway: LLMGateway,
    redis: Redis | None = None,
) -> int | None:
    """Execute `session.checkpoint` for `job`; returns the `seq` of `session.checkpointed`.

    Returns None when a checkpoint covering `up_to_turn_index` already exists.
    """
    payload = job.payload
    session_id = UUID(str(payload["session_id"]))
    up_to_turn_index = int(payload["up_to_turn_index"])

    async with ctx.session_factory() as session:
        sess_row = (
            await session.execute(
                text("SELECT learner_id, domain_id FROM core.sessions WHERE id = :s"),
                {"s": session_id},
            )
        ).one()
        learner_id: UUID = job.learner_id if job.learner_id is not None else sess_row.learner_id
        domain_id: UUID = sess_row.domain_id

        previous = await last_checkpoint(session, learner_id, session_id)
        prev_payload = previous.payload if previous is not None else {}
        prev_up_to = int(prev_payload.get("up_to_turn_index", -1))
        if prev_up_to >= up_to_turn_index:
            return None

        reply_rows = (
            await session.execute(
                text(
                    "SELECT type, payload FROM core.events WHERE learner_id = :l "
                    "AND session_id = :s AND type = ANY(:types) "
                    "AND (payload->>'turn_index')::int > :after "
                    "AND (payload->>'turn_index')::int <= :up_to ORDER BY seq"
                ),
                {
                    "l": learner_id,
                    "s": session_id,
                    "types": list(TURN_EVENT_TYPES),
                    "after": prev_up_to,
                    "up_to": up_to_turn_index,
                },
            )
        ).all()

    turns_to_fold = [
        {
            "role": "student" if r.type == "turn.student_replied" else "teacher",
            "turn_index": int(r.payload["turn_index"]),
            "text": str(r.payload.get("text", "")),
        }
        for r in reply_rows
    ]
    variables = {
        "previous_summary": str(prev_payload.get("rolling_summary", "")),
        "previous_open_threads_json": json.dumps(
            prev_payload.get("open_threads", []), ensure_ascii=False
        ),
        "turns_to_fold_json": json.dumps(turns_to_fold, ensure_ascii=False),
    }

    llm_result = await gateway.generate_structured(
        role="checkpointer",
        prompt_version=1,
        response_model=CheckpointerOutput,
        variables=variables,
        learner_id=learner_id,
        session_id=session_id,
        turn_id=None,
        idempotency_key=f"checkpoint:{session_id}:{up_to_turn_index}",
    )
    output = llm_result.value

    async with ctx.session_factory() as session, session.begin():
        stored = await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="session.checkpointed",
                    payload={
                        "session_id": str(session_id),
                        "up_to_turn_index": up_to_turn_index,
                        "rolling_summary": output.rolling_summary,
                        "open_threads": list(output.open_threads),
                        "key_observations": list(output.key_observations),
                    },
                    actor="checkpointer@1",
                    idempotency_key=f"session.checkpointed:{session_id}:{up_to_turn_index}",
                    session_id=session_id,
                    domain_id=domain_id,
                )
            ],
            publish_wakeup=True,
            redis=redis,
        )
    return stored[-1].seq


@register_job_handler("session.checkpoint")
async def handle_session_checkpoint(job: ClaimedJob, ctx: JobContext) -> None:
    redis = create_redis(ctx.settings)
    provider = HttpLLMProvider(
        ctx.settings.llm_api_base_url,
        ctx.settings.llm_api_key.get_secret_value(),
    )
    gateway = LLMGateway(ctx.session_factory, provider, ctx.settings)
    try:
        await run_session_checkpoint(job, ctx, gateway=gateway, redis=redis)
    finally:
        with contextlib.suppress(Exception):
            await redis.aclose()
