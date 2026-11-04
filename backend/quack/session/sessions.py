"""The session lifecycle on `Db` (ТЗ Ф4 WP13.10; Т-§2.2, П-4.10; D-B-155).

A session is `session.started` in the log (law 1) and its row in `content.sessions` (§6.1),
written in one transaction. Start: a domain of the learner (`Т-§22.9`), an intent, a topic or
a hub. Starting another session does not end the open one: a session stays open for a return
(`П-4.10`). End: `session.ended {reason: learner}` (`POST …/end`) or `{reason: inactive}`
(the inactivity sweep, `session_inactivity_timeout_min`). An action in a session the sweep
ended writes `session.started` of the same session again and the session goes on; a session
the learner ended does not come back (WP13.10). The HTTP routes move here after D-C-119.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, model_validator

from quack.config import Settings
from quack.db.db import Db
from quack.events.errors import IdempotencyConflictError
from quack.events.event_types import SessionEnded, SessionStarted
from quack.events.log import NewEvent, append_events
from quack.practice.today_plan import prepared_session_for

# Т-§12.7 buttons and the free request («ведёт так же, как при свободном запросе»), D-B-155.
type SessionIntent = Literal["learn", "practice", "review", "free"]
type EndReason = Literal["learner", "inactive"]

_COLUMNS = (
    "s.session_id, s.domain_id, s.intent, s.topic_id, s.hub_id, s.status, s.started_at, "
    "s.ended_at, s.last_activity_at, (SELECT min(e.seq) FROM core.events e "
    "WHERE e.learner_id = s.learner_id AND e.session_id = s.session_id "
    "AND e.type = 'session.started') AS start_seq"
)
_SESSION = (
    f"SELECT {_COLUMNS} FROM content.sessions s "
    "WHERE s.learner_id = %(learner_id)s AND s.session_id = %(session_id)s"
)
_SESSION_FOR_UPDATE = _SESSION + " FOR UPDATE OF s"
_SESSIONS = (
    f"SELECT {_COLUMNS} FROM content.sessions s WHERE s.learner_id = %(learner_id)s "
    "AND (%(domain_id)s::uuid IS NULL OR s.domain_id = %(domain_id)s) "
    "ORDER BY s.started_at DESC, start_seq DESC LIMIT %(limit)s"
)
_ACTIVE = (
    f"SELECT {_COLUMNS} FROM content.sessions s WHERE s.learner_id = %(learner_id)s "
    "AND s.status = 'active' ORDER BY s.started_at DESC, start_seq DESC LIMIT 1"
)
_FEED = (
    "SELECT seq, type, actor, occurred_at, payload FROM core.events "
    "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s ORDER BY seq"
)
_DOMAIN_OF_LEARNER = (
    "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'domain.created' AND payload ->> 'domain_id' = %(domain_id)s LIMIT 1"
)
_EVENT_BY_KEY = (
    "SELECT seq, payload FROM core.events "
    "WHERE learner_id = %(learner_id)s AND idempotency_key = %(key)s"
)
_LAST_OF_TYPE = (
    "SELECT seq, payload FROM core.events WHERE learner_id = %(learner_id)s "
    "AND session_id = %(session_id)s AND type = %(type)s ORDER BY seq DESC LIMIT 1"
)
_INSERT = (
    "INSERT INTO content.sessions (session_id, learner_id, domain_id, intent, topic_id, hub_id, "
    "status) VALUES (%(session_id)s, %(learner_id)s, %(domain_id)s, %(intent)s, %(topic_id)s, "
    "%(hub_id)s, 'active')"
)
_HEARTBEAT = (
    "UPDATE content.sessions SET last_activity_at = now() WHERE learner_id = %(learner_id)s "
    "AND session_id = %(session_id)s AND status = 'active' RETURNING session_id"
)
_ENDED = (
    "UPDATE content.sessions SET status = 'ended', ended_at = now() "
    "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s"
)
_REOPENED = (
    "UPDATE content.sessions SET status = 'active', ended_at = NULL, last_activity_at = now() "
    "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s"
)


class SessionConflictError(Exception):
    """The start key was used with other parameters: `409 IDEMPOTENCY_CONFLICT`."""


class StartSession(BaseModel):
    """`POST /v1/sessions` (WP13.10): the domain, the intent, a topic or a hub."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain_id: UUID
    intent: SessionIntent
    topic_id: UUID | None = None
    hub_id: UUID | None = None
    today_item: UUID | None = None  # the skill of the pressed item of «Сегодня» (WP16.8)
    idempotency_key: UUID

    @model_validator(mode="after")
    def _one_goal(self) -> StartSession:
        if self.topic_id is not None and self.hub_id is not None:
            raise ValueError("a session goes to a topic or to a hub, not both")
        return self


class SessionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: UUID
    domain_id: UUID
    intent: str | None
    topic_id: UUID | None
    hub_id: UUID | None
    status: Literal["active", "ended"]
    started_at: datetime
    ended_at: datetime | None
    last_activity_at: datetime
    start_seq: int | None


class FeedEvent(BaseModel):
    """An event of the session feed (`GET /v1/sessions/{id}`, D-B-210)."""

    model_config = ConfigDict(frozen=True)

    seq: int
    type: str
    actor: str
    occurred_at: datetime
    payload: dict[str, object]


class _Found(BaseModel):
    found: int


class _Stored(BaseModel):
    seq: int
    payload: dict[str, object]


async def start_session(db: Db, *, learner_id: UUID, body: StartSession) -> SessionView:
    """Write `session.started` and the row; a repeat with the same key and parameters returns
    the same session, with other parameters — `SessionConflictError`. A concurrent start with
    the same key that committed first is read again after the conflict (D-B-155 п.ж)."""
    params = {"learner_id": learner_id, "domain_id": str(body.domain_id)}
    if await db.fetch_one(_DOMAIN_OF_LEARNER, params, row=_Found) is None:
        raise LookupError(f"domain {body.domain_id} is not the learner's")
    key = f"session.start:{body.idempotency_key}"
    async with db.transaction():
        found = await _started_by_key(db, learner_id, key, body)
        if found is not None:
            return found
        session_id = (
            await prepared_session_for(
                db,
                learner_id=learner_id,
                domain_id=body.domain_id,
                topic_id=body.topic_id,
                skill_id=body.today_item,
            )
            or uuid4()
        )
        payload = SessionStarted(
            session_id=session_id,
            domain_id=body.domain_id,
            intent=body.intent,
            topic_id=body.topic_id,
            hub_id=body.hub_id,
        )
        try:
            await append_events(
                db,
                learner_id,
                [_event("session.started", payload, key, "learner", session_id, body.domain_id)],
            )
        except IdempotencyConflictError:
            # A concurrent start with the same key committed after our read (law 5).
            again = await _started_by_key(db, learner_id, key, body)
            if again is None:
                raise
            return again
        await db.execute(
            _INSERT,
            {
                "session_id": session_id,
                "learner_id": learner_id,
                "domain_id": body.domain_id,
                "intent": body.intent,
                "topic_id": body.topic_id,
                "hub_id": body.hub_id,
            },
        )
        return await get_session(db, learner_id=learner_id, session_id=session_id)


async def get_session(db: Db, *, learner_id: UUID, session_id: UUID) -> SessionView:
    row = await db.fetch_one(
        _SESSION, {"learner_id": learner_id, "session_id": session_id}, row=SessionView
    )
    if row is None:
        raise LookupError(f"session {session_id} not found")
    return row


async def list_sessions(
    db: Db, *, learner_id: UUID, domain_id: UUID | None = None, limit: int | None = None
) -> list[SessionView]:
    """`GET /v1/sessions`: the history, the newest first; of one domain, up to `limit`."""
    params = {"learner_id": learner_id, "domain_id": domain_id, "limit": limit}
    return await db.fetch_all(_SESSIONS, params, row=SessionView)


async def active_session(db: Db, *, learner_id: UUID) -> SessionView | None:
    """`GET /v1/sessions/active`: the newest open session of the learner, if any."""
    return await db.fetch_one(_ACTIVE, {"learner_id": learner_id}, row=SessionView)


async def session_feed(db: Db, *, learner_id: UUID, session_id: UUID) -> list[FeedEvent]:
    """`GET /v1/sessions/{id}`: the events of the session in `seq` order (WP13.10)."""
    params = {"learner_id": learner_id, "session_id": session_id}
    return await db.fetch_all(_FEED, params, row=FeedEvent)


async def heartbeat(db: Db, *, learner_id: UUID, session_id: UUID) -> SessionView:
    """`POST …/heartbeat`: the activity of an open session; an ended one — `LookupError`."""
    keys = {"learner_id": learner_id, "session_id": session_id}
    if await db.fetch_one(_HEARTBEAT, keys) is None:
        raise LookupError(f"no open session {session_id}")
    return await get_session(db, learner_id=learner_id, session_id=session_id)


