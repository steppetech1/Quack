"""Turn pipeline `POST /v1/sessions/{id}/turns` (ТЗ §2.3): lock, context, teacher, commit, SSE."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from quack.api.app import AppResources
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.llm.gateway import LLMGateway
from quack.session.schemas import TurnRequest


async def open_turn(
    resources: AppResources,
    settings: Settings,
    gateway: LLMGateway,
    compute: ComputeClient,
    learner_id: UUID,
    session_id: UUID,
    body: TurnRequest,
    request_id: str,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Take the turn lock (409 TURN_LOCKED if busy) and return the SSE event stream."""
    raise NotImplementedError
