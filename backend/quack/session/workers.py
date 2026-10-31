"""Background handlers `turn.extract` and `session.checkpoint` (ТЗ §2.4)."""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import orjson
from neo4j import AsyncDriver
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text

from quack.db.redis import session_events_channel
from quack.events.append import append_events
from quack.events.models import NewEvent, StoredEvent
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.neo4j_store import Neo4jGraphStore, read_applied_seq
from quack.graph.slice import SliceDeps, read_consistent_slice
from quack.jobs.queue import ClaimedJob
from quack.jobs.runner import JobContext, register_job_handler
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap
from quack.logging import get_logger
from quack.practice.evaluation import load_task
from quack.session.context import TURN_EVENT_TYPES, _topic_slice, last_checkpoint
from quack.session.extraction import (
    EPISODE_EVENT_TYPES,
    extracted_elements,
    extraction_context,
    render_candidate_skills,
    render_events,
    render_known_misconceptions,
    task_line,
    turn_extracted_v2,
)
from quack.session.schemas import CheckpointerOutput, ExtractorOutputV2

log = get_logger(__name__)

# `extractor@N` of `turn.extract` (WP12.1); the actor of `turn.extracted` v2.
EXTRACTOR_PROMPT_VERSION = 2
EXTRACTOR_ACTOR = f"extractor@{EXTRACTOR_PROMPT_VERSION}"


@dataclass(frozen=True)
class _JobResources:
    neo4j: AsyncDriver
    redis: Redis | None
    llm: LLMGateway


def _job_resources(ctx: JobContext) -> _JobResources:
    """Neo4j driver, Redis client and LLM gateway of the process, only from `ctx` (WP2.1).

    `redis = None` is work without Redis (§4.2): the handlers already accept it. The driver
    and the gateway stay optional until D-A-002 is lifted; then this check goes away.
    """
    if ctx.neo4j is None or ctx.llm is None:
        raise RuntimeError("JobContext lacks neo4j or llm (WP2.1)")
    return _JobResources(neo4j=ctx.neo4j, redis=ctx.redis, llm=ctx.llm)


