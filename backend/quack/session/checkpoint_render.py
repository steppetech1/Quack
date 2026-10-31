"""Deterministic session summary for `session.checkpoint` (law 10, Т-§10.3, WP13).

No model call and no previous summary on input: `rolling_summary`, `open_threads` and
`key_observations` are built by templates from the session events only (`turn.extracted`,
`task.evaluated`, turn events). Episode lines are English (Т-§22.2). Events of the
`session.checkpointed` keep their schema; the renderer version is `renderer@1` (D-B-002).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from quack.config import Settings
from quack.events.models import StoredEvent

RENDERER_VERSION = "renderer@1"


@dataclass(frozen=True)
class RenderedCheckpoint:
    """Fields of the `session.checkpointed` payload built by code."""

    up_to_turn_index: int
    rolling_summary: str
    open_threads: list[str]
    key_observations: list[str]


def render_checkpoint(
    events: Sequence[StoredEvent], up_to_turn_index: int, settings: Settings
) -> RenderedCheckpoint:
    """Same events → byte-identical result (`T-C2`); earlier summaries are ignored (`T-C3`)."""
    raise NotImplementedError


def episode_lines(events: Sequence[StoredEvent], settings: Settings) -> list[str]:
    """Collapsed lines of the oldest `conversation_collapse_turns` turns once the verbatim
    window exceeds `conversation_verbatim_max_turns`, in whole batches (`T-C4`)."""
    raise NotImplementedError
