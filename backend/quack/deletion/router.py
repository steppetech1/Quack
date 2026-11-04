"""Routes of physical deletion: a domain, a topic, the whole account (ТЗ Ф4 WP25, §6.4; product
package, ТЗ §4.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from quack.api.auth import get_current_learner_id
from quack.api.errors import ApiError
from quack.api.ratelimit import enforce_action_rate_limit
from quack.api.resources import get_resources
from quack.deletion import account as account_deletion
from quack.deletion import domain as deletion
from quack.deletion import topic as topic_deletion
from quack.llm.tracing import learner_pseudonym
from quack.storage.deletions import DeletionJournalUnavailable

router = APIRouter(
    prefix="/v1",
    tags=["deletion"],
    dependencies=[Depends(get_current_learner_id), Depends(enforce_action_rate_limit)],
)

type CurrentLearnerId = Annotated[UUID, Depends(get_current_learner_id)]


def _not_yours() -> ApiError:
    return ApiError("NOT_FOUND", "no such target of this learner", status_code=404)


class DeletionResponse(BaseModel):
    """The id of the deletion and the term the copies still live (Д-§8.6)."""

    deletion_id: UUID
    copies_days: int


@router.delete("/domains/{domain_id}", response_model=DeletionResponse, status_code=200)
async def delete_domain_endpoint(
    domain_id: UUID, request: Request, learner_id: CurrentLearnerId
) -> DeletionResponse:
    r = get_resources(request)
    try:
        done = await deletion.delete_domain(
            r.db, r.neo4j, r.settings, r.deletions, r.storage, learner_id, domain_id,
            datetime.now(UTC), redis=r.redis,
        )  # fmt: skip
    except (DeletionJournalUnavailable, deletion.DeletionBusy) as exc:
        raise ApiError("SERVICE_UNAVAILABLE", "deletion is not possible now, repeat later",
                       status_code=503) from exc  # fmt: skip
    if done is None:
        raise _not_yours()
    return DeletionResponse(deletion_id=done.deletion_id, copies_days=done.copies_days)


@router.delete("/topics/{topic_id}", response_model=DeletionResponse, status_code=200)
async def delete_topic_endpoint(
    topic_id: UUID, request: Request, learner_id: CurrentLearnerId
) -> DeletionResponse:
    """WP25: the physical deletion of a topic (privacy); «убрать с графа» is `…/hide`."""
    r = get_resources(request)
    try:
        done = await topic_deletion.delete_topic(
            r.db, r.neo4j, r.settings, r.deletions, r.storage, learner_id, topic_id,
            datetime.now(UTC), redis=r.redis,
        )  # fmt: skip
    except (DeletionJournalUnavailable, deletion.DeletionBusy) as exc:
        raise ApiError("SERVICE_UNAVAILABLE", "deletion is not possible now, repeat later",
                       status_code=503) from exc  # fmt: skip
    if done is None:
        raise _not_yours()
    return DeletionResponse(deletion_id=done.deletion_id, copies_days=done.copies_days)


@router.delete("/learner", response_model=DeletionResponse, status_code=200)
async def delete_learner_endpoint(
    request: Request, learner_id: CurrentLearnerId
) -> DeletionResponse:
    """WP25 п.3: the whole account — rows, objects, the subgraph, the learner's traces by their
    pseudonym (D-A-206). The session goes with the rows: the next request is `401`."""
    r = get_resources(request)
    pseudonym = learner_pseudonym(r.settings.langsmith_pseudonym_ns, learner_id)
    try:
        done = await account_deletion.delete_account(
            r.db, r.neo4j, r.deletions, r.storage, learner_id, datetime.now(UTC),
            redis=r.redis, tracer=r.llm.tracer, pseudonym=pseudonym,
        )  # fmt: skip
    except (DeletionJournalUnavailable, deletion.DeletionBusy) as exc:
        raise ApiError("SERVICE_UNAVAILABLE", "deletion is not possible now, repeat later",
                       status_code=503) from exc  # fmt: skip
    return DeletionResponse(deletion_id=done.deletion_id, copies_days=done.copies_days)
