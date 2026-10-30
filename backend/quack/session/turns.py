"""Turn pipeline `POST /v1/sessions/{id}/turns` (ТЗ §2.3): lock, context, teacher, commit, SSE."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Collection
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from quack.api.app import AppResources
from quack.api.errors import ApiError
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.jobs import kinds
from quack.jobs.queue import enqueue_job
from quack.llm.gateway import LLMGateway
from quack.session.context import build_turn_context
from quack.session.locks import acquire_turn_lock
from quack.session.schemas import TeacherOutput, TurnRequest
from quack.session.tasks import (
    _TB_COLUMNS,
    SubmissionOutcome,
    TaskBankRow,
    _row,
    assigned_task_ids_from_overlay,
    evaluate_submission,
    hint_penalties,
    load_task,
    pick_task,
    solved_families_from_overlay,
)


async def get_available_tasks(
    session: AsyncSession,
    learner_id: UUID,
    topic_id: UUID | None,
    exclude_task_ids: Collection[UUID] = (),
    *,
    session_id: UUID | None = None,
) -> list[TaskBankRow]:
    """All unassigned tasks of the topic for available_tasks_json in teacher prompt."""
    if topic_id is None:
        return []
    sql = (
        f"SELECT {_TB_COLUMNS} FROM core.task_bank tb WHERE tb.learner_id = :l AND tb.topic_id = :t"
    )
    params: dict[str, Any] = {
        "l": learner_id,
        "t": topic_id,
    }
    if exclude_task_ids:
        sql += " AND NOT (tb.id = ANY(:exclude))"
        params["exclude"] = sorted(set(exclude_task_ids), key=str)
    if session_id is not None:
        sql += (
            " AND NOT EXISTS (SELECT 1 FROM core.events a WHERE a.learner_id = :l "
            "AND a.session_id = :s AND a.type = 'task.assigned' "
            "AND (a.payload->>'task_id')::uuid = tb.id)"
        )
        params["s"] = session_id
    sql += " ORDER BY tb.difficulty, tb.created_seq, tb.id"
    rows = (await session.execute(text(sql), params)).all()
    return [_row(r) for r in rows]


async def open_turn(
    resources: AppResources,
    settings: Settings,
    gateway: LLMGateway,
    compute: ComputeClient,
    learner_id: UUID,
    session_id: UUID,
    body: TurnRequest,
    request_id: str = "",
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Take the turn lock (409 TURN_LOCKED if busy) and return the SSE event stream."""
    lock = acquire_turn_lock(resources.redis, resources.engine, session_id, request_id)
    await lock.__aenter__()

    try:
        # 1. Verify session existence and active status
        async with resources.session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, domain_id, topic_id, mode, status, "
                        "last_turn_index, last_checkpoint_turn "
                        "FROM core.sessions WHERE id = :id AND learner_id = :l"
                    ),
                    {"id": session_id, "l": learner_id},
                )
            ).one_or_none()
            if row is None or row.status != "active":
                raise ApiError("NOT_FOUND", "session not found", status_code=404)
            prev_turn_index = int(row.last_turn_index)
            domain_id: UUID = row.domain_id
            topic_id: UUID | None = row.topic_id
            mode: str = str(row.mode)
            session_last_checkpoint_turn = int(row.last_checkpoint_turn)

        # 2. Build turn context
        context = await build_turn_context(resources, settings, learner_id, session_id)
        last_checkpoint_turn = max(context.last_checkpoint_turn, session_last_checkpoint_turn)

        # 3. Available tasks for prompt (using overlay.assigned_task_ids and overlay.graph)
        assigned_task_ids = assigned_task_ids_from_overlay(context.overlay)
        solved_family_ids = solved_families_from_overlay(context.overlay)

        async with resources.session_factory() as session:
            available_tasks = await get_available_tasks(
                session,
                learner_id,
                topic_id,
                assigned_task_ids,
            )

        available_tasks_data = [
            {
                "task_id": str(t.id),
                "family_id": t.family_id,
                "kind": t.kind,
                "difficulty": t.difficulty,
                "prompt_text": t.prompt_text,
            }
            for t in available_tasks
        ]

        # 4. In-turn task evaluation (ТЗ §2.3.4) and Teacher call via LLMGateway
        turn_id = uuid4()
        submission_outcome: SubmissionOutcome | None = None
        task_evaluation_json = "null"
        if body.related_task_id is not None:
            async with resources.session_factory() as session:
                submitted_task = await load_task(session, learner_id, body.related_task_id)
            if submitted_task is not None:
                penalties = hint_penalties(context.overlay, submitted_task.id)
                submission_outcome = await evaluate_submission(
                    compute,
                    submitted_task,
                    body.text,
                    session_id,
                    turn_id,
                    body.hints_used,
                    penalties,
                )
                if submission_outcome.evaluated is not None:
                    ev = submission_outcome.evaluated
                    task_evaluation_json = json.dumps(
                        {
                            "task_id": str(ev.task_id),
                            "evaluator": ev.evaluator,
                            "is_correct": ev.is_correct,
                            "score": ev.score,
                            "independence": ev.independence,
                            "feedback": ev.feedback,
                            "skill_scores": [s.model_dump(mode="json") for s in ev.skill_scores],
                        },
                        ensure_ascii=False,
                    )

        variables = {
            "mode": context.mode,
            "topic_name": context.topic_name,
            "rolling_summary": context.rolling_summary,
            "recent_turns_json": json.dumps(context.recent_turns, ensure_ascii=False),
            "skills_slice_json": json.dumps(context.skills_slice, ensure_ascii=False),
            "misconceptions_json": json.dumps(context.misconceptions, ensure_ascii=False),
            "available_tasks_json": json.dumps(available_tasks_data, ensure_ascii=False),
            "task_evaluation_json": task_evaluation_json,
            "student_text": body.text,
        }

        teacher_result = await gateway.generate_structured(
            role="teacher",
            prompt_version=1,
            response_model=TeacherOutput,
            variables=variables,
            learner_id=learner_id,
            session_id=session_id,
            turn_id=turn_id,
            idempotency_key=f"teacher:{body.idempotency_key}",
        )
        teacher_output = teacher_result.value

        # 5. Events to commit
        student_turn_index = prev_turn_index + 1
        teacher_turn_index = prev_turn_index + 2

        events: list[NewEvent] = [
            NewEvent(
                type="turn.student_replied",
                payload={
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "turn_index": student_turn_index,
                    "text": body.text,
                    "related_task_id": str(body.related_task_id) if body.related_task_id else None,
                },
                actor="learner",
                idempotency_key=body.idempotency_key,
                session_id=session_id,
                turn_id=turn_id,
                domain_id=domain_id,
            )
        ]

        if submission_outcome is not None:
            events.append(
                NewEvent(
                    type="task.submitted",
                    payload=submission_outcome.submitted,
                    actor="learner",
                    idempotency_key=f"{body.idempotency_key}:submitted",
                    session_id=session_id,
                    turn_id=turn_id,
                    domain_id=domain_id,
                )
            )
            if submission_outcome.evaluated is not None:
                events.append(
                    NewEvent(
                        type="task.evaluated",
                        payload=submission_outcome.evaluated,
                        actor="code",
                        idempotency_key=f"{body.idempotency_key}:evaluated",
                        session_id=session_id,
                        turn_id=turn_id,
                        domain_id=domain_id,
                    )
                )

        # Optional concept_explained
        if teacher_output.concept_explained is not None:
            ce = teacher_output.concept_explained
            events.append(
                NewEvent(
                    type="teacher.concept_explained",
                    payload={
                        "session_id": str(session_id),
                        "turn_id": str(turn_id),
                        "topic_id": str(ce.topic_id),
                        "skills": [s.model_dump(mode="json") for s in ce.skills],
                        "depth": float(ce.depth),
                        "summary": ce.summary,
                    },
                    actor="teacher@1",
                    idempotency_key=f"{body.idempotency_key}:concept",
                    session_id=session_id,
                    turn_id=turn_id,
                    domain_id=domain_id,
                )
            )

        # Task assignment when pedagogical_move is "assign_task" or mode is "practice"
        assigned_task: TaskBankRow | None = None
        if teacher_output.pedagogical_move == "assign_task" or mode == "practice":
            avail_ids = {t.id for t in available_tasks}
            if (
                teacher_output.assign_task_id is not None
                and teacher_output.assign_task_id in avail_ids
                and teacher_output.assign_task_id not in assigned_task_ids
            ):
                assigned_task = next(
                    (t for t in available_tasks if t.id == teacher_output.assign_task_id), None
                )
            if assigned_task is None and topic_id is not None:
                async with resources.session_factory() as session:
                    assigned_task = await pick_task(
                        session,
                        learner_id,
                        topic_id,
                        exclude_task_ids=assigned_task_ids,
                        solved_family_ids=solved_family_ids,
                    )
            if assigned_task is not None:
                events.append(
                    NewEvent(
                        type="task.assigned",
                        payload={
                            "session_id": str(session_id),
                            "turn_id": str(turn_id),
                            "task_id": str(assigned_task.id),
                            "topic_id": str(assigned_task.topic_id),
                        },
                        actor="teacher@1",
                        idempotency_key=f"{body.idempotency_key}:task",
                        session_id=session_id,
                        turn_id=turn_id,
                        domain_id=domain_id,
                    )
                )

        degraded_mode = "projector_lag" if context.lag_degraded else None
        events.append(
            NewEvent(
                type="turn.teacher_replied",
                payload={
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "turn_index": teacher_turn_index,
                    "text": teacher_output.reply_text,
                    "pedagogical_move": teacher_output.pedagogical_move,
                    "focus_skill_ids": [str(s) for s in teacher_output.focus_skill_ids],
                    "degraded_mode": degraded_mode,
                },
                actor="teacher@1",
                idempotency_key=f"{body.idempotency_key}:teacher",
                session_id=session_id,
                turn_id=turn_id,
                domain_id=domain_id,
            )
        )

        # 6. Single transaction: append events, enqueue turn.extract & session.checkpoint
        async with resources.session_factory() as session, session.begin():
            stored_events = await append_events(
                session,
                learner_id,
                events,
                publish_wakeup=True,
                redis=resources.redis,
            )
            student_stored = next(e for e in stored_events if e.type == "turn.student_replied")
            teacher_stored = next(e for e in stored_events if e.type == "turn.teacher_replied")

            await enqueue_job(
                session,
                kinds.turn_extract(
                    learner_id,
                    session_id,
                    {
                        "session_id": str(session_id),
                        "turn_id": str(turn_id),
                        "student_seq": student_stored.seq,
                        "teacher_seq": teacher_stored.seq,
                    },
                ),
            )

            if (teacher_turn_index - last_checkpoint_turn) >= settings.checkpoint_every_turns:
                await enqueue_job(
                    session,
                    kinds.session_checkpoint(
                        learner_id,
                        session_id,
                        {
                            "session_id": str(session_id),
                            "up_to_turn_index": teacher_turn_index,
                        },
                    ),
                )

        # 7. Prepare SSE event sequence
        sse_events: list[tuple[str, dict[str, Any]]] = [
            (
                "turn_started",
                {
                    "turn_id": str(turn_id),
                    "turn_index": student_turn_index,
                    "user_seq": student_stored.seq,
                },
            ),
            (
                "token",
                {"delta": teacher_output.reply_text},
            ),
        ]
        if submission_outcome is not None and submission_outcome.evaluated is not None:
            ev = submission_outcome.evaluated
            sse_events.append(
                (
                    "task_evaluated",
                    {
                        "task_id": str(ev.task_id),
                        "is_correct": ev.is_correct,
                        "score": ev.score,
                        "feedback": ev.feedback,
                    },
                )
            )
        if assigned_task is not None:
            sse_events.append(
                (
                    "task_assigned",
                    {
                        "task_id": str(assigned_task.id),
                        "kind": assigned_task.kind,
                        "prompt_text": assigned_task.prompt_text,
                    },
                )
            )
        sse_events.append(
            (
                "turn_completed",
                {
                    "turn_id": str(turn_id),
                    "committed_seq": teacher_stored.seq,
                    "degraded_mode": degraded_mode,
                },
            )
        )
        sse_events.append(("done", {}))

    except Exception:
        await lock.__aexit__(None, None, None)
        raise

    async def _stream() -> AsyncIterator[tuple[str, dict[str, Any]]]:
        try:
            for item in sse_events:
                yield item
        finally:
            await lock.__aexit__(None, None, None)

    return _stream()
