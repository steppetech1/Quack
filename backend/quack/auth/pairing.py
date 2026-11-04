"""Pairing of the phone with a canvas (ТЗ Ф4 WP22.1; Т-§16.1; D-A-128, D-C-128).

`POST /v1/canvases/{id}/pairing` issues a pairing token: 128 random bits, one-time, alive
`pairing_ttl_s`, bound to the learner, the session, the canvas and, if any, the task of the
canvas; `core.pairing_tokens` keeps only its hash. `POST /companion/pair {token}` redeems it —
one `UPDATE … RETURNING` with `used_at IS NULL AND expires_at > now`, so a second exchange of
the same token gets nothing — and opens a `companion` session of that canvas in the same
transaction: upload photos into this canvas and see its status, nothing more.

The canvas is read by this package's own SQL (`content.canvas_boards`): product packages do not
import each other (§4.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from quack.auth.links import new_token, token_hash
from quack.auth.sessions import open_session
from quack.config import Settings
from quack.db import Db

_CANVAS = (
    "SELECT session_id, task_id FROM content.canvas_boards "
    "WHERE learner_id = %(learner_id)s AND canvas_id = %(canvas_id)s"
)
_INSERT_TOKEN = (
    "INSERT INTO core.pairing_tokens "
    "(token_hash, learner_id, session_id, canvas_id, task_id, created_at, expires_at) "
    "VALUES (%(hash)s, %(learner_id)s, %(session_id)s, %(canvas_id)s, %(task_id)s, %(now)s, "
    "%(expires)s)"
)
# The learner of a pairing token is unknown until the token is found by its hash (D-A-128).
_REDEEM_TOKEN = (
    "UPDATE core.pairing_tokens SET used_at = %(now)s "
    "WHERE token_hash = %(hash)s AND used_at IS NULL AND expires_at > %(now)s "
    "RETURNING learner_id, canvas_id"
)


class CanvasNotPairable(Exception):
    """No such canvas of this learner, or it has no session to pair with: 404."""


@dataclass(frozen=True)
class PairingToken:
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class CompanionSession:
    token: str
    learner_id: UUID
    canvas_id: UUID


async def issue_pairing_token(
    db: Db, settings: Settings, *, learner_id: UUID, canvas_id: UUID, now: datetime
) -> PairingToken:
    """A pairing token of the learner's canvas; the session and the task come from the canvas."""
    row = await db.fetch_one(_CANVAS, {"learner_id": learner_id, "canvas_id": canvas_id})
    if row is None or row["session_id"] is None:
        raise CanvasNotPairable(f"canvas {canvas_id} not found")
    token = new_token()
    expires_at = now + timedelta(seconds=settings.pairing_ttl_s)
    await db.execute(
        _INSERT_TOKEN,
        {
            "hash": token_hash(token),
            "learner_id": learner_id,
            "session_id": row["session_id"],
            "canvas_id": canvas_id,
            "task_id": row["task_id"],
            "now": now,
            "expires": expires_at,
        },
    )
    return PairingToken(token=token, expires_at=expires_at)


async def redeem_pairing_token(
    db: Db, settings: Settings, *, token: str, now: datetime
) -> CompanionSession | None:
    """The `companion` session of the token's canvas; `None` for an unknown, used or expired
    token."""
    async with db.transaction():
        row = await db.fetch_one(_REDEEM_TOKEN, {"hash": token_hash(token), "now": now})
        if row is None:
            return None
        session_token = await open_session(
            db,
            settings,
            learner_id=row["learner_id"],
            now=now,
            scope="companion",
            canvas_id=row["canvas_id"],
        )
    return CompanionSession(
        token=session_token, learner_id=row["learner_id"], canvas_id=row["canvas_id"]
    )
