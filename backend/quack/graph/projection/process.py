"""The loop of the `projector` process on `rules@3` (ТЗ Ф4 WP1.3, WP3.3; Т-§2.4, Т-§22.4).

Every `consumer_poll_ms`: the learners whose counter is ahead of the copy of their watermark
(`core.learners.seq > ops.projector_offsets.seq`) and whose stream is not stopped
(`ops.projector_failures`), one batch each (`project_learner`). A failure of one learner — a
temporary Neo4j error — is logged and does not stop the others; the next pass repeats it.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from uuid import UUID

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.graph.projection.runner import project_learner
from quack.logging import get_logger

log = get_logger(__name__)

# The projector walks every learner (Т-§2.4): a cross-learner scan (D-A-200).
_PENDING = (
    "SELECT l.learner_id FROM core.learners l "
    "LEFT JOIN ops.projector_offsets o ON o.learner_id = l.learner_id "
    "WHERE l.seq > COALESCE(o.seq, 0) AND NOT EXISTS "
    "(SELECT 1 FROM ops.projector_failures f WHERE f.learner_id = l.learner_id) "
    "ORDER BY l.learner_id LIMIT %(limit)s"
)


@dataclass
class ProjectorP4:
    settings: Settings
    db: Db
    neo4j: AsyncDriver
    redis: Redis | None = None

    async def pending_learners(self, limit: int = 100) -> list[UUID]:
        rows = await self.db.fetch_all(_PENDING, {"limit": limit})
        return [r["learner_id"] for r in rows]

    async def run_once(self) -> int:
        """One batch for each lagging learner; returns how many batches were applied."""
        applied = 0
        for learner_id in await self.pending_learners(self.settings.dispatcher_batch_size):
            try:
                outcome = await project_learner(
                    self.db, self.neo4j, self.settings, learner_id, redis=self.redis
                )
            except Exception as exc:  # a temporary Neo4j error: the next pass repeats the batch
                log.warning("projector_batch_error", learner_id=str(learner_id), error=repr(exc))
                continue
            applied += outcome.status == "applied"
        return applied

    async def run_forever(self, stop: asyncio.Event) -> None:
        idle = self.settings.consumer_poll_ms / 1000
        while not stop.is_set():
            if await self.run_once():
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=idle)
