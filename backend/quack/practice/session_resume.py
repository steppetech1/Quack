"""An action brings a closed session back (ТЗ Ф4 WP13.10; Т-§2.2, П-4.10; D-B-155).

Only the inactivity sweep closes a session. A session never ends for good: the first action
(a message, an answer, a voice segment) or a heartbeat in a closed session writes
`session.started {resumed: true}` of the same session again and opens the row; any accepted
action also refreshes `last_activity_at`, so the sweep does not close a session in the middle of
a talk. The code lives here because the session routes and the voice route are two product
packages and a product package imports only `practice` (§4.1).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from quack.db.db import Db
from quack.events.event_types import SessionEnded, SessionStarted
from quack.events.log import NewEvent, append_events

_LOCKED = (
    "SELECT domain_id, intent, topic_id, hub_id, status FROM content.sessions "
    "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s AND NOT EXISTS ("
    "SELECT 1 FROM core.events x WHERE x.learner_id = content.sessions.learner_id "
    "AND x.session_id = content.sessions.session_id AND x.type = 'session.deleted') FOR UPDATE"
)
_LAST_ENDED = (
    "SELECT seq, payload FROM core.events WHERE learner_id = %(learner_id)s "
    "AND session_id = %(session_id)s AND type = 'session.ended' ORDER BY seq DESC LIMIT 1"
)
_REOPENED = (
    "UPDATE content.sessions SET status = 'active', ended_at = NULL, last_activity_at = now() "
    "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s"
)
_TOUCH = (
    "UPDATE content.sessions SET last_activity_at = now() WHERE learner_id = %(learner_id)s "
    "AND session_id = %(session_id)s AND status = 'active' RETURNING session_id"
)


class _Row(BaseModel):
    domain_id: UUID
    intent: str | None
    topic_id: UUID | None
    hub_id: UUID | None
    status: str


class _Ended(BaseModel):
    seq: int
    payload: dict[str, object]


async def resume_if_ended(db: Db, *, learner_id: UUID, session_id: UUID) -> bool:
    """Open a closed session again: `session.started {resumed}` under the key
    `session.resume:{seq of the closing event}` (law 5), the row is open. True if the session
    came back; False for an open one, one without a row (a session of the log only) or one of
    another learner."""
    async with db.transaction():
        row = await db.fetch_one(
            _LOCKED, {"learner_id": learner_id, "session_id": session_id}, row=_Row
        )
        if row is None or row.status != "ended":
            return False
        ended = await db.fetch_one(
            _LAST_ENDED, {"learner_id": learner_id, "session_id": session_id}, row=_Ended
        )
        if ended is None:
            return False
        SessionEnded.model_validate(ended.payload)  # the reason is any string (old logs: learner)
        payload = SessionStarted(
            session_id=session_id,
            domain_id=row.domain_id,
            intent=row.intent or "free",
            topic_id=row.topic_id,
            hub_id=row.hub_id,
            resumed=True,
        )
        event = NewEvent(
            type="session.started",
            payload=payload,
            actor="learner",
            idempotency_key=f"session.resume:{ended.seq}",
            session_id=session_id,
            domain_id=row.domain_id,
        )
        await append_events(db, learner_id, [event])
        await db.execute(_REOPENED, {"learner_id": learner_id, "session_id": session_id})
    return True


async def touch_session(db: Db, *, learner_id: UUID, session_id: UUID) -> bool:
    """`last_activity_at = now()` of an open session (a heartbeat or any accepted action);
    False if the session is not open or not the learner's."""
    keys = {"learner_id": learner_id, "session_id": session_id}
    return await db.fetch_one(_TOUCH, keys) is not None


__all__ = ["resume_if_ended", "touch_session"]
