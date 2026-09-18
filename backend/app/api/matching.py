"""Phase 2 matching transport; B1 owns all selection rules."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_student, get_rule_deps, get_session
from app.db.repo import forecast as forecast_repo
from app.db.repo import profiles as profile_repo
from app.db.repo import programs as program_repo
from app.errors import NotFound, ValidationFailed
from app.events.dispatch import RuleDeps
from app.graph.queries import kb
from app.matching import compare as compare_rules
from app.matching import hard, rank, realism
from app.schemas.auth import StudentCtx
from app.schemas.common import ExamId
from app.schemas.matching import CompareOut, MatchingOut, MatchOut

router = APIRouter(prefix="/matching", tags=["matching"])


async def _inputs(session: AsyncSession, student: StudentCtx, deps: RuleDeps):
    profile = await profile_repo.get_profile(session, student.student_id)
    programs = await program_repo.list_all(session)
    saved = await program_repo.list_saved_programs(session, student.student_id)
    exam_ids: set[ExamId] = {
        requirement.exam_id
        for program in saved
        for requirement in program.requirements
        if requirement.exam_id is not None
    }
    forecasts = {
        exam_id: forecast
        for exam_id in exam_ids
        if (forecast := await forecast_repo.get(session, student.student_id, exam_id))
        is not None
    }
    all_exam_ids: set[ExamId] = {
        requirement.exam_id
        for program in programs
        for requirement in program.requirements
        if requirement.exam_id is not None
    }
    test_dates = (
        {
            exam_id: await kb.list_test_dates(deps.graph, exam_id)
            for exam_id in all_exam_ids
        }
        if deps.graph is not None
        else {}
    )
    return profile, programs, forecasts, test_dates


@router.get("", response_model=MatchingOut)
async def get_matching(
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
    limit: int = Query(default=10, ge=1),
) -> MatchingOut:
    profile, programs, forecasts, test_dates = await _inputs(session, student, deps)
    hard_results = hard.hard_filter(
        profile, programs, forecasts, test_dates, deps.now().date(), deps.params
    )
    levels = {
        item.program_id: realism.realism(item, deps.params) for item in hard_results
    }
    ranked = rank.rank(profile, hard_results, soft_scores={}, params=deps.params)
    by_program = {program.id: program for program in programs}
    by_hard = {item.program_id: item for item in hard_results}
    items = [
        MatchOut(
            program=by_program[item.program_id],
            realism=levels[item.program_id],
            factors=by_hard[item.program_id].factors,
            assumptions=by_hard[item.program_id].assumptions,
            score=item.score,
            fits_text=None,
            soft_pending=True,
        )
        for item in ranked
    ]
    return MatchingOut(
        items=items[: max(limit, deps.params.min_candidates)],
        total=len(items),
        profile_readiness=profile.readiness,
        forecast_used=bool(forecasts),
        empty_reason=hard.explain_empty(hard_results) if not hard_results else None,
    )


@router.get("/compare", response_model=CompareOut)
async def compare_matching(
    ids: str,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> CompareOut:
    program_ids = [item.strip() for item in ids.split(",")]
    if (
        not 2 <= len(program_ids) <= 4
        or len(set(program_ids)) != len(program_ids)
        or not all(program_ids)
    ):
        raise ValidationFailed("select 2 to 4 distinct programs")
    profile = await profile_repo.get_profile(session, student.student_id)
    programs = []
    for program_id in program_ids:
        program = await program_repo.get_program(session, program_id)
        if program is None or program.flagged:
            raise NotFound("program not found")
        programs.append(program)
    forecasts = {}
    test_dates = {}
    hard_results = hard.hard_filter(
        profile, programs, forecasts, test_dates, deps.now().date(), deps.params
    )
    result = compare_rules.compare(profile, programs, hard_results)
    return CompareOut.model_validate(
        {**result.model_dump(), "program_ids": program_ids, "conclusion": None}
    )
