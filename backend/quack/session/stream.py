"""`GET /v1/sessions/{id}/events`: SSE relay of the Redis channel `session:events:{id}`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from quack.api.app import AppResources


def stream_session_events(
    resources: AppResources, learner_id: UUID, session_id: UUID
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yield `extractor_done` / `cheatsheet_ready` events of an owned session."""
    raise NotImplementedError