async def _read_topic_context(
    neo4j_driver: AsyncDriver,
    learner_id: UUID,
    topic_id: UUID | None,
    *,
    min_seq: int | None,
    redis: Redis | None,
    ctx: JobContext,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if topic_id is None:
        return [], []
    store = Neo4jGraphStore(neo4j_driver, str(learner_id))

    if redis is None:
        graph = await store.dump()
    else:

        async def applied_seq_reader(learner: UUID) -> int:
            return await read_applied_seq(neo4j_driver, str(learner))

        deps = SliceDeps(
            session_factory=ctx.session_factory,
            redis=redis,
            applied_seq_reader=applied_seq_reader,
            snapshot_read_retries=ctx.settings.snapshot_read_retries,
        )
        graph_slice = await read_consistent_slice(
            learner_id, min_seq, store.dump, ctx.settings.projector_lag_timeout_ms, deps=deps
        )
        graph = graph_slice.data
    _, skills, misconceptions = _topic_slice(graph, topic_id)
    return skills, misconceptions


async def _episode_events(
    session: Any,
    learner_id: UUID,
    session_id: UUID,
    turn_id: UUID,
    teacher_seq: int | None,
    context_turns: int,
) -> tuple[list[StoredEvent], list[StoredEvent]]:
    """Events of the episode and of the turns before it (D-B-022).

    Phase 3 has no `episode.closed` (Т-§7.5): the episode is the turn — the session events
    after the previous teacher reply up to this turn's teacher reply (the learner's reply and
    answer, hints, checks, the teacher reply and its problem). `context_turns` teacher turns
    before it are given for understanding only."""
    if teacher_seq is None:
        teacher_seq = (
            await session.execute(
                text(
                    "SELECT max(seq) FROM core.events WHERE learner_id = :l AND turn_id = :t "
                    "AND type = 'turn.teacher_replied'"
                ),
                {"l": learner_id, "t": turn_id},
            )
        ).scalar_one()
    rows = (
        await session.execute(
            text(
                f"SELECT {EVENT_COLUMNS} FROM core.events WHERE learner_id = :l "
                "AND session_id = :s AND type = ANY(:types) "
                "AND seq <= :upto ORDER BY seq"
            ),
            {
                "l": learner_id,
                "s": session_id,
                "types": list(EPISODE_EVENT_TYPES),
                "upto": teacher_seq if teacher_seq is not None else 2**62,
            },
        )
    ).all()
    events = [row_to_event(row) for row in rows]
    replies = [i for i, e in enumerate(events) if e.type == "turn.teacher_replied"]
    if not replies:
        return events, []
    # The last teacher reply closes the episode; the one before it opens it.
    start = replies[-2] + 1 if len(replies) >= 2 else 0
    before_start = replies[-2 - context_turns] + 1 if len(replies) >= 2 + context_turns else 0
    return events[start:], events[before_start:start]


async def run_turn_extract(
    job: ClaimedJob,
    ctx: JobContext,
    *,
    gateway: LLMGateway,
    redis: Redis | None = None,
    neo4j: AsyncDriver | None = None,
) -> int:
    """Execute `turn.extract` for `job` with `extractor@2` (WP12, D-B-016, D-B-022); returns
    the committed `seq` of `turn.extracted` (schema version 2)."""
    payload = job.payload
    session_id = UUID(str(payload["session_id"]))
    turn_id = UUID(str(payload["turn_id"]))
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
        episode, before = await _episode_events(
            session,
            learner_id,
            session_id,
            turn_id,
            int(teacher_seq) if teacher_seq is not None else None,
            ctx.settings.episode_context_turns,
        )
        task_ids = {
            str(e.payload["task_id"]) for e in [*before, *episode] if e.type == "task.assigned"
        }
        tasks = {tid: await load_task(session, learner_id, UUID(tid)) for tid in task_ids}

    driver = neo4j if neo4j is not None else _job_resources(ctx).neo4j
    skills, misconceptions = await _read_topic_context(
        driver,
        learner_id,
        topic_id,
        min_seq=int(teacher_seq) if teacher_seq is not None else None,
        redis=redis,
        ctx=ctx,
    )
    # Т-§4.5, Т-§7.6: candidates are the skills of the session topic, by labels only.
    labels = LabelMap.build(
        ctx.settings,
        skills=[UUID(str(s["skill_id"])) for s in skills],
        misconceptions=[UUID(str(m["misconception_id"])) for m in misconceptions],
    )
    task_lines = {
        tid: task_line(task.prompt_text, task.skill_ids, labels)
        for tid, task in tasks.items()
        if task is not None
    }
    variables = {
        "events_before": render_events(before, task_lines, labels),
        "episode": render_events(episode, task_lines, labels),
        "candidate_skills": render_candidate_skills(skills, labels),
        "known_misconceptions": render_known_misconceptions(misconceptions, labels),
    }

    llm_result = await gateway.generate_structured(
        role="extractor",
        prompt_version=EXTRACTOR_PROMPT_VERSION,
        response_model=ExtractorOutputV2,
        variables=variables,
        learner_id=learner_id,
        session_id=session_id,
        turn_id=turn_id,
        idempotency_key=f"extract:{turn_id}:v{EXTRACTOR_PROMPT_VERSION}",
    )
    # Law 4 (WP12.2): code checks every element; rejected ones are written with the reason.
    check = extraction_context(labels, episode, topic_id=topic_id)
    extracted = turn_extracted_v2(session_id, turn_id, extracted_elements(llm_result.value, check))

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
                    actor=EXTRACTOR_ACTOR,
                    idempotency_key=f"supersede:{turn_id}:{old_seq}",
                    session_id=session_id,
                    turn_id=turn_id,
                    domain_id=domain_id,
                )
            )
            extract_event_key = f"turn.extracted:{turn_id}:after:{old_seq}"
        else:
            extract_event_key = f"turn.extracted:{turn_id}:v{EXTRACTOR_PROMPT_VERSION}"

        events_to_append.append(
            NewEvent(
                type="turn.extracted",
                schema_version=2,
                payload=extracted.model_dump(mode="json"),
                actor=EXTRACTOR_ACTOR,
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
    resources = _job_resources(ctx)
    await run_turn_extract(job, ctx, gateway=resources.llm, redis=resources.redis)


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
    resources = _job_resources(ctx)
    await run_session_checkpoint(job, ctx, gateway=resources.llm, redis=resources.redis)
