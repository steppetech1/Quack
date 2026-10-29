"""HTTP surface of the session stream (ТЗ §3); thin delegation to lifecycle/turns/stream."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from quack.api.app import AppResources, get_resources
from quack.api.sse import sse_response
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.llm.gateway import LLMGateway
from quack.session import lifecycle, stream, turns
from quack.session.deps import (
    current_learner_id,
    enforce_turn_rate_limit,
    get_compute_client,
    get_llm_gateway,
    get_settings_dep,
)
from quack.session.schemas import (
    ActiveSession,
    FinishResponse,
    FinishSessionRequest,
    HeartbeatResponse,
    SessionDetail,
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
    return sse_response(events)


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: UUID, learner_id: LearnerId, resources: Resources
) -> StreamingResponse:
    return sse_response(stream.stream_session_events(resources, learner_id, session_id))
