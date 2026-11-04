"""Routes of the hub cheat sheet (ТЗ Ф4 WP23.5, §6.4 «Артефакты»; Т-§17.6, Т-§17.7; D-C-144).

`GET /v1/hubs/{id}/cheatsheet` — the document and the state of the offer (read, not a job);
`POST /v1/hubs/{id}/cheatsheet` — the consent: `agent.requested`, which the dispatcher turns into
the job `artifact.cheatsheet`; `POST /v1/hubs/{id}/cheatsheet/review` — «собери сам — я проверю»:
remarks on the learner's own sheet. The learner writes the blocks with `PUT /v1/artifacts/{id}/
blocks/{block_id}` (`quack.artifacts.notes_routes`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from quack.api.auth import get_current_learner_id
from quack.api.errors import ApiError
from quack.api.ratelimit import enforce_action_rate_limit
from quack.api.resources import AppResources, get_resources
from quack.artifacts.documents import Artifact, ArtifactNotFound, get_artifact
from quack.artifacts.hub_cheatsheet import Prefetch, request_hub_cheatsheet, review_hub_cheatsheet
from quack.artifacts.notes_routes import ArtifactResponse, KeyedRequest
from quack.graph.read import GraphReader, create_graph_reader
from quack.graph.read.cheatsheet_offer import cheatsheet_offer
from quack.graph.read.materials import ChunkHit, materials_prefetch

# Router-level auth: 401 before body validation (ТЗ Фазы 3 WP1.2, T-A2).
router = APIRouter(
    prefix="/v1",
    tags=["artifacts"],
    dependencies=[Depends(get_current_learner_id), Depends(enforce_action_rate_limit)],
)

type CurrentLearnerId = Annotated[UUID, Depends(get_current_learner_id)]
type Resources = Annotated[AppResources, Depends(get_resources)]


@dataclass(frozen=True)
class Memory:
    """What the review reads from the memory: the graph reader of the learner and the prefetch of
    material chunks (S4, §4.1). A dependency, so tests put a fixed one."""

    reader: GraphReader
    prefetch: Prefetch


def get_memory(resources: Resources, learner_id: CurrentLearnerId) -> Memory:
    async def prefetch(skill_ids: Sequence[UUID]) -> list[ChunkHit]:
        return await materials_prefetch(
            resources.neo4j,
            learner_id=learner_id,
            skill_ids=skill_ids,
            k=resources.settings.materials_k,
        )

    reader = create_graph_reader(resources.neo4j, resources.settings, learner_id=learner_id)
    return Memory(reader, prefetch)


type MemoryOf = Annotated[Memory, Depends(get_memory)]


class CheatsheetResponse(ArtifactResponse):
    """The sheet at its current version and whether to offer it now (D-C-144)."""

    offer: bool


class ConsentResponse(BaseModel):
    """The consent request; a repeat with the same key gives the same one."""

    request_id: UUID


class ReviewResponse(ArtifactResponse):
    """The sheet after the review; `remarks` — the system blocks added to the learner's sheet."""

    remarks: list[dict[str, Any]]


def _view(artifact: Artifact) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "kind": artifact.kind,
        "topic_id": artifact.topic_id,
        "hub_id": artifact.hub_id,
        "version": artifact.version,
        "blocks": [block.model_dump(mode="json", by_alias=True) for block in artifact.blocks],
    }


def _not_found(exc: ArtifactNotFound) -> ApiError:
    return ApiError("NOT_FOUND", str(exc), status_code=404)


@router.get("/hubs/{hub_id}/cheatsheet", response_model=CheatsheetResponse)
async def get_hub_cheatsheet(
    hub_id: UUID, learner_id: CurrentLearnerId, resources: Resources
) -> CheatsheetResponse:
    try:
        sheet = await get_artifact(
            resources.db, learner_id=learner_id, kind="cheatsheet", owner_id=hub_id
        )
    except ArtifactNotFound as exc:
        raise _not_found(exc) from exc
    offer = await cheatsheet_offer(resources.db, learner_id, hub_id, resources.settings)
    return CheatsheetResponse(**_view(sheet), offer=offer)


@router.post("/hubs/{hub_id}/cheatsheet", response_model=ConsentResponse, status_code=202)
async def agree_to_hub_cheatsheet(
    hub_id: UUID, body: KeyedRequest, learner_id: CurrentLearnerId, resources: Resources
) -> ConsentResponse:
    try:
        request_id = await request_hub_cheatsheet(
            resources.db, learner_id, hub_id, idempotency_key=body.idempotency_key
        )
    except ArtifactNotFound as exc:
        raise _not_found(exc) from exc
    return ConsentResponse(request_id=request_id)


@router.post("/hubs/{hub_id}/cheatsheet/review", response_model=ReviewResponse)
async def review_cheatsheet(
    hub_id: UUID,
    body: KeyedRequest,
    learner_id: CurrentLearnerId,
    resources: Resources,
    memory: MemoryOf,
) -> ReviewResponse:
    try:
        review = await review_hub_cheatsheet(
            resources.db,
            resources.llm,
            memory.reader,
            memory.prefetch,
            resources.settings,
            learner_id=learner_id,
            hub_id=hub_id,
            key=body.idempotency_key,
            compute=resources.compute,
            redis=resources.redis,
        )
    except ArtifactNotFound as exc:
        raise _not_found(exc) from exc
    remarks = [block.model_dump(mode="json", by_alias=True) for block in review.remarks]
    return ReviewResponse(**_view(review.artifact), remarks=remarks)
