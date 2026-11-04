"""Routes of topic cards (ТЗ Ф4 WP23.4, §6.4 «Артефакты»; Т-§17.5, П-11.2; D-C-140).

`GET /v1/topics/{id}/cards` — the current version; `PATCH /v1/cards/{id}` — the learner rewrites
a card in one touch; `DELETE /v1/cards/{id}` — a new version without it. An answer to a card is
the session action `answer` with `source = card` (stream B), not a route here.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from quack.api.auth import get_current_learner_id
from quack.api.errors import ApiError
from quack.api.ratelimit import enforce_action_rate_limit
from quack.api.resources import AppResources, get_resources
from quack.artifacts.cards import delete_card, edit_card, topic_cards
from quack.artifacts.documents import Artifact, ArtifactNotFound
from quack.artifacts.notes_routes import KeyedRequest

# Router-level auth: 401 before body validation (ТЗ Фазы 3 WP1.2, T-A2).
router = APIRouter(
    prefix="/v1",
    tags=["artifacts"],
    dependencies=[Depends(get_current_learner_id), Depends(enforce_action_rate_limit)],
)

type CurrentLearnerId = Annotated[UUID, Depends(get_current_learner_id)]
type Resources = Annotated[AppResources, Depends(get_resources)]


class CardEdit(KeyedRequest):
    prompt: str = Field(min_length=1)


class CardsResponse(BaseModel):
    """The topic's cards at the current version; `card_id` is the task to answer."""

    artifact_id: UUID
    topic_id: UUID | None
    version: int
    cards: list[dict[str, Any]]


def _response(artifact: Artifact) -> CardsResponse:
    return CardsResponse(
        artifact_id=artifact.artifact_id,
        topic_id=artifact.topic_id,
        version=artifact.version,
        cards=[card.model_dump(mode="json", by_alias=True) for card in artifact.cards],
    )


def _not_found(exc: ArtifactNotFound) -> ApiError:
    return ApiError("NOT_FOUND", str(exc), status_code=404)


@router.get("/topics/{topic_id}/cards", response_model=CardsResponse)
async def get_topic_cards(
    topic_id: UUID, learner_id: CurrentLearnerId, resources: Resources
) -> CardsResponse:
    try:
        cards = await topic_cards(resources.db, learner_id=learner_id, topic_id=topic_id)
    except ArtifactNotFound as exc:
        raise _not_found(exc) from exc
    return _response(cards)


@router.patch("/cards/{card_id}", response_model=CardsResponse)
async def patch_card(
    card_id: UUID, body: CardEdit, learner_id: CurrentLearnerId, resources: Resources
) -> CardsResponse:
    try:
        artifact = await edit_card(
            resources.db,
            learner_id=learner_id,
            card_id=card_id,
            prompt=body.prompt,
            idempotency_key=body.idempotency_key,
            redis=resources.redis,
        )
    except ArtifactNotFound as exc:
        raise _not_found(exc) from exc
    return _response(artifact)


@router.delete("/cards/{card_id}", response_model=CardsResponse)
async def remove_card(
    card_id: UUID, body: KeyedRequest, learner_id: CurrentLearnerId, resources: Resources
) -> CardsResponse:
    try:
        artifact = await delete_card(
            resources.db,
            learner_id=learner_id,
            card_id=card_id,
            idempotency_key=body.idempotency_key,
            redis=resources.redis,
        )
    except ArtifactNotFound as exc:
        raise _not_found(exc) from exc
    return _response(artifact)
