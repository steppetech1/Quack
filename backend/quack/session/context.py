"""Teacher context assembly (Т-§5.3): graph slice + session overlay + checkpoint + active window."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from quack.api.app import AppResources
from quack.config import Settings
from quack.graph.overlay import SessionOverlay


@dataclass(frozen=True)
class TurnContext:
    topic_id: UUID | None
    topic_name: str
    mode: str
    lag_degraded: bool
    overlay: SessionOverlay
    rolling_summary: str
    open_threads: list[str]
    recent_turns: list[dict[str, Any]]
    skills_slice: list[dict[str, Any]] = field(default_factory=list)
    misconceptions: list[dict[str, Any]] = field(default_factory=list)


async def build_turn_context(
    resources: AppResources, settings: Settings, learner_id: UUID, session_id: UUID
) -> TurnContext:
    """Consistent slice (<= projector_lag_timeout_ms) + overlay + last checkpoint + <= 12 turns."""
    raise NotImplementedError
