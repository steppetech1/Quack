"""The cheat sheet offer of a hub is a state read from the journal (ТЗ Ф4 WP23.5; Т-§17.7,
Т-§14.7; D-C-144).

The teacher (slot 6) and «Сегодня» read `cheatsheet_offer` and keep no rule of their own. No
job is created for the offer: an offer is appropriate while

- the hub has a deadline (the latest `hub.deadline_set` of the hub, `None` removes it) that has
  not passed and is not farther than `cheatsheet_offer_days`, and
- there is neither a consent — `agent.requested {kind: artifact, params: {artifact: cheatsheet,
  hub_id}}` (D-C-143) — nor a saved cheat sheet — `artifact.updated {kind: cheatsheet, hub_id}`.

Every statement is over the learner's own journal, so another learner's hub is never offered.
"""

from __future__ import annotations

import time
from uuid import UUID

from quack.config import Settings
from quack.db import Db

MS_PER_DAY = 86_400_000

_LATEST_DEADLINE = (
    "SELECT (payload ->> 'deadline_at_ms')::bigint AS deadline_at_ms FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = 'hub.deadline_set' "
    "AND payload ->> 'hub_id' = %(hub_id)s ORDER BY seq DESC LIMIT 1"
)
_AGREED_OR_WRITTEN = (
    "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s "
    "AND ((type = 'agent.requested' AND payload ->> 'kind' = 'artifact' "
    "AND payload -> 'params' ->> 'artifact' = 'cheatsheet' "
    "AND payload -> 'params' ->> 'hub_id' = %(hub_id)s) "
    "OR (type = 'artifact.updated' AND payload ->> 'kind' = 'cheatsheet' "
    "AND payload ->> 'hub_id' = %(hub_id)s)) LIMIT 1"
)


async def cheatsheet_offer(
    db: Db, learner_id: UUID, hub_id: UUID, settings: Settings, *, now_ms: int | None = None
) -> bool:
    """Whether the hub's cheat sheet should be offered to the learner now. `now_ms` is the
    server clock by default."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    params = {"learner_id": learner_id, "hub_id": str(hub_id)}
    deadline = await db.fetch_one(_LATEST_DEADLINE, params)
    if deadline is None or deadline["deadline_at_ms"] is None:
        return False
    left_ms = int(deadline["deadline_at_ms"]) - now
    if not 0 <= left_ms <= settings.cheatsheet_offer_days * MS_PER_DAY:
        return False
    return await db.fetch_one(_AGREED_OR_WRITTEN, params) is None
