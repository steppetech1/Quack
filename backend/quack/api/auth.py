"""Authentication of the API on Phase 4 sessions (ТЗ Ф4 WP18.3, WP18.4; D-C-112, D-A-113).

The password, JWT and `/v1/auth/register|login|me` of Phases 1–3 are removed (§2.3 row 3).
`get_current_learner_id` keeps its name and signature, so the routers of every stream stay as
they are; it reads the session cookie (`get_current_session`). A deleted learner needs no extra
check: `delete_account` removes the account and its sessions physically (WP25, D-C-170), so the
cookie no longer resolves.

Routers attach `get_current_learner_id` at router level, so a request without a session gets
`401 UNAUTHENTICATED` before body validation (T-A2). Tests override only this dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID

from fastapi import Depends, Request, WebSocket

from quack.api.errors import ApiError
from quack.api.resources import get_resources
from quack.config import Settings
from quack.db import Db


async def get_current_learner_id(request: Request) -> UUID:
    """FastAPI dependency: the learner of the live session of the cookie;
    `ApiError(UNAUTHENTICATED, 401)` otherwise."""
    return (await get_current_session(request)).learner_id


# --- Sessions of Phase 4 (ТЗ Ф4 WP18.3, WP18.4) ------------------------------------------
# The session lives in `core.auth_sessions` (`quack.auth.sessions`, a product package). Routers
# of every stream depend on this module (API base, §4.1), so the product functions reach it
# through `app.state` set by the assembly (`quack.api.app`): `session_resolver`, `terms_checker`.

SESSION_COOKIE = "quack_session"  # D-C-111


@dataclass(frozen=True)
class CurrentSession:
    learner_id: UUID
    scope: Literal["full", "companion"]
    canvas_id: UUID | None
    expires_at: datetime


class SessionResolver(Protocol):
    async def __call__(self, db: Db, *, token: str, now: datetime) -> CurrentSession | None: ...


class TermsChecker(Protocol):
    async def __call__(self, db: Db, learner_id: UUID, settings: Settings) -> bool: ...


def _unauthenticated(message: str) -> ApiError:
    return ApiError("UNAUTHENTICATED", message, status_code=401)


async def get_current_session(request: Request) -> CurrentSession:
    """FastAPI dependency: the live `full` session of the cookie; `401 UNAUTHENTICATED` without
    one. A `companion` session may only upload into its canvas and see its status (WP22.1,
    T-CP1): every route behind this dependency answers it `403` (D-C-128). `learner_id` comes
    only from here, never from a request body (Т-§19.1)."""
    session = await get_any_session(request)
    if session.scope != "full":
        raise ApiError("UNAUTHORIZED", "a companion session cannot do this", status_code=403)
    return session


async def get_any_session(request: Request) -> CurrentSession:
    """FastAPI dependency: the live session of the cookie of any scope; `401` without one. Only
    for the routes a `companion` may use — the upload of photos into its canvas (WP22.1–22.2);
    the route checks the canvas itself (D-C-128)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _unauthenticated("not signed in")
    resolver: SessionResolver = request.app.state.session_resolver
    session = await resolver(get_resources(request).db, token=token, now=datetime.now(UTC))
    if session is None:
        raise _unauthenticated("session expired or signed out")
    return session


async def websocket_session(websocket: WebSocket) -> CurrentSession | None:
    """The live session of the cookie of a WebSocket handshake (ТЗ Ф4 WP21.2); `None` without
    one — the handler refuses the socket."""
    token = websocket.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    resolver: SessionResolver = websocket.app.state.session_resolver
    return await resolver(get_resources(websocket).db, token=token, now=datetime.now(UTC))


async def require_terms(
    request: Request, session: Annotated[CurrentSession, Depends(get_current_session)]
) -> None:
    """FastAPI dependency for starting a session (WP18.4): `403 CONSENT_REQUIRED` without
    `consent.given {kind: terms}` of the current `terms_version` (Т-§19.4). The session comes
    through `Depends`, so a route with both dependencies resolves the cookie once."""
    resources = get_resources(request)
    checker: TermsChecker = request.app.state.terms_checker
    if not await checker(resources.db, session.learner_id, resources.settings):
        raise ApiError("CONSENT_REQUIRED", "terms of use are not accepted", status_code=403)
