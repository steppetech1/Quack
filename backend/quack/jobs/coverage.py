"""Coverage of episodes by extractors (ТЗ Ф4 WP7.3; `Т-§2.8`, `Т-§7.10`).

`ops.coverage(learner_id, episode_seq, extractor, status, observation_seq)`: a row says the
episode was processed by this extractor version — `extracted` with the observation, or
`nothing_to_extract` written by the dispatcher itself (WP7.2). No row — the episode is not
processed and the dispatcher puts the job. The row is written in the transaction of the
observation event: called inside `db.transaction()` it joins it (law 7).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from quack.db import Db

type CoverageStatus = Literal["extracted", "nothing_to_extract"]

_UPSERT_COVERAGE = (
    "INSERT INTO ops.coverage (learner_id, episode_seq, extractor, status, observation_seq) "
    "VALUES (%(learner_id)s, %(episode_seq)s, %(extractor)s, %(status)s, %(observation_seq)s) "
    "ON CONFLICT (learner_id, episode_seq, extractor) DO UPDATE "
    "SET status = EXCLUDED.status, observation_seq = EXCLUDED.observation_seq"
)


async def mark_covered(
    db: Db,
    learner_id: UUID,
    *,
    episode_seq: int,
    extractor: str,
    status: CoverageStatus,
    observation_seq: int | None,
) -> None:
    """Record that `extractor` covered the episode. `extracted` needs the `seq` of its
    `observation.extracted`; `nothing_to_extract` has none. A repeat replaces the row (law 5):
    a re-extraction (`episode.reassigned`, `skill.split`) points it at the new observation."""
    if (status == "extracted") != (observation_seq is not None):
        raise ValueError("`extracted` needs the seq of its observation, `nothing_to_extract` none")
    await db.execute(
        _UPSERT_COVERAGE,
        {
            "learner_id": learner_id,
            "episode_seq": episode_seq,
            "extractor": extractor,
            "status": status,
            "observation_seq": observation_seq,
        },
    )
