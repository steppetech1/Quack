"""Authentication, Argon2id password hashing, JWT issuance/verification, and Redis rate
limiting (ТЗ §2.1, Т-§16.3, Т-§19, Т-§22.2).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Request
from redis.asyncio import Redis

from quack.api.app import AppResources
from quack.config import Settings
from quack.domains.schemas import (
    AuthResponse,
    LearnerMeResponse,
    LoginRequest,
    RegisterRequest,
)

DEV_JWT_SECRET = "quack-dev-jwt-secret"
JWT_ALGORITHM = "HS256"
JWT_TTL_DAYS = 7
AUTH_RATE_LIMIT_MAX_REQUESTS = 10
AUTH_RATE_LIMIT_TTL_SEC = 60


def normalize_email(email: str) -> str:
    """Normalize learner email (`strip().lower()`) per ТЗ §2.1 and Т-§16.3."""
    raise NotImplementedError


def hash_password(password: str) -> str:
    """Hash password with Argon2id via `pwdlib` (ТЗ §2.1, Т-§16.3)."""
    raise NotImplementedError


def verify_password(password: str, password_hash: str) -> bool:
    """Verify plaintext password against Argon2id hash (ТЗ §2.1, Т-§16.3)."""
    raise NotImplementedError


def jwt_secret_for(settings: Settings) -> str:
    """Resolve JWT signing secret, falling back to `DEV_JWT_SECRET` in dev/test (ТЗ §2.1)."""
    raise NotImplementedError


def create_access_token(learner_id: UUID, settings: Settings) -> str:
    """Create a 7-day HS256 JWT with `sub`, `iat`, `exp` claims (ТЗ §2.1, Т-§19)."""
    raise NotImplementedError


def decode_access_token(token: str, settings: Settings) -> UUID:
    """Decode and validate an HS256 JWT, returning `learner_id` or raising `ApiError` (Т-§19)."""
    raise NotImplementedError


async def check_auth_rate_limit(redis: Redis | None, client_ip: str) -> None:
    """Enforce 10 req/min rate limit per IP on `/v1/auth/*` with fail-open on Redis error
    (ТЗ §2.1, Т-§17.3, Т-§19).
    """
    raise NotImplementedError


async def get_current_learner_id(request: Request) -> UUID:
    """FastAPI dependency: verify Bearer JWT and check `auth.users.deleted_at IS NULL`
    and absence of `ops.deletion_tombstones` (ТЗ §2.1, Т-§16.3, Т-§19).
    """
    raise NotImplementedError


async def register_learner(
    resources: AppResources,
    settings: Settings,
    req: RegisterRequest,
    *,
    client_ip: str = "unknown",
) -> AuthResponse:
    """Register a new learner in `auth.users` and emit `learner.registered` in one transaction
    (ТЗ §2.1, §3; Т-§16.3).
    """
    raise NotImplementedError


async def login_learner(
    resources: AppResources,
    settings: Settings,
    req: LoginRequest,
    *,
    client_ip: str = "unknown",
) -> AuthResponse:
    """Authenticate an existing active learner and issue a JWT (ТЗ §2.1, §3; Т-§16.3, Т-§19)."""
    raise NotImplementedError


async def get_learner_me(
    resources: AppResources,
    learner_id: UUID,
) -> LearnerMeResponse:
    """Return profile and preferences of the authenticated learner (ТЗ §3, Т-§16.3)."""
    raise NotImplementedError
