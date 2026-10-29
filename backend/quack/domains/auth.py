"""Authentication, Argon2id password hashing, JWT issuance/verification, and Redis rate
limiting (ТЗ §2.1, Т-§16.3, Т-§19, Т-§22.2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import orjson
from fastapi import Request
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from quack.api.app import AppResources, get_resources
from quack.api.errors import ApiError
from quack.config import Settings
from quack.db.redis import ratelimit_key
from quack.domains.schemas import (
    AuthResponse,
    LearnerMeResponse,
    LoginRequest,
    RegisterRequest,
)
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.logging import get_logger

log = get_logger(__name__)

DEV_JWT_SECRET = "quack-dev-jwt-secret"
JWT_ALGORITHM = "HS256"
JWT_TTL_DAYS = 7
AUTH_RATE_LIMIT_MAX_REQUESTS = 10
AUTH_RATE_LIMIT_TTL_SEC = 60

_pwd_hasher = PasswordHash((Argon2Hasher(),))


def normalize_email(email: str) -> str:
    """Normalize learner email (`strip().lower()`) per ТЗ §2.1 and Т-§16.3."""
    cleaned = email.strip().lower()
    if not cleaned or "@" not in cleaned:
        raise ApiError("VALIDATION_ERROR", "invalid email format", status_code=422)
    return cleaned


def hash_password(password: str) -> str:
    """Hash password with Argon2id via `pwdlib` (ТЗ §2.1, Т-§16.3)."""
    if len(password) < 8:
        raise ApiError(
            "VALIDATION_ERROR", "password must be at least 8 characters", status_code=422
        )
    return _pwd_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify plaintext password against Argon2id hash (ТЗ §2.1, Т-§16.3)."""
    try:
        return _pwd_hasher.verify(password, password_hash)
    except Exception:
        return False


def jwt_secret_for(settings: Settings) -> str:
    """Resolve JWT signing secret, falling back to `DEV_JWT_SECRET` in dev/test (ТЗ §2.1)."""
    secret = settings.jwt_secret.get_secret_value() if settings.jwt_secret else ""
    if not secret:
        if settings.env in ("dev", "test"):
            return DEV_JWT_SECRET
        raise ApiError("VALIDATION_ERROR", "jwt_secret is not configured", status_code=500)
    return secret


def create_access_token(learner_id: UUID, settings: Settings) -> str:
    """Create a 7-day HS256 JWT with `sub`, `iat`, `exp` claims (ТЗ §2.1, Т-§19)."""
    now = datetime.now(UTC)
    secret = jwt_secret_for(settings)
    payload = {
        "sub": str(learner_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=JWT_TTL_DAYS)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> UUID:
    """Decode and validate an HS256 JWT, returning `learner_id` or raising `ApiError` (Т-§19)."""
    secret = jwt_secret_for(settings)
    try:
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            raise ApiError("UNAUTHORIZED", "missing sub claim", status_code=401)
        return UUID(str(sub))
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise ApiError("UNAUTHORIZED", "invalid or expired token", status_code=401) from exc


async def check_auth_rate_limit(redis: Redis | None, client_ip: str) -> None:
    """Enforce 10 req/min rate limit per IP on `/v1/auth/*` with fail-open on Redis error
    (ТЗ §2.1, Т-§17.3, Т-§19).
    """
    if redis is None:
        return
    key = ratelimit_key("auth", client_ip)
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, AUTH_RATE_LIMIT_TTL_SEC)
        if count > AUTH_RATE_LIMIT_MAX_REQUESTS:
            ttl = await redis.ttl(key)
            if ttl < 1:
                ttl = AUTH_RATE_LIMIT_TTL_SEC
                await redis.expire(key, AUTH_RATE_LIMIT_TTL_SEC)
            raise ApiError(
                "RATE_LIMITED",
                "too many authentication attempts",
                status_code=429,
                details={"retry_after_sec": ttl},
            )
    except ApiError:
        raise
    except Exception as exc:
        log.warning("redis_rate_limit_failed", error=str(exc), client_ip=client_ip)


