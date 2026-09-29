"""Authenticated Phase 2 preparation-set routes."""

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app import fallbacks, keys
from app.api.chat import chat_id_for
from app.api.deps import get_arq, get_current_student, get_rule_deps, get_session
from app.apply import sets as apply_sets
from app.db.repo import forecast as forecast_repo
from app.db.repo import sets as set_repo
from app.db.repo import summaries as summaries_repo
from app.errors import Conflict, NotFound, ValidationFailed
from app.events import dispatch, store, version
from app.events.dispatch import RuleDeps
from app.schemas.auth import StudentCtx
from app.schemas.common import ExamId
from app.schemas.events import (
    EventIn,
    EventType,
    SetCompletedPayload,
    SetDeadlineChangedPayload,
    SetOpenedPayload,
    SetSwitchedByUserPayload,
    TopicCompletedPayload,
    TopicOpenedPayload,
)
from app.schemas.roadmap import SetSummaryOut
from app.schemas.sets import SetEditIn, SetOut, SetsByExam, SetSwitchIn, TopicOut
from app.sets import static_snapshot
from app.workers.queue import enqueue

router = APIRouter(prefix="/sets", tags=["sets"])
_logger = structlog.get_logger(__name__)
_MESSAGE_TYPES = [EventType.message_user, EventType.message_assistant]


async def _version(response: Response, deps: RuleDeps, student_id: UUID) -> None:
    response.headers["X-Knowledge-Version"] = str(
        await version.get(deps.redis, student_id)
    )


async def _owned_set(session: AsyncSession, student_id: UUID, set_id: UUID) -> SetOut:
    item = await set_repo.get_set(session, student_id, set_id)
    if item is None:
        raise NotFound("set not found")
    return item


def _topic(item: SetOut, skill_id: str) -> TopicOut:
    topic = next((topic for topic in item.topics if topic.skill_id == skill_id), None)
    if topic is None:
        raise NotFound("set topic not found")
    return topic


async def _read_sets(
    session: AsyncSession,
    student_id: UUID,
    exam_id: ExamId,
    deps: RuleDeps | None = None,
) -> SetsByExam:
    """The persisted plan, labelled with how it was answered.

    With Neo4j down the sets are still the student's real sets — they live in
    Postgres — but nothing can be rebuilt from knowledge right now, so the
    answer is marked `static` and the forecast is withheld rather than
    reported as a stale live number (§11 A3, AC05).
    """
    items = await set_repo.list_sets(session, student_id, exam_id)
    graph_ok = deps is None or deps.graph is not None
    forecast = await forecast_repo.get(session, student_id, exam_id)
    pending = False
    if deps is not None:
        from app.events import recovery

        pending = await recovery.is_pending(session, student_id)
    availability = fallbacks.availability(
        graph_ok=graph_ok,
        projection_pending=pending,
        as_of_event_id=forecast.as_of_event_id if forecast is not None else None,
    )
    if not graph_ok:
        # A forecast is a statement about knowledge we cannot read right now;
        # showing the last one as if it were current is the one thing §11 A3
        # forbids. The topics keep the canonical order from the seed snapshot,
        # so the screen stays readable — and if that snapshot is missing the
        # answer says `unavailable` rather than pretending (D05).
        forecast = None
        items = [
            item.model_copy(
                update={
                    "topics": static_snapshot.sort_topics(
                        exam_id, list(item.topics), lambda topic: topic.skill_id
                    )
                }
            )
            for item in items
        ]
        if not static_snapshot.available():
            availability = availability.model_copy(update={"mode": "unavailable"})
    return SetsByExam(
        exam_id=exam_id,
        forecast=forecast,
        current=next((item for item in items if item.status == "current"), None),
        upcoming=[item for item in items if item.status == "upcoming"],
        done=[item for item in items if item.status == "done"],
        availability=availability,
    )


