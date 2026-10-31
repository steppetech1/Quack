"""The only place where JWT is issued and verified (ТЗ Фазы 3 WP1.2).

Token contract of ТЗ-П1 §2.1 п.2 is unchanged: HS256, claims `sub`/`iat`/`exp`, secret
`Settings.jwt_secret` with the dev value `DEV_JWT_SECRET` when `env in ("dev", "test")`.
`get_current_learner_id` additionally requires that the user exists, `deleted_at IS NULL`
and there is no row in `ops.deletion_tombstones`.

Routers attach `get_current_learner_id` at router level, so a request without a token gets
`401 UNAUTHORIZED` before body validation (T-A2). Tests override only this dependency.

Stage I: signatures only; Stage III moves `quack/domains/auth.py` token code here and removes
the own JWT checks of `quack/session/deps.py` and `quack/artifacts/deps.py` (§7.3 case 1).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Request

from quack.config import Settings

DEV_JWT_SECRET = "quack-dev-jwt-secret"
JWT_ALGORITHM = "HS256"


def jwt_secret_for(settings: Settings) -> str:
    """Signing secret: `jwt_secret`, or `DEV_JWT_SECRET` in dev/test when it is empty."""
    raise NotImplementedError


def issue_access_token(learner_id: UUID, settings: Settings) -> str:
    """HS256 JWT with `sub`, `iat`, `exp = iat + jwt_ttl_days`."""
    raise NotImplementedError


def decode_access_token(token: str, settings: Settings) -> UUID:
    """Learner id from a valid token; `ApiError(UNAUTHORIZED, 401)` otherwise."""
    raise NotImplementedError


async def get_current_learner_id(request: Request) -> UUID:
    """FastAPI dependency: `Authorization: Bearer <JWT>` → learner id of an existing,
    not deleted, not tombstoned user; `ApiError(UNAUTHORIZED, 401)` otherwise."""
    raise NotImplementedError
