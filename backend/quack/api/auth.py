"""The only place where JWT is issued and verified (ТЗ Фазы 3 WP1.2).

Token contract of ТЗ-П1 §2.1 п.2 is unchanged: HS256, claims `sub`/`iat`/`exp`, secret
`Settings.jwt_secret` with the dev value `DEV_JWT_SECRET` when `env in ("dev", "test")`.
`get_current_learner_id` additionally requires that the user exists, `deleted_at IS NULL`
and there is no row in `ops.deletion_tombstones` (D-C-004).

Routers attach `get_current_learner_id` at router level, so a request without a token gets
`401 UNAUTHORIZED` before body validation (T-A2). Tests override only this dependency.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import Request
from sqlalchemy import text

from quack.api.errors import ApiError
from quack.api.resources import get_resources
from quack.config import Settings

DEV_JWT_SECRET = "quack-dev-jwt-secret"
JWT_ALGORITHM = "HS256"
BEARER_SCHEME = "bearer"

_ACTIVE_USER_SQL = text(
    "SELECT 1 FROM auth.users u WHERE u.id = :id AND u.deleted_at IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM ops.deletion_tombstones t WHERE t.learner_id = u.id)"
)


def _unauthorized(message: str) -> ApiError:
    return ApiError("UNAUTHORIZED", message, status_code=401)


def jwt_secret_for(settings: Settings) -> str:
    """Signing secret: `jwt_secret`, or `DEV_JWT_SECRET` in dev/test when it is empty."""
    secret = settings.jwt_secret.get_secret_value()
    if secret:
        return secret
    if settings.env in ("dev", "test"):
        return DEV_JWT_SECRET
    raise RuntimeError("JWT_SECRET is not configured")


def issue_access_token(learner_id: UUID, settings: Settings) -> str:
    """HS256 JWT with `sub`, `iat`, `exp = iat + jwt_ttl_days`."""
    now = datetime.now(UTC)
    claims = {
        "sub": str(learner_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_ttl_days)).timestamp()),
    }
    return jwt.encode(claims, jwt_secret_for(settings), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> UUID:
    """Learner id from a valid token; `ApiError(UNAUTHORIZED, 401)` otherwise."""
    try:
        claims = jwt.decode(token, jwt_secret_for(settings), algorithms=[JWT_ALGORITHM])
        return UUID(str(claims["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        raise _unauthorized("invalid or expired token") from exc


def _bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != BEARER_SCHEME or not token:
        raise _unauthorized("missing bearer token")
    return token


async def get_current_learner_id(request: Request) -> UUID:
    """FastAPI dependency: `Authorization: Bearer <JWT>` → learner id of an existing,
    not deleted, not tombstoned user; `ApiError(UNAUTHORIZED, 401)` otherwise."""
    token = _bearer_token(request)
    resources = get_resources(request)
    learner_id = decode_access_token(token, resources.settings)
    async with resources.session_factory() as session:
        active = await session.scalar(_ACTIVE_USER_SQL, {"id": learner_id})
    if active is None:
        raise _unauthorized("learner not found or deleted")
    return learner_id