async def _skill_names(deps: RuleDeps, exam_id: ExamId) -> dict[str, str]:
    """Topics are stored by skill id; the screen needs the skill's name.

    The graph holds the names (seed data). Without it the id stays — the
    same soft-fail as the rest of the read path.
    """
    if deps.graph is None:
        return {}
    from app.graph.queries import canonical as canonical_q

    try:
        weights = await canonical_q.list_exam_skills(deps.graph, exam_id)
    except Exception:  # noqa: BLE001
        return {}
    return {w.skill.id: w.skill.name for w in weights if w.skill.name}


def _named(item: SetOut, names: dict[str, str]) -> SetOut:
    topics = [
        t.model_copy(update={"name": names.get(t.skill_id, t.name)})
        for t in item.topics
    ]
    # The planner writes the reason as "<skill_id>: why" — it has no names at hand.
    skill_id, sep, why = item.reason.partition(": ")
    reason = f"{names[skill_id]}: {why}" if sep and skill_id in names else item.reason
    return item.model_copy(update={"topics": topics, "reason": reason})


async def _set_with_names(item: SetOut, deps: RuleDeps) -> SetOut:
    """Every answer that carries a set names its topics, not only the list read:
    the client caches what `switch` or `open` returned and draws the cards from it."""
    return _named(item, await _skill_names(deps, item.exam_id))


async def _with_names(sets: SetsByExam, deps: RuleDeps, exam_id: ExamId) -> SetsByExam:
    names = await _skill_names(deps, exam_id)
    if not names:
        return sets

    def named(item: SetOut | None) -> SetOut | None:
        return _named(item, names) if item is not None else None

    return sets.model_copy(
        update={
            "current": named(sets.current),
            "upcoming": [named(i) for i in sets.upcoming],
            "done": [named(i) for i in sets.done],
        }
    )


async def _record(session: AsyncSession, deps: RuleDeps, event_in: EventIn) -> dict:
    event = await store.append(session, deps.redis, event_in, dispatch_event=False)
    return await dispatch.dispatch(session, event, deps)


def _nothing_planned(sets: SetsByExam) -> bool:
    """True when Postgres holds no sets for this exam at all."""
    return sets.current is None and not sets.upcoming and not sets.done