async def get_current_learner_id(request: Request) -> UUID:
    """FastAPI dependency: verify Bearer JWT and check `auth.users.deleted_at IS NULL`
    and absence of `ops.deletion_tombstones` (ТЗ §2.1, Т-§16.3, Т-§19).
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise ApiError("UNAUTHORIZED", "missing bearer token", status_code=401)
    token = header.removeprefix("Bearer ").strip()
    if not token:
        raise ApiError("UNAUTHORIZED", "missing bearer token", status_code=401)

    settings: Settings = request.app.state.settings
    learner_id = decode_access_token(token, settings)

    resources = get_resources(request)
    async with resources.session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT u.deleted_at, t.status AS tombstone_status "
                        "FROM auth.users u "
                        "LEFT JOIN ops.deletion_tombstones t ON t.learner_id = u.id "
                        "WHERE u.id = :id"
                    ),
                    {"id": learner_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None or row["deleted_at"] is not None or row["tombstone_status"] is not None:
            raise ApiError("UNAUTHORIZED", "user not found or deleted", status_code=401)

    return learner_id


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
    await check_auth_rate_limit(resources.redis, client_ip)
    email = normalize_email(req.email)
    pw_hash = hash_password(req.password)
    learner_id = uuid4()

    async with resources.session_factory() as session, session.begin():
        existing = (
            await session.execute(
                text("SELECT id FROM auth.users WHERE email = :email"),
                {"email": email},
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ApiError(
                "IDEMPOTENCY_CONFLICT",
                f"email '{email}' is already registered",
                status_code=409,
            )

        try:
            await session.execute(
                text(
                    "INSERT INTO auth.users (id, email, password_hash, display_name, preferences) "
                    "VALUES (:id, :email, :pw, :name, CAST(:prefs AS JSONB))"
                ),
                {
                    "id": learner_id,
                    "email": email,
                    "pw": pw_hash,
                    "name": req.display_name,
                    "prefs": orjson.dumps(req.preferences).decode(),
                },
            )
        except IntegrityError as exc:
            raise ApiError(
                "IDEMPOTENCY_CONFLICT",
                f"email '{email}' is already registered",
                status_code=409,
            ) from exc

        await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="learner.registered",
                    actor="learner",
                    idempotency_key=f"register:{learner_id}",
                    payload={
                        "email": email,
                        "display_name": req.display_name,
                        "preferences": req.preferences,
                    },
                )
            ],
            publish_wakeup=True,
            redis=resources.redis,
        )

    token = create_access_token(learner_id, settings)
    return AuthResponse(
        token=token,
        learner_id=learner_id,
        email=email,
        display_name=req.display_name,
        preferences=req.preferences,
    )


async def login_learner(
    resources: AppResources,
    settings: Settings,
    req: LoginRequest,
    *,
    client_ip: str = "unknown",
) -> AuthResponse:
    """Authenticate an existing active learner and issue a JWT (ТЗ §2.1, §3; Т-§16.3, Т-§19)."""
    await check_auth_rate_limit(resources.redis, client_ip)
    email = normalize_email(req.email)

    async with resources.session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT u.id, u.email, u.password_hash, u.display_name, u.preferences, "
                        "u.deleted_at, t.status AS tombstone_status "
                        "FROM auth.users u "
                        "LEFT JOIN ops.deletion_tombstones t ON t.learner_id = u.id "
                        "WHERE u.email = :email"
                    ),
                    {"email": email},
                )
            )
            .mappings()
            .first()
        )

        if (
            row is None
            or row["deleted_at"] is not None
            or row["tombstone_status"] is not None
            or not verify_password(req.password, str(row["password_hash"]))
        ):
            raise ApiError("UNAUTHORIZED", "invalid email or password", status_code=401)

        learner_id: UUID = row["id"]
        token = create_access_token(learner_id, settings)
        return AuthResponse(
            token=token,
            learner_id=learner_id,
            email=row["email"],
            display_name=row["display_name"],
            preferences=row["preferences"] or {},
        )


async def get_learner_me(
    resources: AppResources,
    learner_id: UUID,
) -> LearnerMeResponse:
    """Return profile and preferences of the authenticated learner (ТЗ §3, Т-§16.3)."""
    async with resources.session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT u.id, u.email, u.display_name, u.preferences, u.created_at, "
                        "u.deleted_at, t.status AS tombstone_status "
                        "FROM auth.users u "
                        "LEFT JOIN ops.deletion_tombstones t ON t.learner_id = u.id "
                        "WHERE u.id = :id"
                    ),
                    {"id": learner_id},
                )
            )
            .mappings()
            .first()
        )

        if row is None or row["deleted_at"] is not None or row["tombstone_status"] is not None:
            raise ApiError("UNAUTHORIZED", "learner not found or deleted", status_code=401)

        return LearnerMeResponse(
            learner_id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            preferences=row["preferences"] or {},
            created_at=row["created_at"],
        )
