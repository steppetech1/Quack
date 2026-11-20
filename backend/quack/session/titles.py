"""The name of a session: the job `session.title` (owner decision 2026-11-19).

- The dispatcher puts one job per session after its first committed turn (`rules_p4`, key
  `session.title:{learner}:{session}`); the kind is P3 and not visible on the plaque.
- The job reads the first learner messages (typed or transcribed) and the first reply of the
  teacher from the log, asks `session_titler@1` for two to six words and writes
  `session.titled` under the key `session.title:{session_id}` together with
  `content.sessions.title` in one transaction (law 1: the log first; the column is its reading).
- A session that already has a title is left alone, without a model call: a name is given once
  and the learner does not rename it here. A failed job commits nothing (law 7) and the hot path
  never waits for it (law 9): until the name is written the panel shows the first words of the
  learner (`sessions.provisional_title`).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from quack.config import Settings
from quack.db.db import Db
from quack.events.errors import IdempotencyConflictError
from quack.events.event_types import SessionTitled
from quack.events.log import NewEvent, append_events
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.llm.gateway import LLMGateway
from quack.practice.role_outputs import SessionTitleOutput

TITLER_ROLE = "session_titler"
NONE_LINE = "(none)"
_QUOTES = "\"'`«»“”„‘’"

_ROW = (
    "SELECT title, domain_id FROM content.sessions "
    "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s"
)
_ROW_FOR_UPDATE = _ROW + " FOR UPDATE"
_MESSAGES = (
    "SELECT payload ->> 'text' AS text FROM core.events WHERE learner_id = %(learner_id)s "
    "AND session_id = %(session_id)s AND type IN ('message.sent', 'voice.transcribed') "
    "AND btrim(payload ->> 'text') <> '' ORDER BY seq LIMIT %(limit)s"
)
_FIRST_REPLY = (
    "SELECT payload ->> 'text' AS text FROM core.events WHERE learner_id = %(learner_id)s "
    "AND session_id = %(session_id)s AND type = 'turn.committed' "
    "AND btrim(payload ->> 'text') <> '' ORDER BY seq LIMIT 1"
)
_TITLED = (
    "SELECT payload ->> 'title' AS title FROM core.events WHERE learner_id = %(learner_id)s "
    "AND idempotency_key = %(key)s"
)
_SET = (
    "UPDATE content.sessions SET title = %(title)s WHERE learner_id = %(learner_id)s "
    "AND session_id = %(session_id)s AND title IS NULL"
)


class _Row(BaseModel):
    title: str | None
    domain_id: UUID


class _Text(BaseModel):
    text: str


class _Title(BaseModel):
    title: str


def title_key(session_id: UUID) -> str:
    return f"session.title:{session_id}"


def clean_title(raw: str, max_chars: int) -> str:
    """One line without quotes, brackets of a label or the final full stop, cut to `max_chars`
    at a word boundary; an empty result means the model gave nothing usable."""
    lines = [line for line in raw.splitlines() if line.strip()]
    text = " ".join(lines[0].split()) if lines else ""
    text = text.strip(_QUOTES + " ").rstrip(".。!").strip(_QUOTES + " ")
    if len(text) > max_chars:
        head = text[: max_chars + 1]
        text = (head.rsplit(" ", 1)[0] if " " in head else head[:max_chars]).rstrip(" ,;:-")
    return text


def _cut(text: str, max_chars: int) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= max_chars else one_line[:max_chars].rstrip() + "…"


async def _inputs(
    db: Db, settings: Settings, learner_id: UUID, session_id: UUID
) -> dict[str, str] | None:
    params = {"learner_id": learner_id, "session_id": session_id}
    messages = await db.fetch_all(
        _MESSAGES, {**params, "limit": settings.session_title_messages}, row=_Text
    )
    reply = await db.fetch_one(_FIRST_REPLY, params, row=_Text)
    if not messages and reply is None:
        return None
    chars = settings.session_title_message_chars
    return {
        "messages": "\n".join(f"{n}. {_cut(m.text, chars)}" for n, m in enumerate(messages, 1))
        or NONE_LINE,
        "reply": _cut(reply.text, chars) if reply is not None else NONE_LINE,
    }


async def title_session(
    db: Db,
    gateway: LLMGateway,
    settings: Settings,
    *,
    learner_id: UUID,
    session_id: UUID,
    trigger_seq: int | None = None,
) -> str | None:
    """Name the session once. Returns the title written now, `None` when nothing was done: no
    such session, it is named already, or nobody has said anything yet."""
    params = {"learner_id": learner_id, "session_id": session_id}
    row = await db.fetch_one(_ROW, params, row=_Row)
    if row is None or row.title is not None:
        return None
    variables = await _inputs(db, settings, learner_id, session_id)
    if variables is None:
        return None
    prompt = settings.llm_roles[TITLER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'session_titler' has no prompt")
    key = title_key(session_id)
    result = await gateway.generate_structured(
        TITLER_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        SessionTitleOutput,
        variables,
        learner_id,
        session_id,
        None,
        key,
    )
    title = clean_title(result.value.title, settings.session_title_max_chars)
    if not title:
        raise ValueError("session_titler gave an empty title")
    async with db.transaction():
        # The row is locked: two jobs of one session cannot both write (a repeat is a no-op).
        locked = await db.fetch_one(_ROW_FOR_UPDATE, params, row=_Row)
        if locked is None or locked.title is not None:
            return None
        try:
            await append_events(
                db,
                learner_id,
                [
                    NewEvent(
                        type="session.titled",
                        payload=SessionTitled(session_id=session_id, title=title),
                        actor=prompt,
                        idempotency_key=key,
                        domain_id=locked.domain_id,
                        session_id=session_id,
                        causation_seq=trigger_seq,
                    )
                ],
            )
        except IdempotencyConflictError:
            # The event is in the log with another text (the column was lost): the log wins.
            stored = await db.fetch_one(_TITLED, {"learner_id": learner_id, "key": key}, row=_Title)
            if stored is None:
                raise
            title = stored.title
        await db.execute(_SET, {**params, "title": title})
    return title


@register_job_handler("session.title")
async def handle_session_title(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None or job.session_id is None:
        raise ValueError("session.title needs learner_id and session_id")
    trigger = job.payload.get("trigger_seq")
    await title_session(
        ctx.db,
        ctx.llm,
        ctx.settings,
        learner_id=job.learner_id,
        session_id=job.session_id,
        trigger_seq=int(str(trigger)) if trigger is not None else None,
    )
