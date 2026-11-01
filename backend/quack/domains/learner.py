"""Learner preferences update and two-phase account deletion (ТЗ §2.1, §3; Т-§16.3, Т-§22.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import orjson
from sqlalchemy import text

from quack.api.errors import ApiError
from quack.api.resources import AppResources
from quack.domains.schemas import (
    DeleteLearnerResponse,
    UpdatePreferencesRequest,
    UpdatePreferencesResponse,
)
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.jobs.queue import cancel_learner_jobs


async def update_learner_preferences(
    resources: AppResources,
    learner_id: UUID,
    req: UpdatePreferencesRequest,
) -> UpdatePreferencesResponse:
    """Update `auth.users.preferences` and emit `learner.preferences_updated`
    (ТЗ §2.1, §3; Т-§16.3, Т-§22.2).
    """
    async with resources.session_factory() as session, session.begin():
        stored_events = await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="learner.preferences_updated",
                    actor="learner",
                    idempotency_key=req.idempotency_key,
                    payload={"preferences": req.preferences},
                )
            ],
            publish_wakeup=True,
            redis=resources.redis,
        )
        if not stored_events:
            raise ApiError(
                "VALIDATION_ERROR", "failed to record preferences event", status_code=422
            )

        updated_row = await session.execute(
            text(
                "UPDATE auth.users "
                "SET preferences = auth.users.preferences || CAST(:prefs AS JSONB) "
                "WHERE id = :id AND deleted_at IS NULL "
                "RETURNING preferences"
            ),
            {"id": learner_id, "prefs": orjson.dumps(req.preferences).decode()},
        )
        user_prefs: dict[str, Any] | None = updated_row.scalar_one_or_none()
        if user_prefs is None:
            raise ApiError("UNAUTHORIZED", "learner not found or deleted", status_code=401)

    return UpdatePreferencesResponse(
        preferences=user_prefs,
        seq=stored_events[0].seq,
    )


async def delete_learner_account(
    resources: AppResources,
    learner_id: UUID,
) -> DeleteLearnerResponse:
    """Two-phase account deletion: mark `auth.users.deleted_at = now()`, cancel queued jobs in
    `ops.jobs`, and insert `pending_purge` into `ops.deletion_tombstones` (ТЗ §2.1, §3; Т-§16.3).
    """
    now = datetime.now(UTC)
    async with resources.session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE auth.users SET deleted_at = :now WHERE id = :id AND deleted_at IS NULL"),
            {"id": learner_id, "now": now},
        )
        await cancel_learner_jobs(session, learner_id)
        await session.execute(
            text(
                "INSERT INTO ops.deletion_tombstones (learner_id, requested_at, status) "
                "VALUES (:id, :now, 'pending_purge') "
                "ON CONFLICT (learner_id) DO NOTHING"
            ),
            {"id": learner_id, "now": now},
        )

    return DeleteLearnerResponse(status="pending_purge", requested_at=now)
