"""Turn manifest `ops.turn_manifests` (Т-§3.2, WP14.2).

One row per teacher turn, written in the same transaction as `turn.teacher_replied`; the
full context text is not stored. Cleanup by `turn_manifest_retention_days` is next phase
(R14). The table comes with migration `0002_phase3` (§6.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class TurnManifest:
    turn_id: UUID
    learner_id: UUID
    session_id: UUID
    # Column types of migration `0002_phase3`: versions are the numbers N of `name@N`.
    prompt_version: int
    renderer_version: int | None
    role: str
    model: str
    reasoning_effort: str | None
    projected_seq: int
    overlay_seqs: list[int] = field(default_factory=list)
    label_table: dict[str, str] = field(default_factory=dict)
    degraded_mode: str | None = None


async def write_turn_manifest(session: AsyncSession, manifest: TurnManifest) -> None:
    """Insert the row inside the caller's transaction (`T-M4`)."""
    await session.execute(
        text(
            "INSERT INTO ops.turn_manifests (turn_id, learner_id, session_id, prompt_version, "
            "renderer_version, role, model, reasoning_effort, projected_seq, overlay_seqs, "
            "label_table, degraded_mode) VALUES (:turn_id, :learner_id, :session_id, "
            ":prompt_version, :renderer_version, :role, :model, :reasoning_effort, "
            ":projected_seq, :overlay_seqs, CAST(:label_table AS jsonb), :degraded_mode)"
        ),
        {
            "turn_id": manifest.turn_id,
            "learner_id": manifest.learner_id,
            "session_id": manifest.session_id,
            "prompt_version": manifest.prompt_version,
            "renderer_version": manifest.renderer_version,
            "role": manifest.role,
            "model": manifest.model,
            "reasoning_effort": manifest.reasoning_effort,
            "projected_seq": manifest.projected_seq,
            "overlay_seqs": list(manifest.overlay_seqs),
            "label_table": orjson.dumps(manifest.label_table).decode(),
            "degraded_mode": manifest.degraded_mode,
        },
    )
