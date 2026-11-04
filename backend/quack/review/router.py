"""Routes of «Сегодня», prep mode and the grade (ТЗ Ф4 WP17, §6.4 «Хабы»; product package,
ТЗ §4.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from quack.api.auth import get_current_learner_id
from quack.api.errors import ApiError
from quack.api.ratelimit import enforce_action_rate_limit
from quack.api.resources import get_resources
from quack.review import today as review_today

router = APIRouter(
    prefix="/v1",
    tags=["review"],
    dependencies=[Depends(get_current_learner_id), Depends(enforce_action_rate_limit)],
)

type CurrentLearnerId = Annotated[UUID, Depends(get_current_learner_id)]


def _not_yours() -> ApiError:
    return ApiError("NOT_FOUND", "no such target of this learner", status_code=404)


@router.get("/domains/{domain_id}/today", response_model=review_today.TodayView)
async def today_endpoint(
    domain_id: UUID, request: Request, learner_id: CurrentLearnerId
) -> review_today.TodayView:
    r = get_resources(request)
    view = await review_today.today(
        r.db, r.neo4j, r.redis, r.settings, learner_id, domain_id, datetime.now(UTC)
    )
    if view is None:
        raise _not_yours()
    return view


@router.post("/hubs/{hub_id}/prep-mode", response_model=review_today.Accepted, status_code=202)
async def prep_mode_endpoint(
    hub_id: UUID, req: review_today.PrepMode, request: Request, learner_id: CurrentLearnerId
) -> review_today.Accepted:
    r = get_resources(request)
    seq = await review_today.set_prep_mode(r.db, r.neo4j, r.redis, learner_id, hub_id, req.enabled)
    if seq is None:
        raise _not_yours()
    return review_today.Accepted(seq=seq)


@router.post("/hubs/{hub_id}/grade", response_model=review_today.Accepted, status_code=202)
async def grade_endpoint(
    hub_id: UUID, req: review_today.Grade, request: Request, learner_id: CurrentLearnerId
) -> review_today.Accepted:
    r = get_resources(request)
    seq = await review_today.report_grade(r.db, r.neo4j, r.redis, learner_id, hub_id, req)
    if seq is None:
        raise _not_yours()
    return review_today.Accepted(seq=seq)
