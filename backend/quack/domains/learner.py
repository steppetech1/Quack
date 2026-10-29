"""Learner preferences update and two-phase account deletion (ТЗ §2.1, §3; Т-§16.3, Т-§22.2)."""

from __future__ import annotations

from uuid import UUID

from quack.api.app import AppResources
from quack.domains.schemas import (
    DeleteLearnerResponse,
    UpdatePreferencesRequest,
    UpdatePreferencesResponse,
)


async def update_learner_preferences(
    resources: AppResources,
    learner_id: UUID,
    req: UpdatePreferencesRequest,
) -> UpdatePreferencesResponse:
    """Update `auth.users.preferences` and emit `learner.preferences_updated`
    (ТЗ §2.1, §3; Т-§16.3, Т-§22.2).
    """
    raise NotImplementedError


async def delete_learner_account(
    resources: AppResources,
    learner_id: UUID,
) -> DeleteLearnerResponse:
    """Two-phase account deletion: mark `auth.users.deleted_at = now()`, cancel queued jobs in
    `ops.jobs`, and insert `pending_purge` into `ops.deletion_tombstones` (ТЗ §2.1, §3; Т-§16.3).
    """
    raise NotImplementedError
