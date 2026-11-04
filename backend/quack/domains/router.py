"""FastAPI router of stream A (`/v1/learner/preferences`, `/v1/domains/*`, `/v1/topics/*`,
`/v1/hubs/*`, disputes) wiring its HTTP endpoints to the modules of the package (ТЗ Ф4 §6.4).

Register, login and `GET /v1/auth/me` are removed with the password and JWT (ТЗ Ф4 §2.3 row 3,
WP18.3, D-A-113); sign-in is `quack.auth` (WP18). The interview of the structure, the diagnostic,
the archive of a domain and the status of a topic are removed with their events (§2.3 row 5,
D-A-207); `DELETE /v1/learner` is in `quack.deletion.router`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from quack.api.auth import get_current_learner_id
from quack.api.errors import ApiError
from quack.api.ratelimit import enforce_action_rate_limit
from quack.api.resources import get_llm, get_resources
from quack.domains import disputes, graph_reads, graph_view, learner, service, topic_edits
from quack.domains.schemas import (
    CreateDomainRequest,
    CreateDomainResponse,
    DomainGraphResponse,
    DomainListResponse,
    SetPreferenceRequest,
    SetPreferenceResponse,
)
from quack.graph.read.models import HubGoal, WhyFacts
from quack.llm.gateway import LLMGateway
from quack.practice import domain_classify
from quack.practice.domain_classify import ClassifyDomainRequest, DomainSuggestion

# Every route authenticates at router level, so a request without a session gets 401 before
# body validation (ТЗ Фазы 3 WP1.2, T-A2), and its mutating requests count in the `actions`
# limit.
_protected = APIRouter(
    prefix="/v1",
    tags=["domains"],
    dependencies=[Depends(get_current_learner_id), Depends(enforce_action_rate_limit)],
)

type CurrentLearnerId = Annotated[UUID, Depends(get_current_learner_id)]
type CurrentLLMGateway = Annotated[LLMGateway, Depends(get_llm)]


def _not_yours() -> ApiError:
    return ApiError("NOT_FOUND", "no such target of this learner", status_code=404)


# --- 1. The learner's preferences ---


@_protected.patch("/learner/preferences", response_model=SetPreferenceResponse, status_code=200)
async def set_preference_endpoint(
    req: SetPreferenceRequest, request: Request, learner_id: CurrentLearnerId
) -> SetPreferenceResponse:
    r = get_resources(request)
    return await learner.set_preference(r.db, r.redis, learner_id, req)


# --- 2. Domains ---


@_protected.post("/domains", response_model=CreateDomainResponse, status_code=201)
async def create_domain_endpoint(
    req: CreateDomainRequest, request: Request, learner_id: CurrentLearnerId
) -> CreateDomainResponse:
    r = get_resources(request)
    return await service.create_domain(r.db, r.redis, learner_id, req)


# ТЗ Ф4 WP16.4, §7.3 п.3: the body is stream B's (`quack.practice.domain_classify`, D-B-173).
@_protected.post("/domains/classify", response_model=DomainSuggestion, status_code=200)
async def classify_domain_endpoint(
    req: ClassifyDomainRequest,
    request: Request,
    learner_id: CurrentLearnerId,
    llm: CurrentLLMGateway,
) -> DomainSuggestion:
    resources = get_resources(request)
    return await domain_classify.classify_domain(
        resources.db, llm, resources.settings, learner_id=learner_id, request=req
    )


@_protected.get("/domains", response_model=DomainListResponse, status_code=200)
async def list_domains_endpoint(
    request: Request, learner_id: CurrentLearnerId
) -> DomainListResponse:
    return await service.list_domains(get_resources(request).db, learner_id)


# --- 3. The map and the card of a topic (codes, not text; Т-§22.2, T-TH5) ---


@_protected.get("/domains/{domain_id}/graph", response_model=DomainGraphResponse, status_code=200)
async def get_domain_graph_endpoint(
    domain_id: UUID,
    request: Request,
    learner_id: CurrentLearnerId,
    include_hidden: Annotated[bool, Query()] = False,
) -> DomainGraphResponse:
    r = get_resources(request)
    graph = await graph_view.domain_graph(
        r.db, r.neo4j, r.settings, learner_id, domain_id, include_hidden=include_hidden
    )
    if graph is None:
        raise _not_yours()
    return graph


@_protected.get("/topics/{topic_id}", response_model=graph_reads.TopicView, status_code=200)
async def get_topic_endpoint(
    topic_id: UUID, request: Request, learner_id: CurrentLearnerId
) -> graph_reads.TopicView:
    r = get_resources(request)
    view = await graph_reads.topic_view(r.db, r.neo4j, learner_id, topic_id)
    if view is None:
        raise _not_yours()
    return view


# --- ТЗ Ф4 WP8.1: «не согласен» and «это была описка» (Т-§2.8, Т-§7.6) ---


@_protected.post("/disputes", response_model=disputes.EventAccepted, status_code=202)
async def file_dispute_endpoint(
    req: disputes.DisputeRequest, request: Request, learner_id: CurrentLearnerId
) -> disputes.EventAccepted:
    r = get_resources(request)
    seq = await disputes.file_dispute(r.db, r.neo4j, r.redis, learner_id, req)
    if seq is None:
        raise _not_yours()
    return disputes.EventAccepted(seq=seq)


@_protected.post(
    "/verdicts/{answer_seq}/typo", response_model=disputes.EventAccepted, status_code=202
)
async def declare_typo_endpoint(
    answer_seq: int, req: disputes.TypoRequest, request: Request, learner_id: CurrentLearnerId
) -> disputes.EventAccepted:
    r = get_resources(request)
    seq = await disputes.declare_typo(r.db, r.redis, learner_id, answer_seq, req.part)
    if seq is None:
        raise _not_yours()
    return disputes.EventAccepted(seq=seq)


# --- ТЗ Ф4 WP8.2: edits of topics and «я это уже знаю» (Т-§4.11) ---


def _edited(seq: int | None) -> topic_edits.Edited:
    if seq is None:
        raise _not_yours()
    return topic_edits.Edited(seq=seq)


@_protected.patch("/topics/{topic_id}", response_model=topic_edits.Edited, status_code=202)
async def rename_topic_endpoint(
    topic_id: UUID, req: topic_edits.RenameTopic, request: Request, learner_id: CurrentLearnerId
) -> topic_edits.Edited:
    r = get_resources(request)
    return _edited(await topic_edits.rename(r.db, r.neo4j, r.redis, learner_id, topic_id, req.name))


@_protected.post("/topics/{topic_id}/merge", response_model=topic_edits.Edited, status_code=202)
async def merge_topic_endpoint(
    topic_id: UUID, req: topic_edits.MergeTopic, request: Request, learner_id: CurrentLearnerId
) -> topic_edits.Edited:
    r = get_resources(request)
    return _edited(await topic_edits.merge(r.db, r.neo4j, r.redis, learner_id, topic_id, req.into))


@_protected.post("/topics/{topic_id}/hide", response_model=topic_edits.Edited, status_code=202)
async def hide_topic_endpoint(
    topic_id: UUID, request: Request, learner_id: CurrentLearnerId
) -> topic_edits.Edited:
    r = get_resources(request)
    return _edited(await topic_edits.hide(r.db, r.neo4j, r.redis, learner_id, topic_id))


@_protected.post("/topics/{topic_id}/known", response_model=topic_edits.Edited, status_code=202)
async def claim_known_endpoint(
    topic_id: UUID, request: Request, learner_id: CurrentLearnerId
) -> topic_edits.Edited:
    r = get_resources(request)
    return _edited(await topic_edits.claim_known(r.db, r.neo4j, r.redis, learner_id, topic_id))


@_protected.post(
    "/episodes/{episode_seq}/reassign", response_model=topic_edits.Edited, status_code=202
)
async def reassign_episode_endpoint(
    episode_seq: int,
    req: topic_edits.ReassignEpisode,
    request: Request,
    learner_id: CurrentLearnerId,
) -> topic_edits.Edited:
    r = get_resources(request)
    seq = await topic_edits.reassign(r.db, r.neo4j, r.redis, learner_id, episode_seq, req.topic_id)
    return _edited(seq)


@_protected.patch("/hubs/{hub_id}", response_model=topic_edits.Edited, status_code=202)
async def edit_hub_endpoint(
    hub_id: UUID, req: topic_edits.EditHub, request: Request, learner_id: CurrentLearnerId
) -> topic_edits.Edited:
    """WP6.3, ТЗ §6.4 «Хабы»: the learner corrects the scope and the date (Т-§4.8)."""
    r = get_resources(request)
    return _edited(await topic_edits.edit_hub(r.db, r.neo4j, r.redis, learner_id, hub_id, req))


# --- ТЗ Ф4 WP6.5: reads of topics and hubs (codes, not text; Т-§22.2) ---


@_protected.get("/topics/{topic_id}/why", response_model=WhyFacts, status_code=200)
async def topic_why_endpoint(
    topic_id: UUID,
    request: Request,
    learner_id: CurrentLearnerId,
    level: Annotated[int | None, Query(ge=1, le=6)] = None,
) -> WhyFacts:
    r = get_resources(request)
    why = await graph_reads.topic_why(r.db, r.neo4j, r.settings, learner_id, topic_id, level)
    if why is None:
        raise _not_yours()
    return why


@_protected.get("/domains/{domain_id}/hubs", response_model=list[HubGoal], status_code=200)
async def domain_hubs_endpoint(
    domain_id: UUID, request: Request, learner_id: CurrentLearnerId
) -> list[HubGoal]:
    r = get_resources(request)
    hubs = await graph_reads.domain_hubs(r.neo4j, r.settings, learner_id, domain_id)
    if hubs is None:
        raise _not_yours()
    return hubs


@_protected.post(
    "/domains/{domain_id}/hubs", response_model=graph_reads.HubCreated, status_code=201
)
async def create_hub_endpoint(
    domain_id: UUID, req: graph_reads.CreateHub, request: Request, learner_id: CurrentLearnerId
) -> graph_reads.HubCreated:
    r = get_resources(request)
    created = await graph_reads.create_hub(r.db, r.neo4j, r.redis, learner_id, domain_id, req)
    if created is None:
        raise _not_yours()
    return created


@_protected.get("/hubs/{hub_id}", response_model=graph_reads.HubView, status_code=200)
async def hub_view_endpoint(
    hub_id: UUID, request: Request, learner_id: CurrentLearnerId
) -> graph_reads.HubView:
    r = get_resources(request)
    view = await graph_reads.hub_view(r.neo4j, r.settings, learner_id, hub_id)
    if view is None:
        raise _not_yours()
    return view


router = APIRouter()
router.include_router(_protected)
