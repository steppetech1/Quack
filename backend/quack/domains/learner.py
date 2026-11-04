"""The learner's explicit preferences: `preference.set` (ТЗ Ф4 §6.3–6.4 «Ученик»; Т-§9.5).

The event is the whole record: the projector folds the latest value of a key into `Profile.pref*`
(WP5.3). The account itself is deleted by `quack.deletion.account` (WP25).
"""

from __future__ import annotations

from uuid import UUID

from redis.asyncio import Redis

from quack.db import Db
from quack.domains.schemas import SetPreferenceRequest, SetPreferenceResponse
from quack.events.log import NewEvent, append_events


async def set_preference(
    db: Db, redis: Redis | None, learner_id: UUID, req: SetPreferenceRequest
) -> SetPreferenceResponse:
    """Append `preference.set {key, value}`; a retry with the same key returns the same event
    (law 5), the same key with another value is `409`."""
    event = NewEvent(
        type="preference.set",
        payload={"key": req.key, "value": req.value},
        actor="learner",
        idempotency_key=f"preference:{req.idempotency_key}",
    )
    [stored] = await append_events(db, learner_id, [event], redis=redis)
    return SetPreferenceResponse(key=req.key, value=req.value, seq=stored.seq)
