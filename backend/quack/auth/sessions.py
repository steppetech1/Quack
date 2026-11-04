"""Sessions and the cookie (Т-§19.1, ТЗ Ф4 WP18.3).

An opaque token; the database keeps its hash with the learner, the scope (`full`, or
`companion` for the phone of one canvas, WP22.1), the expiry `auth_session_ttl_days` and the
revocation time. `learner_id` comes only from the session, never from a request body.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from quack.api.auth import SESSION_COOKIE, CurrentSession
from quack.auth.links import new_token, token_hash
from quack.config import Settings
from quack.db import Db

type Scope = Literal["full", "companion"]

# The cookie name and the session type live in the API base (`quack.api.auth`), which every
# router depends on (§4.1); D-C-111 names the cookie.
AuthSession = CurrentSession
__all__ = ["SESSION_COOKIE", "AuthSession"]
# The learner of a request is unknown until its session is found by the token (D-C-111).
_SESSION_BY_TOKEN = (
    "SELECT learner_id, scope, canvas_id, expires_at FROM core.auth_sessions "
    "WHERE token_hash = %(hash)s AND revoked_at IS NULL AND expires_at > %(now)s"
)


async def open_session(
    db: Db,
    settings: Settings,
    *,
    learner_id: UUID,
    now: datetime,
    scope: Scope = "full",
    canvas_id: UUID | None = None,
) -> str:
    """A new session; returns the token for the cookie."""
    token = new_token()
    await db.execute(
        "INSERT INTO core.auth_sessions "
        "(token_hash, learner_id, scope, canvas_id, created_at, expires_at) "
        "VALUES (%(hash)s, %(learner_id)s, %(scope)s, %(canvas)s, %(now)s, %(expires)s)",
        {
            "hash": token_hash(token),
            "learner_id": learner_id,
            "scope": scope,
            "canvas": canvas_id,
            "now": now,
            "expires": now + timedelta(days=settings.auth_session_ttl_days),
        },
    )
    return token


async def resolve_session(db: Db, *, token: str, now: datetime) -> AuthSession | None:
    """The live session of the token; `None` if unknown, expired or revoked."""
    row = await db.fetch_one(_SESSION_BY_TOKEN, {"hash": token_hash(token), "now": now})
    if row is None:
        return None
    return AuthSession(
        learner_id=row["learner_id"],
        scope=row["scope"],
        canvas_id=row["canvas_id"],
        expires_at=row["expires_at"],
    )


async def revoke_session(db: Db, *, learner_id: UUID, token: str, now: datetime) -> None:
    """`POST /auth/logout` of the learner of the session: the next request with this token is
    401."""
    await db.execute(
        "UPDATE core.auth_sessions SET revoked_at = %(now)s "
        "WHERE token_hash = %(hash)s AND learner_id = %(learner_id)s AND revoked_at IS NULL",
        {"hash": token_hash(token), "learner_id": learner_id, "now": now},
    )


def cookie_params(settings: Settings) -> dict[str, Any]:
    """Arguments of `Response.set_cookie`: `HttpOnly`, `Secure`, `SameSite=Lax`,
    `Domain = session_cookie_domain`, max age `auth_session_ttl_days` (Т-§19.1)."""
    return {
        "key": SESSION_COOKIE,
        "httponly": True,
        "secure": True,
        "samesite": "lax",
        "domain": settings.session_cookie_domain,
        "max_age": settings.auth_session_ttl_days * 24 * 3600,
        "path": "/",
    }
