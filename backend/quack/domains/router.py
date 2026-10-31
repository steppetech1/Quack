"""FastAPI router for Stream 1 (`/v1/auth/*`, `/v1/learner/*`, `/v1/domains/*`)
wiring all 18 HTTP endpoints to domain service modules (ТЗ §3).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from quack.api.auth import get_current_learner_id
from quack.api.resources import get_llm, get_resources
from quack.config import Settings
from quack.domains import auth, diagnostic, graph_view, learner, service, structure, topics
from quack.domains.schemas import (
    AnswerDiagnosticRequest,
    AnswerDiagnosticResponse,
    ArchiveDomainRequest,
    ArchiveDomainResponse,
    AuthResponse,
    ConfirmStructureRequest,
    ConfirmStructureResponse,
    CreateDomainRequest,
    CreateDomainResponse,
    DeleteLearnerResponse,
    DomainGraphResponse,
    DomainListResponse,
    DomainSummary,
    EditStructureRequest,
    EditStructureResponse,
    FinishDiagnosticRequest,
    FinishDiagnosticResponse,
    LearnerMeResponse,
    LoginRequest,
    ProposeStructureRequest,
    ProposeStructureResponse,
    RegisterRequest,
    StartDiagnosticRequest,
    StartDiagnosticResponse,
    TopicDetailResponse,
    UpdatePreferencesRequest,
    UpdatePreferencesResponse,
    UpdateTopicStatusRequest,
    UpdateTopicStatusResponse,
)
from quack.llm.gateway import LLMGateway

# Register and login are public; every other route authenticates at router level, so a
# request without a token gets 401 before body validation (ТЗ Фазы 3 WP1.2, T-A2).
_public = APIRouter(prefix="/v1", tags=["domains"])
_protected = APIRouter(
    prefix="/v1", tags=["domains"], dependencies=[Depends(get_current_learner_id)]
)


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _client_ip(request: Request) -> str:
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


type CurrentLearnerId = Annotated[UUID, Depends(get_current_learner_id)]
type CurrentLLMGateway = Annotated[LLMGateway, Depends(get_llm)]


# --- 1. Auth & Learner Profile (5 endpoints) ---


@_public.post("/auth/register", response_model=AuthResponse, status_code=201)
async def register_endpoint(
    req: RegisterRequest,
    request: Request,
) -> AuthResponse:
    resources = get_resources(request)
    settings = _get_settings(request)
    return await auth.register_learner(resources, settings, req, client_ip=_client_ip(request))


@_public.post("/auth/login", response_model=AuthResponse, status_code=200)
async def login_endpoint(
    req: LoginRequest,
    request: Request,
) -> AuthResponse:
    resources = get_resources(request)
    settings = _get_settings(request)
    return await auth.login_learner(resources, settings, req, client_ip=_client_ip(request))


@_protected.get("/auth/me", response_model=LearnerMeResponse, status_code=200)
async def me_endpoint(
    request: Request,
    learner_id: CurrentLearnerId,
) -> LearnerMeResponse:
    resources = get_resources(request)
    return await auth.get_learner_me(resources, learner_id)


@_protected.patch("/learner/preferences", response_model=UpdatePreferencesResponse, status_code=200)
async def update_preferences_endpoint(
    req: UpdatePreferencesRequest,
    request: Request,
    learner_id: CurrentLearnerId,
) -> UpdatePreferencesResponse:
    resources = get_resources(request)
    return await learner.update_learner_preferences(resources, learner_id, req)


@_protected.delete("/learner", response_model=DeleteLearnerResponse, status_code=200)
async def delete_learner_endpoint(
    request: Request,
    learner_id: CurrentLearnerId,
) -> DeleteLearnerResponse:
    resources = get_resources(request)
    return await learner.delete_learner_account(resources, learner_id)


# --- 2. Domains CRUD (4 endpoints) ---


@_protected.post("/domains", response_model=CreateDomainResponse, status_code=201)
async def create_domain_endpoint(
    req: CreateDomainRequest,
    request: Request,
    learner_id: CurrentLearnerId,
) -> CreateDomainResponse:
    resources = get_resources(request)
    return await service.create_domain(resources, learner_id, req)


@_protected.get("/domains", response_model=DomainListResponse, status_code=200)
async def list_domains_endpoint(
    request: Request,
    learner_id: CurrentLearnerId,
    include_archived: Annotated[bool, Query()] = False,
) -> DomainListResponse:
    resources = get_resources(request)
    return await service.list_domains(resources, learner_id, include_archived=include_archived)


@_protected.get("/domains/{domain_id}", response_model=DomainSummary, status_code=200)
async def get_domain_endpoint(
    domain_id: UUID,
    request: Request,
    learner_id: CurrentLearnerId,
) -> DomainSummary:
    resources = get_resources(request)
    return await service.get_domain(resources, learner_id, domain_id)


@_protected.post(
    "/domains/{domain_id}/archive",
    response_model=ArchiveDomainResponse,
    status_code=200,
)
async def archive_domain_endpoint(
    domain_id: UUID,
    req: ArchiveDomainRequest,
    request: Request,
    learner_id: CurrentLearnerId,
) -> ArchiveDomainResponse:
    resources = get_resources(request)
    return await service.archive_domain(resources, learner_id, domain_id, req)


# --- 3. Structure Onboarding & Editing (4 endpoints) ---


@_protected.post(
    "/domains/{domain_id}/structure/propose",
    response_model=ProposeStructureResponse,
    status_code=200,
)
async def propose_structure_endpoint(
    domain_id: UUID,
    req: ProposeStructureRequest,
    request: Request,
    learner_id: CurrentLearnerId,
    llm: CurrentLLMGateway,
) -> ProposeStructureResponse:
    resources = get_resources(request)
    settings = _get_settings(request)
    return await structure.propose_structure(resources, settings, llm, learner_id, domain_id, req)


@_protected.post(
    "/domains/{domain_id}/structure/confirm",
    response_model=ConfirmStructureResponse,
    status_code=200,
)
async def confirm_structure_endpoint(
    domain_id: UUID,
    req: ConfirmStructureRequest,
    request: Request,
    learner_id: CurrentLearnerId,
) -> ConfirmStructureResponse:
    resources = get_resources(request)
    return await structure.confirm_structure(resources, learner_id, domain_id, req)


@_protected.patch(
    "/domains/{domain_id}/structure",
    response_model=EditStructureResponse,
    status_code=200,
)
async def edit_structure_endpoint(
    domain_id: UUID,
    req: EditStructureRequest,
    request: Request,
    learner_id: CurrentLearnerId,
) -> EditStructureResponse:
    resources = get_resources(request)
    settings = _get_settings(request)
    return await structure.edit_structure(resources, settings, learner_id, domain_id, req)


@_protected.patch(
    "/domains/{domain_id}/topics/{topic_id}/status",
    response_model=UpdateTopicStatusResponse,
    status_code=200,
)
async def update_topic_status_endpoint(
    domain_id: UUID,
    topic_id: UUID,
    req: UpdateTopicStatusRequest,
    request: Request,
    learner_id: CurrentLearnerId,
) -> UpdateTopicStatusResponse:
    resources = get_resources(request)
    return await structure.update_topic_status(resources, learner_id, domain_id, topic_id, req)


# --- 4. Adaptive Diagnostics (3 endpoints) ---


@_protected.post(
    "/domains/{domain_id}/diagnostic/start",
    response_model=StartDiagnosticResponse,
    status_code=200,
)
async def start_diagnostic_endpoint(
    domain_id: UUID,
    req: StartDiagnosticRequest,
    request: Request,
    learner_id: CurrentLearnerId,
    llm: CurrentLLMGateway,
) -> StartDiagnosticResponse:
    resources = get_resources(request)
    settings = _get_settings(request)
    return await diagnostic.start_diagnostic(resources, settings, llm, learner_id, domain_id, req)


@_protected.post(
    "/domains/{domain_id}/diagnostic/answer",
    response_model=AnswerDiagnosticResponse,
    status_code=200,
)
async def answer_diagnostic_endpoint(
    domain_id: UUID,
    req: AnswerDiagnosticRequest,
    request: Request,
    learner_id: CurrentLearnerId,
    llm: CurrentLLMGateway,
) -> AnswerDiagnosticResponse:
    resources = get_resources(request)
    settings = _get_settings(request)
    return await diagnostic.answer_diagnostic(resources, settings, llm, learner_id, domain_id, req)


@_protected.post(
    "/domains/{domain_id}/diagnostic/finish",
    response_model=FinishDiagnosticResponse,
    status_code=200,
)
async def finish_diagnostic_endpoint(
    domain_id: UUID,
    req: FinishDiagnosticRequest,
    request: Request,
    learner_id: CurrentLearnerId,
) -> FinishDiagnosticResponse:
    resources = get_resources(request)
    settings = _get_settings(request)
    return await diagnostic.finish_diagnostic(resources, settings, learner_id, domain_id, req)


# --- 5. Knowledge Map Views for UI (2 endpoints) ---


@_protected.get(
    "/domains/{domain_id}/graph",
    response_model=DomainGraphResponse,
    status_code=200,
)
async def get_domain_graph_endpoint(
    domain_id: UUID,
    request: Request,
    learner_id: CurrentLearnerId,
    include_archived: Annotated[bool, Query()] = False,
) -> DomainGraphResponse:
    resources = get_resources(request)
    settings = _get_settings(request)
    return await graph_view.get_domain_graph_view(
        resources, settings, learner_id, domain_id, include_archived=include_archived
    )


@_protected.get(
    "/domains/{domain_id}/topics/{topic_id}",
    response_model=TopicDetailResponse,
    status_code=200,
)
async def get_topic_detail_endpoint(
    domain_id: UUID,
    topic_id: UUID,
    request: Request,
    learner_id: CurrentLearnerId,
) -> TopicDetailResponse:
    resources = get_resources(request)
    settings = _get_settings(request)
    return await graph_view.get_topic_detail_view(
        resources, settings, learner_id, domain_id, topic_id
    )


# --- 6. Short topic routes (resolve `domain_id` from the learner's events) ---


@_protected.get(
    "/topics/{topic_id}",
    response_model=TopicDetailResponse,
    status_code=200,
)
async def get_topic_detail_short_endpoint(
    topic_id: UUID,
    request: Request,
    learner_id: CurrentLearnerId,
) -> TopicDetailResponse:
    resources = get_resources(request)
    domain_id = await topics.resolve_topic_domain_id(resources, learner_id, topic_id)
    return await get_topic_detail_endpoint(domain_id, topic_id, request, learner_id)


@_protected.patch(
    "/topics/{topic_id}/status",
    response_model=UpdateTopicStatusResponse,
    status_code=200,
)
async def update_topic_status_short_endpoint(
    topic_id: UUID,
    req: UpdateTopicStatusRequest,
    request: Request,
    learner_id: CurrentLearnerId,
) -> UpdateTopicStatusResponse:
    resources = get_resources(request)
    domain_id = await topics.resolve_topic_domain_id(resources, learner_id, topic_id)
    return await update_topic_status_endpoint(domain_id, topic_id, req, request, learner_id)


router = APIRouter()
router.include_router(_public)
router.include_router(_protected)
