"""Route of a visualization page (ТЗ Ф4 WP16.5, §6.4 «Артефакты», §7.3 п.3; Т-§13.6; D-B-178).

Only the declaration: the body is stream B's (`quack.practice.viz_page`). The page is returned
with the CSP header; the client shows it in `<iframe sandbox="allow-scripts">`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from quack.api.auth import get_current_learner_id
from quack.api.errors import ApiError
from quack.api.resources import AppResources, get_resources
from quack.practice.viz_page import VizNotFound, content_security_policy, load_viz_page

router = APIRouter(prefix="/v1", tags=["artifacts"], dependencies=[Depends(get_current_learner_id)])

type CurrentLearnerId = Annotated[UUID, Depends(get_current_learner_id)]
type Resources = Annotated[AppResources, Depends(get_resources)]


@router.get(
    "/viz/{viz_id}",
    response_class=Response,
    responses={200: {"content": {"text/html": {"schema": {"type": "string"}}}}},
)
async def get_viz(viz_id: UUID, learner_id: CurrentLearnerId, resources: Resources) -> Response:
    try:
        page = await load_viz_page(
            resources.db, resources.storage, learner_id=learner_id, viz_id=viz_id
        )
    except VizNotFound as exc:
        raise ApiError("NOT_FOUND", "visualization not found", status_code=404) from exc
    return Response(
        page,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": content_security_policy(resources.settings),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )
