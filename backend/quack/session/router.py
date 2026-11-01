"""HTTP surface of the session stream (ТЗ §3); thin delegation to lifecycle/turns/stream."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.sse import EventSourceResponse

from quack.api.errors import ApiError
from quack.api.resources import AppResources, get_resources
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.llm.gateway import LLMGateway
from quack.session import lifecycle, stream, turns
from quack.session.actions import Action, ActionAccepted, ActionFrame
from quack.session.deps import (
    current_learner_id,
    enforce_turn_rate_limit,
    get_compute_client,
    get_llm_gateway,
    get_settings_dep,
)
from quack.session.keepalive import sse_keepalive_response
from quack.session.schemas import (
    ActiveSession,
    FinishResponse,
    FinishSessionRequest,
    HeartbeatResponse,
    SessionDetail,
    SessionListResponse,
    SessionStarted,
    StartSessionRequest,
    TurnRequest,
)

router = APIRouter(prefix="/v1")

LearnerId = Annotated[UUID, Depends(current_learner_id)]
Resources = Annotated[AppResources, Depends(get_resources)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


@router.post("/sessions", status_code=201)
async def start_session(
    body: StartSessionRequest, learner_id: LearnerId, resources: Resources, settings: SettingsDep
) -> SessionStarted:
    return await lifecycle.start_session(resources, settings, learner_id, body)


@router.get("/sessions")
async def list_sessions(
    learner_id: LearnerId,
    resources: Resources,
    settings: SettingsDep,
    domain_id: UUID | None = None,
    limit: int | None = Query(None),
) -> SessionListResponse:
    effective_limit = settings.session_list_default_limit if limit is None else limit
    if effective_limit < 1 or effective_limit > settings.session_list_max_limit:
        raise ApiError(
            "VALIDATION_ERROR",
            f"limit must be between 1 and {settings.session_list_max_limit}",
            status_code=422,
        )
    return await lifecycle.list_sessions(resources, learner_id, domain_id, effective_limit)


@router.get("/sessions/active")
async def get_active_session(learner_id: LearnerId, resources: Resources) -> ActiveSession | None:
    return await lifecycle.get_active_session(resources, learner_id)


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: UUID, learner_id: LearnerId, resources: Resources
) -> SessionDetail:
    return await lifecycle.get_session(resources, learner_id, session_id)


@router.post("/sessions/{session_id}/heartbeat")
async def heartbeat(
    session_id: UUID, learner_id: LearnerId, resources: Resources, settings: SettingsDep
) -> HeartbeatResponse:
    return await lifecycle.heartbeat(resources, settings, learner_id, session_id)


@router.post("/sessions/{session_id}/finish")
async def finish_session(
    session_id: UUID, body: FinishSessionRequest, learner_id: LearnerId, resources: Resources
) -> FinishResponse:
    return await lifecycle.finish_session(resources, learner_id, session_id, body)


@router.post("/sessions/{session_id}/turns")
async def post_turn(
    session_id: UUID,
    body: TurnRequest,
    request: Request,
    learner_id: LearnerId,
    resources: Resources,
    settings: SettingsDep,
    gateway: Annotated[LLMGateway, Depends(get_llm_gateway)],
    compute: Annotated[ComputeClient, Depends(get_compute_client)],
) -> StreamingResponse:
    await enforce_turn_rate_limit(resources, settings, learner_id)
    events = await turns.open_turn(
        resources,
        settings,
        gateway,
        compute,
        learner_id,
        session_id,
        body,
        str(getattr(request.state, "request_id", "")),
    )
    return sse_keepalive_response(events, settings.sse_keepalive_s)


@router.post(
    "/sessions/{session_id}/actions",
    response_class=EventSourceResponse,
    response_description="Actions with a teacher turn: SSE frames in the order of ТЗ Ф4 §4.7.",
    responses={
        201: {
            "description": "Actions without a teacher turn (`confidence`): the action event seq.",
            "content": {"application/json": {"schema": ActionAccepted.model_json_schema()}},
        },
    },
)
async def post_action(
    session_id: UUID, body: Action, learner_id: LearnerId, resources: Resources
) -> AsyncIterator[ActionFrame]:
    """ТЗ Ф4 WP13.1 (`Т-§1.4`): one learner action. Stage I of WP13 (§0.3): declared for the
    OpenAPI draft of G1 (seam S11); the body lands with WP13."""
    raise NotImplementedError
    yield  # a generator: FastAPI streams its frames as SSE


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: UUID, learner_id: LearnerId, resources: Resources, settings: SettingsDep
) -> StreamingResponse:
    events = await stream.stream_session_events(resources, learner_id, session_id)
    return sse_keepalive_response(events, settings.sse_keepalive_s)
