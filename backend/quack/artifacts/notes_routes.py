"""Routes of topic notes in blocks (ТЗ Ф4 WP23.1, §6.4 «Артефакты»; Т-§17.2, П-11.1).

`GET /v1/topics/{id}/notes` — the current version; `PUT /v1/artifacts/{id}/blocks/{block_id}` —
the learner edits a block or writes a new one; `POST …/accept` — a supplement is merged into the
learner's block; `DELETE …/blocks/{block_id}` — a new version without the block. Every save is a
new version and `artifact.updated` (`quack.artifacts.documents`).
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from quack.api.auth import get_current_learner_id
from quack.api.errors import ApiError
from quack.api.ratelimit import enforce_action_rate_limit
from quack.api.resources import AppResources, get_resources
from quack.artifacts.documents import (
    Artifact,
    ArtifactNotFound,
    NotASupplement,
    accept_supplement,
    delete_block,
    edit_block,
    get_artifact,
)

# Router-level auth: 401 before body validation (ТЗ Фазы 3 WP1.2, T-A2).
router = APIRouter(
    prefix="/v1",
    tags=["artifacts"],
    dependencies=[Depends(get_current_learner_id), Depends(enforce_action_rate_limit)],
)

type CurrentLearnerId = Annotated[UUID, Depends(get_current_learner_id)]
type Resources = Annotated[AppResources, Depends(get_resources)]


class KeyedRequest(BaseModel):
    """§6.4: a changing path carries `idempotency_key` (T-E2)."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1)


class BlockEdit(KeyedRequest):
    content: str


class ArtifactResponse(BaseModel):
    """An artifact at its current version; blocks with the names of Т-§17.2."""

    artifact_id: UUID
    kind: str
    topic_id: UUID | None
    hub_id: UUID | None
    version: int
    blocks: list[dict[str, Any]]


def _response(artifact: Artifact) -> ArtifactResponse:
    return ArtifactResponse(
        artifact_id=artifact.artifact_id,
        kind=artifact.kind,
        topic_id=artifact.topic_id,
        hub_id=artifact.hub_id,
        version=artifact.version,
        blocks=[block.model_dump(mode="json", by_alias=True) for block in artifact.blocks],
    )


def _not_found(exc: ArtifactNotFound) -> ApiError:
    return ApiError("NOT_FOUND", str(exc), status_code=404)


@router.get("/topics/{topic_id}/notes", response_model=ArtifactResponse)
async def topic_notes(
    topic_id: UUID, learner_id: CurrentLearnerId, resources: Resources
) -> ArtifactResponse:
    try:
        notes = await get_artifact(
            resources.db, learner_id=learner_id, kind="notes", owner_id=topic_id
        )
    except ArtifactNotFound as exc:
        raise _not_found(exc) from exc
    return _response(notes)


@router.put("/artifacts/{artifact_id}/blocks/{block_id}", response_model=ArtifactResponse)
async def put_block(
    artifact_id: UUID,
    block_id: UUID,
    body: BlockEdit,
    learner_id: CurrentLearnerId,
    resources: Resources,
) -> ArtifactResponse:
    try:
        artifact = await edit_block(
            resources.db,
            learner_id=learner_id,
            artifact_id=artifact_id,
            block_id=block_id,
            content=body.content,
            idempotency_key=body.idempotency_key,
            redis=resources.redis,
        )
    except ArtifactNotFound as exc:
        raise _not_found(exc) from exc
    return _response(artifact)


@router.post("/artifacts/{artifact_id}/blocks/{block_id}/accept", response_model=ArtifactResponse)
async def accept_block(
    artifact_id: UUID,
    block_id: UUID,
    body: KeyedRequest,
    learner_id: CurrentLearnerId,
    resources: Resources,
) -> ArtifactResponse:
    try:
        artifact = await accept_supplement(
            resources.db,
            learner_id=learner_id,
            artifact_id=artifact_id,
            block_id=block_id,
            idempotency_key=body.idempotency_key,
            redis=resources.redis,
        )
    except ArtifactNotFound as exc:
        raise _not_found(exc) from exc
    except NotASupplement as exc:
        raise ApiError("VALIDATION_ERROR", str(exc), status_code=422) from exc
    return _response(artifact)


@router.delete("/artifacts/{artifact_id}/blocks/{block_id}", response_model=ArtifactResponse)
async def remove_block(
    artifact_id: UUID,
    block_id: UUID,
    body: KeyedRequest,
    learner_id: CurrentLearnerId,
    resources: Resources,
) -> ArtifactResponse:
    try:
        artifact = await delete_block(
            resources.db,
            learner_id=learner_id,
            artifact_id=artifact_id,
            block_id=block_id,
            idempotency_key=body.idempotency_key,
            redis=resources.redis,
        )
    except ArtifactNotFound as exc:
        raise _not_found(exc) from exc
    return _response(artifact)