@router.get("", response_model=SetsByExam)
async def list_sets(
    exam_id: ExamId,
    response: Response,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> SetsByExam:
    current = await _read_sets(session, student.student_id, exam_id, deps)
    if _nothing_planned(current):
        # The only write this route makes, and it happens at most once per
        # student and exam. The plan is a read model in Postgres, written by
        # the events that change it — анкета, замер, мок, ответ на задачу,
        # сохранённая программа. A student who has had none of them yet has no
        # rows at all, and an empty «Подготовка» is a dead end — so the first read
        # builds the plan. It terminates: the rebuild writes rows, and this
        # branch is never taken again.
        #
        # Anything short of that — a plan that looks wrong, a plan of one
        # consolidation set — is NOT rebuilt here. `replace_plan` gives every
        # upcoming set a new id, so rebuilding on each read would move the
        # student's sets out from under the links, the generated texts and the
        # opened forecast, all of which are keyed by set id.
        rebuilt = await apply_sets.rebuild_sets(
            session, deps, student.student_id, exam_id
        )
        # A rebuild without the graph cannot produce a plan; keep the honest
        # label instead of returning an unmarked empty answer (§11 A3).
        current = rebuilt.model_copy(update={"availability": current.availability})
    await _version(response, deps, student.student_id)
    return await _with_names(current, deps, exam_id)


@router.post("/switch", response_model=SetsByExam)
async def switch_set(
    body: SetSwitchIn,
    response: Response,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> SetsByExam:
    target = await _owned_set(session, student.student_id, body.set_id)
    if target.status == "done":
        raise Conflict("completed set cannot be selected")
    before = await set_repo.list_sets(session, student.student_id, target.exam_id)
    previous = next((item.id for item in before if item.status == "current"), None)
    try:
        await _record(
            session,
            deps,
            EventIn(
                type=EventType.set_switched_by_user,
                payload=SetSwitchedByUserPayload(
                    from_set_id=previous, to_set_id=body.set_id
                ).model_dump(mode="json"),
                student_id=student.student_id,
                exam_id=target.exam_id,
                set_id=body.set_id,
            ),
        )
    except StaleDataError as exc:
        # A rebuild replaced the plan between reading and switching: the chosen id is
        # gone. That is a conflict to re-read, not a server fault.
        await session.rollback()
        raise Conflict("the plan changed while switching, reload the sets") from exc
    result = await _read_sets(session, student.student_id, target.exam_id, deps)
    await _version(response, deps, student.student_id)
    return await _with_names(result, deps, target.exam_id)


@router.get("/{set_id}", response_model=SetOut)
async def get_set(
    set_id: UUID,
    response: Response,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> SetOut:
    item = await _owned_set(session, student.student_id, set_id)
    await _version(response, deps, student.student_id)
    return await _set_with_names(item, deps)


@router.get("/{set_id}/summary", response_model=SetSummaryOut)
async def get_set_summary(
    set_id: UUID,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SetSummaryOut:
    """The end-of-set report: statistics always, text when it is ready (§4.5)."""
    await _owned_set(session, student.student_id, set_id)
    summary = await summaries_repo.get(session, set_id)
    if summary is None:
        raise NotFound("set summary not found")
    return summary


@router.post("/{set_id}/open", response_model=SetOut)
async def open_set(
    set_id: UUID,
    response: Response,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> SetOut:
    item = await _owned_set(session, student.student_id, set_id)
    if item.status == "done":
        raise Conflict("completed set cannot be opened")
    await _record(
        session,
        deps,
        EventIn(
            type=EventType.set_opened,
            payload=SetOpenedPayload(
                set_id=set_id, skill_ids=[topic.skill_id for topic in item.topics]
            ).model_dump(mode="json"),
            student_id=student.student_id,
            exam_id=item.exam_id,
            set_id=set_id,
        ),
    )
    result = await apply_sets.open_set(session, deps, student.student_id, set_id)
    await _version(response, deps, student.student_id)
    return await _set_with_names(result, deps)


@router.patch("/{set_id}", response_model=SetOut)
async def edit_set(
    set_id: UUID,
    body: SetEditIn,
    response: Response,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> SetOut:
    item = await _owned_set(session, student.student_id, set_id)
    if body.deadline is not None and body.deadline < deps.now().date():
        raise ValidationFailed("deadline cannot be in the past")
    old_deadline = item.deadline
    await set_repo.update_set(
        session, student.student_id, set_id, body.skill_ids, body.deadline
    )
    if body.deadline is not None and body.deadline != old_deadline:
        await _record(
            session,
            deps,
            EventIn(
                type=EventType.set_deadline_changed,
                payload=SetDeadlineChangedPayload(
                    set_id=set_id, old=old_deadline, new=body.deadline
                ).model_dump(mode="json"),
                student_id=student.student_id,
                exam_id=item.exam_id,
                set_id=set_id,
            ),
        )
    result = await _owned_set(session, student.student_id, set_id)
    await _version(response, deps, student.student_id)
    return await _set_with_names(result, deps)


@router.post("/{set_id}/topics/{skill_id}/open", response_model=TopicOut)
async def open_topic(
    set_id: UUID,
    skill_id: str,
    response: Response,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> TopicOut:
    item = await _owned_set(session, student.student_id, set_id)
    topic = _topic(item, skill_id)
    await _record(
        session,
        deps,
        EventIn(
            type=EventType.topic_opened,
            payload=TopicOpenedPayload(set_id=set_id, skill_id=skill_id).model_dump(
                mode="json"
            ),
            student_id=student.student_id,
            exam_id=item.exam_id,
            set_id=set_id,
            topic_skill_id=skill_id,
        ),
    )
    await _version(response, deps, student.student_id)
    names = await _skill_names(deps, item.exam_id)
    return topic.model_copy(update={"name": names.get(topic.skill_id, topic.name)})


@router.post("/{set_id}/topics/{skill_id}/complete", response_model=SetOut)
async def complete_topic(
    set_id: UUID,
    skill_id: str,
    response: Response,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
    arq: Annotated[Any, Depends(get_arq)],
) -> SetOut:
    item = await _owned_set(session, student.student_id, set_id)
    topic = _topic(item, skill_id)
    if topic.status == "closed":
        raise Conflict("topic already completed")
    await set_repo.set_topic_status(session, set_id, skill_id, "closed")
    await _record(
        session,
        deps,
        EventIn(
            type=EventType.topic_completed,
            payload=TopicCompletedPayload(set_id=set_id, skill_id=skill_id).model_dump(
                mode="json"
            ),
            student_id=student.student_id,
            exam_id=item.exam_id,
            set_id=set_id,
            topic_skill_id=skill_id,
        ),
    )
    updated = await _owned_set(session, student.student_id, set_id)
    set_done = bool(updated.topics) and all(
        topic.status == "closed" for topic in updated.topics
    )
    if set_done:
        await _record(
            session,
            deps,
            EventIn(
                type=EventType.set_completed,
                payload=SetCompletedPayload(set_id=set_id).model_dump(mode="json"),
                student_id=student.student_id,
                exam_id=item.exam_id,
                set_id=set_id,
            ),
        )
        await set_repo.set_status(session, student.student_id, set_id, "done")
        updated = await _owned_set(session, student.student_id, set_id)
    # The observer should see the topic window before the student moves on;
    # the cached context of the closed topic and of the set is stale now.
    await session.commit()
    await _drop_topic_context(deps, student.student_id, set_id, skill_id)
    await _observe_after_completion(
        session, arq, student.student_id, updated, skill_id, set_done
    )
    await _version(response, deps, student.student_id)
    return await _set_with_names(updated, deps)


async def _drop_topic_context(
    deps: RuleDeps, student_id: UUID, set_id: UUID, skill_id: str
) -> None:
    try:
        await deps.redis.delete(
            keys.ctx_topic(str(student_id), skill_id),
            keys.ctx_topic(str(student_id), f"set:{set_id}"),
        )
    except Exception:  # noqa: BLE001 - the knowledge version still guards it
        _logger.warning("ctx_topic_invalidate_failed", student_id=str(student_id))


async def _observe_after_completion(
    session: AsyncSession,
    arq: Any,
    student_id: UUID,
    set_out: SetOut,
    skill_id: str,
    set_done: bool,
) -> None:
    """Queue the observer on the completed topic chat; when the whole set is
    done - on every topic chat with unprocessed messages and on the set chat
    (phase3 3.12)."""
    if arq is None:
        return
    try:
        targets: list[tuple[UUID, str]] = [
            (chat_id_for(student_id, "prep", set_out.id, skill_id), "topic_completed")
        ]
        if set_done:
            for topic in set_out.topics:
                chat_id = chat_id_for(student_id, "prep", set_out.id, topic.skill_id)
                if topic.skill_id != skill_id and await store.count_unprocessed(
                    session, chat_id, _MESSAGE_TYPES
                ):
                    targets.append((chat_id, "set_completed"))
            set_chat = chat_id_for(student_id, "prep", set_out.id, None)
            if await store.count_unprocessed(session, set_chat, _MESSAGE_TYPES):
                targets.append((set_chat, "set_completed"))
    except Exception:  # noqa: BLE001
        _logger.warning("observer_targets_failed", student_id=str(student_id))
        return
    for chat_id, trigger in targets:
        try:
            job_id = await enqueue(
                arq,
                "interactive",
                "observe_chat",
                _job_id=f"observe:{chat_id}",
                chat_id=chat_id,
                student_id=student_id,
                trigger=trigger,
            )
        except Exception:  # noqa: BLE001 - completing a topic must not fail on it
            _logger.warning("observer_not_enqueued", chat_id=str(chat_id))
            continue
        _logger.info(
            "observer_enqueued", chat_id=str(chat_id), trigger=trigger, job_id=job_id
        )
