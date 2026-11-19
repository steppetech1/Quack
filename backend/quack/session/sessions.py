"""The session lifecycle on `Db` (ТЗ Ф4 WP13.10; Т-§2.2, П-4.10; D-B-155).

A session is `session.started` in the log (law 1) and its row in `content.sessions` (§6.1),
written in one transaction. Start: a domain of the learner (`Т-§22.9`), an intent, a topic or
a hub. Starting another session does not end the open one: a session stays open for a return
(`П-4.10`). End: only `session.ended {reason: inactive}` (the inactivity sweep,
`session_inactivity_timeout_min`); the learner has no way to end a session. An action, a voice
segment or a heartbeat in a closed session writes `session.started {resumed}` of the same
session again and the session goes on, and every accepted action refreshes `last_activity_at`
(WP13.10, `quack.practice.session_resume`). Old logs may hold `session.ended` with the retired
reason `learner`; it stays readable. The HTTP routes move here after D-C-119.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, model_validator

from quack.config import Settings
from quack.db.db import Db
from quack.events.errors import IdempotencyConflictError
from quack.events.event_types import SessionEnded, SessionStarted
from quack.events.log import NewEvent, append_events
from quack.events.values import Outcome
from quack.practice.session_resume import resume_if_ended, touch_session
from quack.practice.today_plan import prepared_session_for

# Т-§12.7 buttons and the free request («ведёт так же, как при свободном запросе»), D-B-155.
type SessionIntent = Literal["learn", "practice", "review", "free"]
type EndReason = Literal["inactive"]

# The name of the session: the stored one (`session.titled`), else the first words of the learner
# (a typed message or a transcribed voice one), cut by the code to `title_chars` at a word
# boundary (`provisional_title`); one more character is read to see that it was cut.
_FIRST_WORDS = (
    "(SELECT left(regexp_replace(btrim(f.payload ->> 'text'), '[[:space:]]+', ' ', 'g'), "
    "%(title_chars)s + 1) FROM core.events f WHERE f.learner_id = s.learner_id "
    "AND f.session_id = s.session_id AND f.type IN ('message.sent', 'voice.transcribed') "
    "AND btrim(f.payload ->> 'text') <> '' ORDER BY f.seq LIMIT 1)"
)
_COLUMNS = (
    "s.session_id, s.domain_id, s.intent, s.topic_id, s.hub_id, s.status, s.started_at, "
    "s.ended_at, s.last_activity_at, (SELECT min(e.seq) FROM core.events e "
    "WHERE e.learner_id = s.learner_id AND e.session_id = s.session_id "
    "AND e.type = 'session.started') AS start_seq, s.title AS stored_title, "
    f"CASE WHEN s.title IS NULL THEN {_FIRST_WORDS} END AS first_words"
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
    "SELECT seq, type, actor, occurred_at, causation_seq, payload FROM core.events "
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
_ENDED = (
    "UPDATE content.sessions SET status = 'ended', ended_at = now() "
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
    """`title` — the name of the session: the one the job `session.title` wrote, else the first
    words of the learner (a provisional name, the old sessions too), else `None` (nobody has said
    anything yet: the client names the session by its intent and topic)."""

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
    title: str | None


class _SessionRow(BaseModel):
    """A row of the session query: the view plus what the name is made of."""

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
    stored_title: str | None
    first_words: str | None

    def view(self, title_chars: int) -> SessionView:
        title = self.stored_title or provisional_title(self.first_words, title_chars)
        return SessionView(**self.model_dump(exclude={"stored_title", "first_words"}), title=title)


def provisional_title(text: str | None, max_chars: int) -> str | None:
    """The first words of the learner as a name: one line, cut at a word boundary to `max_chars`
    (with `…` when something was left out); `None` for an empty text."""
    one_line = " ".join((text or "").split())
    if not one_line:
        return None
    if len(one_line) <= max_chars:
        return one_line
    head = one_line[: max_chars + 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else head[:max_chars]
    return cut.rstrip(" ,;:-") + "…"


class FeedEvent(BaseModel):
    """An event of the session feed (`GET /v1/sessions/{id}`, D-B-210).

    `causation_seq` is the causal parent in `core.events`; `turn_seq`, `element_id` and
    `outcome` link element responses, submitted answers and verdicts back to their exact
    turn and element without guessing by order."""

    model_config = ConfigDict(frozen=True)

    seq: int
    type: str
    actor: str
    occurred_at: datetime
    causation_seq: int | None = None
    turn_seq: int | None = None
    element_id: str | None = None
    outcome: Outcome | None = None
    payload: dict[str, object]


class _FeedRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    type: str
    actor: str
    occurred_at: datetime
    causation_seq: int | None = None
    payload: dict[str, object]


_OUTCOMES: frozenset[str] = frozenset({"correct", "incorrect", "partial", "unclear"})


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_outcome(value: object) -> Outcome | None:
    if isinstance(value, str) and value in _OUTCOMES:
        return value  # type: ignore[return-value]
    return None


def _enrich_feed(rows: Sequence[_FeedRow]) -> list[FeedEvent]:
    """Attach `turn_seq`, `element_id` and `outcome` to session feed events so reloads match
    responses and verdicts to their exact turn and element."""
    task_targets: dict[str, tuple[int, str]] = {}
    response_targets: dict[int, tuple[int | None, str | None]] = {}
    answer_targets: dict[int, tuple[int | None, str | None, int | None]] = {}
    verdict_by_response: dict[int, Outcome] = {}
    verdict_by_element: dict[tuple[int, str], Outcome] = {}
    row_meta: list[tuple[int | None, str | None, Outcome | None]] = []

    for row in rows:
        pl = row.payload
        if row.type == "turn.committed":
            markup = pl.get("markup")
            tasks = markup.get("tasks") if isinstance(markup, dict) else None
            if isinstance(tasks, list):
                for task in tasks:
                    if not isinstance(task, dict):
                        continue
                    tid = _as_str(task.get("task_id"))
                    el_id = _as_str(task.get("element_id"))
                    if tid is not None and el_id is not None:
                        task_targets[tid] = (row.seq, el_id)
            row_meta.append((None, None, None))
        elif row.type == "element.responded":
            t_seq = _as_int(pl.get("turn_seq"))
            el_id = _as_str(pl.get("element_id"))
            response_targets[row.seq] = (t_seq, el_id)
            row_meta.append((t_seq, el_id, None))
        elif row.type == "answer.submitted":
            el_id = _as_str(pl.get("element_id"))
            t_seq: int | None = None
            resp_seq: int | None = None
            if row.causation_seq is not None and row.causation_seq in response_targets:
                resp_seq = row.causation_seq
                t_seq, resp_el_id = response_targets[resp_seq]
                el_id = el_id or resp_el_id
            elif (tid := _as_str(pl.get("task_id"))) is not None and tid in task_targets:
                t_seq, task_el_id = task_targets[tid]
                el_id = el_id or task_el_id
            answer_targets[row.seq] = (t_seq, el_id, resp_seq)
            row_meta.append((t_seq, el_id, None))
        elif row.type == "verdict.recorded":
            answer_seq = _as_int(pl.get("answer_seq"))
            if answer_seq is None:
                answer_seq = row.causation_seq
            outcome = _as_outcome(pl.get("outcome"))
            t_seq = None
            el_id = None
            resp_seq = None
            if answer_seq is not None and answer_seq in answer_targets:
                t_seq, el_id, resp_seq = answer_targets[answer_seq]
            if outcome is not None:
                if resp_seq is not None:
                    verdict_by_response[resp_seq] = outcome
                if t_seq is not None and el_id is not None:
                    verdict_by_element[(t_seq, el_id)] = outcome
            row_meta.append((t_seq, el_id, outcome))
        elif row.type == "item.flagged_unclear":
            row_meta.append((_as_int(pl.get("turn_seq")), None, None))
        else:
            row_meta.append((None, None, None))

    out: list[FeedEvent] = []
    for row, (t_seq, el_id, outcome) in zip(rows, row_meta, strict=True):
        if row.type == "element.responded":
            outcome = verdict_by_response.get(row.seq)
            if outcome is None and t_seq is not None and el_id is not None:
                outcome = verdict_by_element.get((t_seq, el_id))
        out.append(
            FeedEvent(
                seq=row.seq,
                type=row.type,
                actor=row.actor,
                occurred_at=row.occurred_at,
                causation_seq=row.causation_seq,
                turn_seq=t_seq,
                element_id=el_id,
                outcome=outcome,
                payload=row.payload,
            )
        )
    return out


class _Found(BaseModel):
    found: int


class _Stored(BaseModel):
    seq: int
    payload: dict[str, object]


async def start_session(
    db: Db, *, learner_id: UUID, body: StartSession, title_chars: int | None = None
) -> SessionView:
    """Write `session.started` and the row; a repeat with the same key and parameters returns
    the same session, with other parameters — `SessionConflictError`. A concurrent start with
    the same key that committed first is read again after the conflict (D-B-155 п.ж)."""
    params = {"learner_id": learner_id, "domain_id": str(body.domain_id)}
    if await db.fetch_one(_DOMAIN_OF_LEARNER, params, row=_Found) is None:
        raise LookupError(f"domain {body.domain_id} is not the learner's")
    key = f"session.start:{body.idempotency_key}"
    async with db.transaction():
        found = await _started_by_key(db, learner_id, key, body, title_chars)
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
            again = await _started_by_key(db, learner_id, key, body, title_chars)
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
        return await get_session(
            db, learner_id=learner_id, session_id=session_id, title_chars=title_chars
        )


def _title_chars(title_chars: int | None) -> int:
    """`session_title_provisional_chars` unless the caller (it has the settings) gives it."""
    if title_chars is not None:
        return title_chars
    return int(Settings.model_fields["session_title_provisional_chars"].default)


async def get_session(
    db: Db, *, learner_id: UUID, session_id: UUID, title_chars: int | None = None
) -> SessionView:
    chars = _title_chars(title_chars)
    row = await db.fetch_one(
        _SESSION,
        {"learner_id": learner_id, "session_id": session_id, "title_chars": chars},
        row=_SessionRow,
    )
    if row is None:
        raise LookupError(f"session {session_id} not found")
    return row.view(chars)


async def list_sessions(
    db: Db,
    *,
    learner_id: UUID,
    domain_id: UUID | None = None,
    limit: int | None = None,
    title_chars: int | None = None,
) -> list[SessionView]:
    """`GET /v1/sessions`: the history, the newest first; of one domain, up to `limit`."""
    chars = _title_chars(title_chars)
    params = {
        "learner_id": learner_id,
        "domain_id": domain_id,
        "limit": limit,
        "title_chars": chars,
    }
    return [row.view(chars) for row in await db.fetch_all(_SESSIONS, params, row=_SessionRow)]


async def active_session(
    db: Db, *, learner_id: UUID, title_chars: int | None = None
) -> SessionView | None:
    """`GET /v1/sessions/active`: the newest open session of the learner, if any."""
    chars = _title_chars(title_chars)
    row = await db.fetch_one(
        _ACTIVE, {"learner_id": learner_id, "title_chars": chars}, row=_SessionRow
    )
    return None if row is None else row.view(chars)


async def session_feed(db: Db, *, learner_id: UUID, session_id: UUID) -> list[FeedEvent]:
    """`GET /v1/sessions/{id}`: the events of the session in `seq` order (WP13.10)."""
    params = {"learner_id": learner_id, "session_id": session_id}
    rows = await db.fetch_all(_FEED, params, row=_FeedRow)
    return _enrich_feed(rows)


async def heartbeat(
    db: Db, *, learner_id: UUID, session_id: UUID, title_chars: int | None = None
) -> SessionView:
    """`POST …/heartbeat`: the activity of the session; a closed one comes back first
    (`resume_if_ended`); a session of another learner or none — `LookupError`."""
    view = await get_session(db, learner_id=learner_id, session_id=session_id)
    if view.status == "ended":
        await resume_if_ended(db, learner_id=learner_id, session_id=session_id)
    if not await touch_session(db, learner_id=learner_id, session_id=session_id):
        raise LookupError(f"no open session {session_id}")
    return await get_session(
        db, learner_id=learner_id, session_id=session_id, title_chars=title_chars
    )


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


# --- internals ---


async def _started_by_key(
    db: Db, learner_id: UUID, key: str, body: StartSession, title_chars: int | None
) -> SessionView | None:
    stored = await db.fetch_one(_EVENT_BY_KEY, {"learner_id": learner_id, "key": key}, row=_Stored)
    if stored is None:
        return None
    was = SessionStarted.model_validate(stored.payload)
    asked = (body.domain_id, body.intent, body.topic_id, body.hub_id)
    if (was.domain_id, was.intent, was.topic_id, was.hub_id) != asked:
        raise SessionConflictError(f"{key} was used with other parameters")
    return await get_session(
        db, learner_id=learner_id, session_id=was.session_id, title_chars=title_chars
    )


async def _locked(db: Db, learner_id: UUID, session_id: UUID) -> SessionView:
    chars = _title_chars(None)
    row = await db.fetch_one(
        _SESSION_FOR_UPDATE,
        {"learner_id": learner_id, "session_id": session_id, "title_chars": chars},
        row=_SessionRow,
    )
    if row is None:
        raise LookupError(f"session {session_id} not found")
    return row.view(chars)


async def _end(db: Db, learner_id: UUID, row: SessionView, reason: EndReason) -> None:
    started = await db.fetch_one(
        _LAST_OF_TYPE,
        {"learner_id": learner_id, "session_id": row.session_id, "type": "session.started"},
        row=_Stored,
    )
    opened = started.seq if started is not None else 0
    payload = SessionEnded(session_id=row.session_id, reason=reason)
    key = f"session.end:{row.session_id}:{opened}"
    await append_events(
        db,
        learner_id,
        [_event("session.ended", payload, key, "system", row.session_id, row.domain_id)],
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
    "get_session",
    "heartbeat",
    "list_sessions",
    "provisional_title",
    "resume_if_ended",
    "session_feed",
    "start_session",
    "touch_session",
]
