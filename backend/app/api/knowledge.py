"""Authenticated Phase 2 knowledge read models and misconception disputes."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from neo4j.exceptions import ServiceUnavailable, SessionExpired
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app import fallbacks
from app.api.chat import chat_id_for
from app.api.deps import (
    get_arq,
    get_current_student,
    get_llm,
    get_rule_deps,
    get_session,
)
from app.apply import knowledge as apply_knowledge
from app.apply import matching as apply_matching
from app.events import dispatch, handlers, recovery, store, version  # noqa: F401
from app.events.dispatch import RuleDeps
from app.graph.queries import personal
from app.schemas.auth import StudentCtx
from app.schemas.chat import ChatKind
from app.schemas.common import AvailabilityOut, ExamId
from app.schemas.events import (
    EventIn,
    EventType,
    MisconceptionDisputedPayload,
    ObserverRequestedPayload,
)
from app.schemas.knowledge import (
    EvidenceOut,
    MisconceptionStateOut,
    RootCauseOut,
    SkillStateView,
)
from app.workers.queue import enqueue

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class KnowledgeOut(BaseModel):
    skills: list[SkillStateView]
    misconceptions: list[MisconceptionStateOut]
    roots: list[RootCauseOut]
    # Phase 5 (D03): "the graph is down" and "this student knows nothing" are
    # both an empty list; only this field tells them apart.
    availability: AvailabilityOut | None = None


class EvidenceListOut(BaseModel):
    items: list[EvidenceOut]


class DisputeIn(BaseModel):
    disputed: bool


class RefreshIn(BaseModel):
    """Кнопка «обновить модель знаний» (memory-architecture §6,
    событие `observer.requested`)."""

    kind: ChatKind = "prep"
    set_id: UUID | None = None
    topic_skill_id: str | None = None


FailedReason = Literal["llm_unavailable", "queue_unavailable", "job_not_enqueued"]


class RefreshOut(BaseModel):
    """Ответ кнопки. `failed_reason` — машинный код для фронта: без него
    статус `failed` ничем не отличается от «наблюдатель ничего не нашёл»
    (`empty`), а это разные сообщения для ученика."""

    status: Literal["queued", "empty", "failed"]
    job_id: str | None = None
    window_size: int = 0
    failed_reason: FailedReason | None = None


async def _version(response: Response, deps: RuleDeps, student: StudentCtx) -> None:
    response.headers["X-Knowledge-Version"] = str(
        await version.get(deps.redis, student.student_id)
    )


@router.get("", response_model=KnowledgeOut)
async def get_knowledge(
    exam_id: ExamId,
    response: Response,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> KnowledgeOut:
    skills = await apply_knowledge.states_view(
        session, deps, student.student_id, exam_id
    )
    misconceptions = await apply_knowledge.misconceptions_view(
        deps, student.student_id, exam_id
    )
    roots: list[RootCauseOut] = []
    if deps.graph is not None:
        try:
            roots = await personal.list_root_causes(
                deps.graph, student.student_id, deps.params.root_window_days
            )
        except (ServiceUnavailable, SessionExpired):
            roots = []
    await _version(response, deps, student)
    pending = await recovery.is_pending(session, student.student_id)
    return KnowledgeOut(
        skills=skills,
        misconceptions=misconceptions,
        roots=roots,
        availability=fallbacks.availability(
            graph_ok=await apply_matching.graph_reachable(deps),
            projection_pending=pending,
        ),
    )


@router.get("/explain/{node_id}", response_model=EvidenceListOut)
async def explain(
    node_id: str,
    response: Response,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> EvidenceListOut:
    items = await apply_knowledge.explain(deps, student.student_id, node_id)
    await _version(response, deps, student)
    return EvidenceListOut(items=items)


@router.post("/refresh", response_model=RefreshOut)
async def refresh(
    body: RefreshIn,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
    llm: Annotated[Any, Depends(get_llm)],
    arq: Annotated[Any, Depends(get_arq)],
) -> RefreshOut:
    """Поставить наблюдателя на окно этого чата прямо сейчас.

    Окно — необработанные события чата (`processed_at IS NULL`), не больше
    `observer_window_max`; пустое окно — это не ошибка, а «нечего смотреть».
    """
    chat_id = chat_id_for(
        student.student_id, body.kind, body.set_id, body.topic_skill_id
    )
    window = await store.list_unprocessed(
        session, chat_id, limit=deps.params.observer_window_max
    )
    if not window:
        return RefreshOut(status="empty")

    llm_status = await llm.status() if llm is not None else "down"
    if llm_status == "down":
        return RefreshOut(
            status="failed",
            window_size=len(window),
            failed_reason="llm_unavailable",
        )
    if arq is None:
        return RefreshOut(
            status="failed",
            window_size=len(window),
            failed_reason="queue_unavailable",
        )

    await store.append(
        session,
        deps.redis,
        EventIn(
            type=EventType.observer_requested,
            payload=ObserverRequestedPayload(reason="button").model_dump(mode="json"),
            student_id=student.student_id,
            chat_id=chat_id,
            set_id=body.set_id,
            topic_skill_id=body.topic_skill_id,
        ),
        dispatch_event=False,
    )
    # The event must be visible to the job before it takes the window.
    await session.commit()
    try:
        job_id = await enqueue(
            arq,
            "interactive",
            "observe_chat",
            _job_id=f"observe:{chat_id}",
            chat_id=chat_id,
            student_id=student.student_id,
            trigger="requested",
        )
    except RedisError:
        return RefreshOut(
            status="failed",
            window_size=len(window),
            failed_reason="queue_unavailable",
        )
    if job_id is None:
        return RefreshOut(
            status="failed",
            window_size=len(window),
            failed_reason="job_not_enqueued",
        )
    return RefreshOut(status="queued", job_id=job_id, window_size=len(window))


@router.post(
    "/misconceptions/{misconception_id}/dispute",
    response_model=MisconceptionStateOut,
)
async def dispute(
    misconception_id: str,
    body: DisputeIn,
    response: Response,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> MisconceptionStateOut:
    event_type = (
        EventType.misconception_disputed
        if body.disputed
        else EventType.misconception_undisputed
    )
    event = await store.append(
        session,
        deps.redis,
        EventIn(
            type=event_type,
            payload=MisconceptionDisputedPayload(
                misconception_id=misconception_id
            ).model_dump(mode="json"),
            student_id=student.student_id,
        ),
        dispatch_event=False,
    )
    results = await dispatch.dispatch(session, event, deps)
    await _version(response, deps, student)
    return MisconceptionStateOut.model_validate(results["apply_dispute"])