async def end_session(db: Db, *, learner_id: UUID, session_id: UUID) -> SessionView:
    """`POST …/end`: `session.ended {reason: learner}`; an ended session stays as it is."""
    async with db.transaction():
        row = await _locked(db, learner_id, session_id)
        if row.status == "active":
            await _end(db, learner_id, row, "learner")
    return await get_session(db, learner_id=learner_id, session_id=session_id)


async def end_if_inactive(
    db: Db, *, learner_id: UUID, session_id: UUID, settings: Settings, now: datetime
) -> bool:
    """The inactivity sweep for one session: no activity for `session_inactivity_timeout_min`
    → `session.ended {reason: inactive}`. True if it ended the session."""
    limit = timedelta(minutes=settings.session_inactivity_timeout_min)
    async with db.transaction():
        row = await _locked(db, learner_id, session_id)
        if row.status != "active" or now - row.last_activity_at < limit:
            return False
        await _end(db, learner_id, row, "inactive")
    return True


async def resume_if_swept(db: Db, *, learner_id: UUID, session_id: UUID) -> bool:
    """An action in a session the sweep ended: `session.started` of the same session again and
    the row is open (WP13.10). A session the learner ended does not come back. True if the
    session came back."""
    async with db.transaction():
        row = await _locked(db, learner_id, session_id)
        if row.status != "ended":
            return False
        ended = await db.fetch_one(
            _LAST_OF_TYPE,
            {"learner_id": learner_id, "session_id": session_id, "type": "session.ended"},
            row=_Stored,
        )
        if ended is None or SessionEnded.model_validate(ended.payload).reason != "inactive":
            return False
        payload = SessionStarted(
            session_id=session_id,
            domain_id=row.domain_id,
            intent=row.intent or "free",
            topic_id=row.topic_id,
            hub_id=row.hub_id,
            resumed=True,
        )
        key = f"session.resume:{ended.seq}"
        await append_events(
            db,
            learner_id,
            [_event("session.started", payload, key, "learner", session_id, row.domain_id)],
        )
        await db.execute(_REOPENED, {"learner_id": learner_id, "session_id": session_id})
    return True


# --- internals ---


async def _started_by_key(
    db: Db, learner_id: UUID, key: str, body: StartSession
) -> SessionView | None:
    stored = await db.fetch_one(_EVENT_BY_KEY, {"learner_id": learner_id, "key": key}, row=_Stored)
    if stored is None:
        return None
    was = SessionStarted.model_validate(stored.payload)
    asked = (body.domain_id, body.intent, body.topic_id, body.hub_id)
    if (was.domain_id, was.intent, was.topic_id, was.hub_id) != asked:
        raise SessionConflictError(f"{key} was used with other parameters")
    return await get_session(db, learner_id=learner_id, session_id=was.session_id)


async def _locked(db: Db, learner_id: UUID, session_id: UUID) -> SessionView:
    row = await db.fetch_one(
        _SESSION_FOR_UPDATE, {"learner_id": learner_id, "session_id": session_id}, row=SessionView
    )
    if row is None:
        raise LookupError(f"session {session_id} not found")
    return row


async def _end(db: Db, learner_id: UUID, row: SessionView, reason: EndReason) -> None:
    started = await db.fetch_one(
        _LAST_OF_TYPE,
        {"learner_id": learner_id, "session_id": row.session_id, "type": "session.started"},
        row=_Stored,
    )
    opened = started.seq if started is not None else 0
    payload = SessionEnded(session_id=row.session_id, reason=reason)
    key = f"session.end:{row.session_id}:{opened}"
    actor = "learner" if reason == "learner" else "system"
    await append_events(
        db,
        learner_id,
        [_event("session.ended", payload, key, actor, row.session_id, row.domain_id)],
    )
    await db.execute(_ENDED, {"learner_id": learner_id, "session_id": row.session_id})


def _event(
    type_: str,
    payload: SessionStarted | SessionEnded,
    key: str,
    actor: str,
    session_id: UUID,
    domain_id: UUID,
) -> NewEvent:
    return NewEvent(
        type=type_,
        payload=payload,
        actor=actor,
        idempotency_key=key,
        session_id=session_id,
        domain_id=domain_id,
    )


__all__ = [
    "EndReason",
    "FeedEvent",
    "SessionConflictError",
    "SessionIntent",
    "SessionView",
    "StartSession",
    "active_session",
    "end_if_inactive",
    "end_session",
    "get_session",
    "heartbeat",
    "list_sessions",
    "resume_if_swept",
    "session_feed",
    "start_session",
]
