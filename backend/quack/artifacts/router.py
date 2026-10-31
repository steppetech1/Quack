"""HTTP endpoints of Stream 3 (ТЗ §3). Thin: every handler calls one module function.

Mounted by the integrator with `app.include_router(router)`; tests mount it on
`create_app(settings)` locally (ТЗ §0.1).
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from quack.api.auth import get_current_learner_id
from quack.api.resources import AppResources, get_compute, get_llm, get_resources
from quack.artifacts import cheatsheets, notes, practice, search
from quack.artifacts.params import PARAMS
from quack.artifacts.schemas import (
    ArtifactListResponse,
    ArtifactView,
    CheatsheetRefreshRequest,
    CheatsheetRefreshResponse,
    ExportJson,
    ExportMdResponse,
    HintRequest,
    HintResponse,
    NextTaskRequest,
    NextTaskResponse,
    NoteCreateRequest,
    NoteCreateResponse,
    NoteDeleteRequest,
    NoteDeleteResponse,
    NoteUpdateRequest,
    NoteUpdateResponse,
    SearchResponse,
    SubmitRequest,
    SubmitResponse,
)
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.llm.gateway import LLMGateway

# Router-level auth: 401 before body validation (ТЗ Фазы 3 WP1.2, T-A2).
router = APIRouter(prefix="/v1", tags=["artifacts"], dependencies=[Depends(get_current_learner_id)])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


type CurrentLearnerId = Annotated[UUID, Depends(get_current_learner_id)]
type Resources = Annotated[AppResources, Depends(get_resources)]
type Gateway = Annotated[LLMGateway, Depends(get_llm)]
type Compute = Annotated[ComputeClient, Depends(get_compute)]
type AppSettings = Annotated[Settings, Depends(_settings)]


# --- Practice ---


@router.post("/sessions/{session_id}/tasks/next", response_model=NextTaskResponse)
async def next_task(
    session_id: UUID,
    body: NextTaskRequest,
    learner_id: CurrentLearnerId,
    resources: Resources,
    gateway: Gateway,
    settings: AppSettings,
) -> NextTaskResponse:
    return await practice.next_task(learner_id, session_id, body, resources, gateway, settings)


@router.post("/sessions/{session_id}/tasks/{task_id}/hint", response_model=HintResponse)
async def give_hint(
    session_id: UUID,
    task_id: UUID,
    body: HintRequest,
    learner_id: CurrentLearnerId,
    resources: Resources,
    gateway: Gateway,
    settings: AppSettings,
) -> HintResponse:
    return await practice.give_hint(
        learner_id, session_id, task_id, body, resources, gateway, settings
    )


@router.post("/sessions/{session_id}/tasks/{task_id}/submit", response_model=SubmitResponse)
async def submit_answer(
    session_id: UUID,
    task_id: UUID,
    body: SubmitRequest,
    learner_id: CurrentLearnerId,
    resources: Resources,
    gateway: Gateway,
    compute: Compute,
    settings: AppSettings,
) -> SubmitResponse:
    return await practice.submit_answer(
        learner_id, session_id, task_id, body, resources, gateway, compute, settings
    )


# --- Notes and artifacts ---


@router.post("/artifacts/notes", response_model=NoteCreateResponse, status_code=201)
async def create_note(
    body: NoteCreateRequest, learner_id: CurrentLearnerId, resources: Resources
) -> NoteCreateResponse:
    return await notes.create_note(learner_id, body, resources)


@router.patch("/artifacts/notes/{artifact_id}", response_model=NoteUpdateResponse)
async def update_note(
    artifact_id: UUID, body: NoteUpdateRequest, learner_id: CurrentLearnerId, resources: Resources
) -> NoteUpdateResponse:
    return await notes.update_note(learner_id, artifact_id, body, resources)


@router.delete("/artifacts/notes/{artifact_id}", response_model=NoteDeleteResponse)
async def delete_note(
    artifact_id: UUID, body: NoteDeleteRequest, learner_id: CurrentLearnerId, resources: Resources
) -> NoteDeleteResponse:
    return await notes.delete_note(learner_id, artifact_id, body, resources)


@router.get("/artifacts", response_model=ArtifactListResponse)
async def list_artifacts(
    learner_id: CurrentLearnerId,
    resources: Resources,
    domain_id: UUID | None = None,
    topic_id: UUID | None = None,
    kind: str | None = None,
) -> ArtifactListResponse:
    return await notes.list_artifacts(learner_id, domain_id, topic_id, kind, resources)


@router.get("/artifacts/search", response_model=SearchResponse)
async def search_artifacts(
    learner_id: CurrentLearnerId,
    resources: Resources,
    q: str = Query(min_length=PARAMS.search_min_query_len),
    domain_id: UUID | None = None,
    limit: int = Query(PARAMS.search_default_limit, ge=1, le=PARAMS.search_max_limit),
) -> SearchResponse:
    return await search.search_artifacts(learner_id, q, domain_id, limit, resources)


# --- Cheatsheets ---


@router.get("/topics/{topic_id}/cheatsheet", response_model=ArtifactView | None)
async def get_cheatsheet(
    topic_id: UUID, learner_id: CurrentLearnerId, resources: Resources
) -> ArtifactView | None:
    return await cheatsheets.get_cheatsheet(learner_id, topic_id, resources)


@router.post("/topics/{topic_id}/cheatsheet/refresh", response_model=CheatsheetRefreshResponse)
async def request_cheatsheet_refresh(
    topic_id: UUID,
    body: CheatsheetRefreshRequest,
    learner_id: CurrentLearnerId,
    resources: Resources,
) -> CheatsheetRefreshResponse:
    return await cheatsheets.request_cheatsheet_refresh(learner_id, topic_id, body, resources)


# --- Export ---


@router.get("/domains/{domain_id}/export", response_model=ExportMdResponse | ExportJson)
async def export_domain(
    domain_id: UUID,
    learner_id: CurrentLearnerId,
    resources: Resources,
    format: Literal["md", "json"] = "md",
) -> ExportMdResponse | ExportJson:
    return await search.export_domain(learner_id, domain_id, format, resources)
