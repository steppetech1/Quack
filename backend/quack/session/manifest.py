"""Turn manifest `ops.turn_manifests` (Т-§3.2, WP14.2).

One row per teacher turn, written in the same transaction as `turn.teacher_replied`; the
full context text is not stored. Cleanup by `turn_manifest_retention_days` is next phase
(R14). The table comes with migration `0002_phase3` (§6.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class TurnManifest:
    turn_id: UUID
    learner_id: UUID
    session_id: UUID
    prompt_version: str
    renderer_version: str
    role: str
    model: str
    reasoning_effort: str
    projected_seq: int
    overlay_seqs: list[int] = field(default_factory=list)
    label_table: dict[str, str] = field(default_factory=dict)
    degraded_mode: str | None = None


async def write_turn_manifest(session: AsyncSession, manifest: TurnManifest) -> None:
    """Insert the row inside the caller's transaction (`T-M4`)."""
    raise NotImplementedError
