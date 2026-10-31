"""Turn pipeline `POST /v1/sessions/{id}/turns` (ТЗ §2.3): lock, context, teacher, commit, SSE."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Collection
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from quack.api.errors import ApiError
from quack.api.resources import AppResources
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.events.payloads import TaskEvaluated, TaskSubmitted
from quack.graph.ids import QUACK_UUID_NS
from quack.jobs import kinds
from quack.jobs.queue import enqueue_job
from quack.llm.errors import LLMBudgetExceededError, LLMSchemaError, LLMUnavailableError
from quack.llm.gateway import LLMGateway
from quack.practice.evaluation import (
    TASK_COLUMNS,
    AnswerVerdict,
    TaskToCheck,
    evaluate_answer,
    hint_penalties,
    load_task,
    task_from_row,
)
from quack.practice.selection import pick_task
from quack.session.context import build_turn_context
from quack.session.locks import acquire_turn_lock
from quack.session.schemas import TeacherOutput, TurnRequest
from quack.session.tasks import assigned_task_ids_from_overlay, solved_families_from_overlay
from quack.session.turn_policy import MEMORY_UNAVAILABLE_MARKER, resolve_degraded_mode

_TB_COLUMNS = ", ".join(f"tb.{c.strip()}" for c in TASK_COLUMNS.split(","))


def degraded_reply_text(task: TaskToCheck | None) -> str:
    """Template teacher reply used while the LLM is unavailable or over budget (Т-§17.1)."""
    if task is None:
        return (
            "The tutor is temporarily limited right now. "
            "Please try again in a little while or continue with your own notes."
        )
    return (
        "The tutor is temporarily limited right now, so let's practise with a ready task:\n\n"
        f"{task.prompt_text}"
    )


async def get_available_tasks(
    session: AsyncSession,
    learner_id: UUID,
    topic_id: UUID | None,
    exclude_task_ids: Collection[UUID] = (),
    *,
    session_id: UUID | None = None,
) -> list[TaskToCheck]:
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
    return [task_from_row(r) for r in rows]


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

            # S-1: Check if this idempotency key was already used
            existing_event_row = (
                await session.execute(
                    text(
                        "SELECT seq, session_id, turn_id, type, payload "
                        "FROM core.events "
                        "WHERE learner_id = :l AND idempotency_key = :k"
                    ),
                    {"l": learner_id, "k": body.idempotency_key},
                )
            ).one_or_none()

            if existing_event_row is not None:
                exp_task_id = str(body.related_task_id) if body.related_task_id else None
                if (
                    existing_event_row.type != "turn.student_replied"
                    or existing_event_row.session_id != session_id
                    or existing_event_row.payload.get("text") != body.text
                    or existing_event_row.payload.get("related_task_id") != exp_task_id
                ):
                    raise ApiError(
                        "IDEMPOTENCY_CONFLICT",
                        "Idempotency key reused with different turn parameters",
                        status_code=409,
                    )

                # Reconstruct stored turn events
                existing_turn_id = existing_event_row.turn_id
                turn_rows = (
                    await session.execute(
                        text(
                            "SELECT seq, type, payload FROM core.events "
                            "WHERE learner_id = :l AND session_id = :s AND turn_id = :t "
                            "ORDER BY seq"
                        ),
                        {"l": learner_id, "s": session_id, "t": existing_turn_id},
                    )
                ).all()

                stored_by_type = {r.type: r for r in turn_rows}
                student_ev = stored_by_type.get("turn.student_replied")
                teacher_ev = stored_by_type.get("turn.teacher_replied")
                eval_ev = stored_by_type.get("task.evaluated")
                assigned_ev = stored_by_type.get("task.assigned")

                if student_ev is None or teacher_ev is None:
                    raise ApiError("VALIDATION_ERROR", "incomplete stored turn", status_code=500)

                cached_sse: list[tuple[str, dict[str, Any]]] = [
                    (
                        "turn_started",
                        {
                            "turn_id": str(existing_turn_id),
                            "turn_index": student_ev.payload["turn_index"],
                            "user_seq": student_ev.seq,
                        },
                    ),
                    (
                        "token",
                        {"delta": teacher_ev.payload.get("text", "")},
                    ),
                ]
                if eval_ev is not None:
                    cached_sse.append(
                        (
                            "task_evaluated",
                            {
                                "task_id": eval_ev.payload["task_id"],
                                "is_correct": eval_ev.payload["is_correct"],
                                "score": eval_ev.payload["score"],
                                "feedback": eval_ev.payload["feedback"],
                            },
                        )
                    )
                if assigned_ev is not None:
                    assigned_t_row = await load_task(
                        session, learner_id, UUID(assigned_ev.payload["task_id"])
                    )
                    cached_sse.append(
                        (
                            "task_assigned",
                            {
                                "task_id": assigned_ev.payload["task_id"],
                                "kind": assigned_t_row.kind if assigned_t_row else "choice",
                                "prompt_text": assigned_t_row.prompt_text if assigned_t_row else "",
                            },
                        )
                    )
                cached_sse.append(
                    (
                        "turn_completed",
                        {
                            "turn_id": str(existing_turn_id),
                            "committed_seq": teacher_ev.seq,
                            "degraded_mode": teacher_ev.payload.get("degraded_mode"),
                        },
                    )
                )
                cached_sse.append(("done", {}))

                async def _cached_stream() -> AsyncIterator[tuple[str, dict[str, Any]]]:
                    try:
                        for itm in cached_sse:
                            yield itm
                    finally:
                        await lock.__aexit__(None, None, None)

                return _cached_stream()

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
                session_id=session_id,
            )

        available_tasks_data = [
            {
                "task_id": str(t.task_id),
                "family_id": t.family_id,
                "kind": t.kind,
                "difficulty": t.difficulty,
                "prompt_text": t.prompt_text,
            }
            for t in available_tasks
        ]

        # 4. In-turn task evaluation (ТЗ-П2 §2.3.4) through the one cascade of quack.practice
        #    (WP6.2, D-B-006), then the teacher call via LLMGateway.
        turn_id = uuid5(QUACK_UUID_NS, f"turn:{learner_id}:{session_id}:{body.idempotency_key}")
        submitted_task: TaskToCheck | None = None
        verdict: AnswerVerdict | None = None
        task_evaluation_json = "null"
        if body.related_task_id is not None:
            async with resources.session_factory() as session:
                submitted_task = await load_task(session, learner_id, body.related_task_id)
                given = (
                    await hint_penalties(session, learner_id, session_id, submitted_task.task_id)
                    if submitted_task is not None
                    else []
                )
            if submitted_task is not None:
                verdict = await evaluate_answer(
                    submitted_task,
                    body.text,
                    [penalty for _, penalty in given],
                    learner_id=learner_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    idempotency_key=(
                        f"evaluator:{session_id}:{submitted_task.task_id}:{body.idempotency_key}"
                    ),
                    gateway=gateway,
                    compute=compute,
                    settings=settings,
                )
                task_evaluation_json = json.dumps(
                    {
                        "task_id": str(submitted_task.task_id),
                        "evaluator": verdict.evaluator,
                        "is_correct": verdict.is_correct,
                        "score": verdict.score,
                        "independence": verdict.independence,
                        "feedback": verdict.feedback,
                        "skill_scores": verdict.skill_scores,
                    },
                    ensure_ascii=False,
                )

        variables = {
            "mode": context.mode,
            "topic_name": context.topic_name,
            "rolling_summary": context.rolling_summary,
            "recent_turns_json": json.dumps(context.recent_turns, ensure_ascii=False),
            # WP6.1 (Т-§20.7): the memory slot is marked while Neo4j is unreachable.
            "skills_slice_json": (
                MEMORY_UNAVAILABLE_MARKER
                if context.memory_unavailable
                else json.dumps(context.skills_slice, ensure_ascii=False)
            ),
            "misconceptions_json": (
                MEMORY_UNAVAILABLE_MARKER
                if context.memory_unavailable
                else json.dumps(context.misconceptions, ensure_ascii=False)
            ),
            "available_tasks_json": json.dumps(available_tasks_data, ensure_ascii=False),
            "task_evaluation_json": task_evaluation_json,
            "student_text": body.text,
        }

        # Deterministic degradation (Т-§17.1): the turn never fails with 503 on LLM errors.
        llm_degraded_mode: str | None = None
        fallback_task: TaskToCheck | None = None
        try:
            teacher_result = await gateway.generate_structured(
                role="teacher",
                prompt_version=1,
                response_model=TeacherOutput,
                variables=variables,
                learner_id=learner_id,
                session_id=session_id,
                turn_id=turn_id,
                idempotency_key=f"turn:{session_id}:{body.idempotency_key}:teacher",
            )
            teacher_output = teacher_result.value
        except (LLMBudgetExceededError, LLMUnavailableError, LLMSchemaError) as exc:
            llm_degraded_mode = (
                "budget_exceeded" if isinstance(exc, LLMBudgetExceededError) else "llm_unavailable"
            )
            if topic_id is not None:
                async with resources.session_factory() as session:
                    fallback_task = await pick_task(
                        session,
                        learner_id,
                        topic_id,
                        exclude_task_ids=assigned_task_ids,
                        solved_family_ids=solved_family_ids,
                        kinds=("math", "choice"),
                        session_id=session_id,
                    )
            teacher_output = TeacherOutput(
                reply_text=degraded_reply_text(fallback_task),
                pedagogical_move="assign_task" if fallback_task is not None else "explain",
                assign_task_id=fallback_task.task_id if fallback_task is not None else None,
            )

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

        final_evaluated: TaskEvaluated | None = None
        if submitted_task is not None and verdict is not None:
            events.append(
                NewEvent(
                    type="task.submitted",
                    payload=TaskSubmitted(
                        session_id=session_id,
                        turn_id=turn_id,
                        task_id=submitted_task.task_id,
                        answer=body.text,
                        hints_used=max(0, body.hints_used),
                        response_time_sec=body.response_time_sec,
                    ),
                    actor="learner",
                    idempotency_key=f"{body.idempotency_key}:submitted",
                    session_id=session_id,
                    turn_id=turn_id,
                    domain_id=domain_id,
                )
            )
            final_evaluated = TaskEvaluated.model_validate(
                {
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "task_id": str(submitted_task.task_id),
                    "evaluator": verdict.evaluator,
                    "is_correct": verdict.is_correct,
                    "score": verdict.score,
                    "independence": verdict.independence,
                    "feedback": verdict.feedback,
                    "skill_scores": verdict.skill_scores,
                    "triggered_misconceptions": verdict.triggered_misconceptions,
                }
            )
            events.append(
                NewEvent(
                    type="task.evaluated",
                    payload=final_evaluated,
                    actor=verdict.actor,
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
        assigned_task: TaskToCheck | None = fallback_task
        if llm_degraded_mode is None and (
            teacher_output.pedagogical_move == "assign_task" or mode == "practice"
        ):
            avail_ids = {t.task_id for t in available_tasks}
            if (
                teacher_output.assign_task_id is not None
                and teacher_output.assign_task_id in avail_ids
                and teacher_output.assign_task_id not in assigned_task_ids
            ):
                assigned_task = next(
                    (t for t in available_tasks if t.task_id == teacher_output.assign_task_id),
                    None,
                )
            if assigned_task is None and topic_id is not None:
                async with resources.session_factory() as session:
                    assigned_task = await pick_task(
                        session,
                        learner_id,
                        topic_id,
                        exclude_task_ids=assigned_task_ids,
                        solved_family_ids=solved_family_ids,
                        session_id=session_id,
                    )
        degraded_mode = resolve_degraded_mode(
            budget_exceeded=llm_degraded_mode == "budget_exceeded",
            llm_unavailable=llm_degraded_mode == "llm_unavailable",
            memory_unavailable=context.memory_unavailable,
            teacher_fallback=False,  # role `teacher_fallback` arrives with WP5.4
            projector_lag=context.lag_degraded,
        )
        teacher_actor = (
            "code" if llm_degraded_mode in ("llm_unavailable", "budget_exceeded") else "teacher@1"
        )

        if assigned_task is not None:
            events.append(
                NewEvent(
                    type="task.assigned",
                    payload={
                        "session_id": str(session_id),
                        "turn_id": str(turn_id),
                        "task_id": str(assigned_task.task_id),
                        "topic_id": str(assigned_task.topic_id),
                    },
                    actor=teacher_actor,
                    idempotency_key=f"{body.idempotency_key}:task",
                    session_id=session_id,
                    turn_id=turn_id,
                    domain_id=domain_id,
                )
            )

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
                actor=teacher_actor,
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
        if final_evaluated is not None:
            sse_events.append(
                (
                    "task_evaluated",
                    {
                        "task_id": str(final_evaluated.task_id),
                        "is_correct": final_evaluated.is_correct,
                        "score": final_evaluated.score,
                        "feedback": final_evaluated.feedback,
                    },
                )
            )
        if assigned_task is not None:
            sse_events.append(
                (
                    "task_assigned",
                    {
                        "task_id": str(assigned_task.task_id),
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
