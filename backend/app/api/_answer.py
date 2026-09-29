"""Shared task-answer recording for topic, diagnostic, and mock routes."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event as EventRow
from app.db.models import TaskInstance as InstanceRow
from app.errors import Conflict, NotFound, ValidationFailed
from app.events import dispatch, handlers, store  # noqa: F401 (register B1 handlers)
from app.events.dispatch import RuleDeps
from app.events.session import current_session_id, session_minute
from app.schemas.auth import StudentCtx
from app.schemas.common import TaskMode
from app.schemas.events import EventIn, EventType, TaskAnsweredPayload
from app.schemas.tasks import AnswerIn, AnswerResult, TaskInstance


async def owned_instance(
    session: AsyncSession,
    student_id: UUID,
    instance_id: UUID,
    *,
    lock: bool = False,
) -> InstanceRow:
    statement = select(InstanceRow).where(
        InstanceRow.id == instance_id,
        InstanceRow.student_id == student_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if row is None:
        raise NotFound("task instance not found")
    return row


async def skipped_instance(
    session: AsyncSession, student_id: UUID, instance_id: UUID
) -> bool:
    event_id = await session.scalar(
        select(EventRow.id)
        .where(
            EventRow.student_id == student_id,
            EventRow.type.in_(
                (EventType.task_skipped.value, EventType.task_timed_out.value)
            ),
            EventRow.payload["instance_id"].astext == str(instance_id),
        )
        .limit(1)
    )
    return event_id is not None


async def record_answer(
    session: AsyncSession,
    deps: RuleDeps,
    student: StudentCtx,
    instance_id: UUID,
    body: AnswerIn,
    mode: TaskMode,
) -> AnswerResult:
    """Append once, dispatch once, and return the B1 handler's result."""
    body = AnswerIn.model_validate(body)
    if body.instance_id != instance_id or body.mode != mode:
        raise ValidationFailed("answer instance or mode does not match request")
    row = await owned_instance(session, student.student_id, instance_id, lock=True)
    if row.mode is not None and row.mode != mode:
        raise ValidationFailed("task mode does not match issued instance")
    if row.answered_at is not None or await skipped_instance(
        session, student.student_id, instance_id
    ):
        raise Conflict("task already answered or skipped")

    session_id = await current_session_id(deps.redis, student.student_id)
    now = deps.now()
    minute = await session_minute(session, student.student_id, session_id, now)
    payload = TaskAnsweredPayload(
        instance_id=instance_id,
        answer=body.answer,
        time_spent_sec=body.time_spent_sec,
        mode=mode,
        session_minute=minute,
        after_guideline=body.after_guideline,
        hint_level_before=body.hint_level_before,
    )
    event = await store.append(
        session,
        deps.redis,
        EventIn(
            type=EventType.task_answered,
            payload=payload.model_dump(mode="json"),
            student_id=student.student_id,
            session_id=session_id,
            exam_id=row.exam_id,
            topic_skill_id=row.skill_id,
            occurred_at=now,
        ),
        dispatch_event=False,
    )
    results = await dispatch.dispatch(session, event, deps)
    if "apply_task_answered" not in results:
        # The dispatcher refused to project: Neo4j is down, or an older event
        # of this student is still unapplied and this one must not overtake it
        # (§11 A2). The answer itself is already durable, so the student keeps
        # the grade and is told the knowledge update is owed.
        _queue_recovery(deps, student.student_id, event.id)
        from app.tasks.answer import grade as grade_fn

        # `row` — строка SQLAlchemy: `options` и `trap_answers` лежат в ней
        # словарями из JSON-колонки, а `grade` читает у них поля (`opt.key`).
        # Без разбора этот путь падал с 500 ровно там, где §11 A2 обещает
        # ученику сохранённую оценку — то есть при каждой деградации.
        instance = TaskInstance.model_validate(
            {name: getattr(row, name) for name in TaskInstance.model_fields}
        )

        return AnswerResult(
            grade=grade_fn(instance, body.answer),
            solution=row.solution_rendered,
            state_after=None,
            misconception_change=None,
            state_words="",
            knowledge_version=0,
            projection_status="pending",
        )
    result = AnswerResult.model_validate(results["apply_task_answered"])
    if result.projection_status == "pending":
        _queue_recovery(deps, student.student_id, event.id)
    return result


def _queue_recovery(deps: RuleDeps, student_id: UUID, event_id: int) -> None:
    """One durable catch-up intent per student (§13.3).

    The id is deterministic, so a burst of answers during an outage records
    one intent, not one per answer. Anything it does not reach — events that
    arrived after its watermark — the ten-minute sweep picks up.
    """
    deps.jobs.enqueue(
        "bulk",
        "recover_graph_events",
        job_id=f"graph-recover:{student_id}",
        student_id=str(student_id),
        through_event_id=event_id,
    )
