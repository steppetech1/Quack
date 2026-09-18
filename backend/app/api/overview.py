"""Phase 2 overview transport; B2 owns roadmap calculations."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_student, get_rule_deps, get_session
from app.apply import knowledge as apply_knowledge
from app.db.models import SetSummary
from app.db.repo import forecast as forecast_repo
from app.db.repo import milestones as milestone_repo
from app.db.repo import profiles as profile_repo
from app.db.repo import programs as program_repo
from app.errors import NotFound
from app.events import store
from app.events.dispatch import RuleDeps
from app.graph.queries import canonical, kb
from app.roadmap import conflicts, milestones, progress, requirements
from app.schemas.auth import StudentCtx
from app.schemas.common import ExamId
from app.schemas.events import EventIn, EventType, MilestoneDonePayload
from app.schemas.roadmap import MilestoneOut, OverviewOut, SetSummaryOut

router = APIRouter(prefix="/overview", tags=["overview"])


class MilestoneMarkIn(BaseModel):
    done: bool


async def _last_summary(session: AsyncSession, student_id) -> SetSummaryOut | None:
    row = await session.scalar(
        select(SetSummary)
        .where(SetSummary.student_id == student_id)
        .order_by(SetSummary.created_at.desc(), SetSummary.id.desc())
        .limit(1)
    )
    return SetSummaryOut.model_validate(row, from_attributes=True) if row else None


async def _build(
    session: AsyncSession, student: StudentCtx, deps: RuleDeps
) -> OverviewOut:
    saved = await program_repo.list_saved_programs(session, student.student_id)
    profile = await profile_repo.get_profile(session, student.student_id)
    exam_ids: set[ExamId] = {
        requirement.exam_id
        for program in saved
        for requirement in program.requirements
        if requirement.exam_id is not None
    }
    formats = {}
    test_dates = {}
    forecasts = {}
    for exam_id in sorted(exam_ids):
        if deps.graph is not None:
            exam_format = await canonical.get_exam_format(deps.graph, exam_id)
            if exam_format is not None:
                formats[exam_id] = exam_format
            test_dates[exam_id] = await kb.list_test_dates(deps.graph, exam_id)
        else:
            test_dates[exam_id] = []
        forecasts[exam_id] = await forecast_repo.get(
            session, student.student_id, exam_id
        )
    marks = await milestone_repo.list_marks(session, student.student_id)
    required = requirements.build_requirements(
        saved, profile, formats, test_dates, forecasts, deps.params
    )
    calendar = [item for dates in test_dates.values() for item in dates]
    today = deps.now().date()
    milestone_items = milestones.build_milestones(
        saved, required, test_dates, calendar, marks, today
    )
    planned_dates = {
        exam_id: next((item.date for item in dates if item.date >= today), None)
        for exam_id, dates in test_dates.items()
    }
    conflict_items = conflicts.find_conflicts(milestone_items, saved, planned_dates)
    progress_items = []
    for requirement in required:
        states = await apply_knowledge.states_view(
            session, deps, student.student_id, requirement.exam_id
        )
        progress_items.append(
            progress.exam_progress(
                requirement, states, forecasts.get(requirement.exam_id), milestone_items
            )
        )
    return OverviewOut(
        requirements=required,
        milestones=milestone_items,
        conflicts=conflict_items,
        progress=progress_items,
        last_set_summary=await _last_summary(session, student.student_id),
    )


@router.get("", response_model=OverviewOut)
async def get_overview(
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> OverviewOut:
    return await _build(session, student, deps)


@router.post("/milestones/{key}", response_model=MilestoneOut)
async def mark_milestone(
    key: str,
    body: MilestoneMarkIn,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> MilestoneOut:
    before = await _build(session, student, deps)
    if not any(item.key == key for item in before.milestones):
        raise NotFound("milestone not found")
    await milestone_repo.set_mark(session, student.student_id, key, body.done)
    await store.append(
        session,
        deps.redis,
        EventIn(
            type=EventType.milestone_done,
            payload=MilestoneDonePayload(milestone_key=key, done=body.done).model_dump(
                mode="json"
            ),
            student_id=student.student_id,
        ),
        deps,
    )
    after = await _build(session, student, deps)
    return next(item for item in after.milestones if item.key == key)
