"""Phase 2 matching transport; B1 owns all selection rules.

The assembly lives in `app.apply.matching` (phase 3, F13): the selection
assistant's `run_matching` / `compare` tools call the same functions, so the
route and the chat cannot disagree on order or realism.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import fallbacks, keys
from app.api.deps import get_current_student, get_rule_deps, get_session
from app.apply import matching as apply_matching
from app.events.dispatch import RuleDeps
from app.schemas.auth import StudentCtx
from app.schemas.matching import CompareOut, MatchingOut

router = APIRouter(prefix="/matching", tags=["matching"])


@router.get("", response_model=MatchingOut)
async def get_matching(
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
    limit: int = Query(default=10, ge=1),
) -> MatchingOut:
    result = await apply_matching.run_matching(session, deps, student.student_id, limit)
    # Hard factors and the table are rules over stored programs, so they work
    # without either dependency. The label says which of the two is missing:
    # a search that cannot run means the list is the cache plus the verified
    # floor, and a graph that cannot answer means no forecast went into it.
    return result.model_copy(
        update={
            "availability": fallbacks.availability(
                graph_ok=await apply_matching.graph_reachable(deps),
                search_ok=await _search_ok(deps),
                cached=not result.forecast_used,
            )
        }
    )


async def _search_ok(deps: RuleDeps) -> bool:
    """The last recorded provider outcome; unknown counts as working.

    Same source as `/health.checks.search` — a read of what the background
    jobs wrote, never a provider call from inside a GET (§10).
    """
    try:
        return not await deps.redis.get(keys.search_last_error())
    except Exception:  # noqa: BLE001
        return True


@router.get("/compare", response_model=CompareOut)
async def compare_matching(
    ids: str,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    session: Annotated[AsyncSession, Depends(get_session)],
    deps: Annotated[RuleDeps, Depends(get_rule_deps)],
) -> CompareOut:
    program_ids = [item.strip() for item in ids.split(",")]
    return await apply_matching.compare_programs(
        session, deps, student.student_id, program_ids
    )
