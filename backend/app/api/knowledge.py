"""Authenticated Phase 2 knowledge read models and misconception disputes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_student, get_rule_deps, get_session
from app.apply import knowledge as apply_knowledge
from app.events import dispatch, handlers, store, version  # noqa: F401
from app.events.dispatch import RuleDeps
from app.graph.queries import personal
from app.schemas.auth import StudentCtx
from app.schemas.common import ExamId
from app.schemas.events import EventIn, EventType, MisconceptionDisputedPayload
from app.schemas.knowledge import (
    EvidenceOut,
    MisconceptionStateOut,
    RootCauseOut,
    SkillStateView,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class KnowledgeOut(BaseModel):
    skills: list[SkillStateView]
    misconceptions: list[MisconceptionStateOut]
    roots: list[RootCauseOut]


class EvidenceListOut(BaseModel):
    items: list[EvidenceOut]


class DisputeIn(BaseModel):
    disputed: bool


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
    roots = (
        await personal.list_root_causes(
            deps.graph, student.student_id, deps.params.root_window_days
        )
        if deps.graph is not None
        else []
    )
    await _version(response, deps, student)
    return KnowledgeOut(
        skills=skills,
        misconceptions=misconceptions,
        roots=roots,
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
