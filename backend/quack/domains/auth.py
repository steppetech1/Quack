"""Registration, login and Argon2id password hashing (ТЗ §2.1, Т-§16.3, Т-§22.2). JWT is issued
and verified only in `quack.api.auth`; the per-IP limit is `quack.api.ratelimit` (ТЗ Фазы 3
WP1.2, WP1.5).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import orjson
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from quack.api.auth import DEV_JWT_SECRET as DEV_JWT_SECRET  # re-export for Phase 2 tests
from quack.api.auth import JWT_ALGORITHM as JWT_ALGORITHM  # re-export for Phase 2 tests
from quack.api.auth import issue_access_token
from quack.api.errors import ApiError
from quack.api.resources import AppResources
from quack.config import Settings
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


async def register_learner(
    resources: AppResources,
    settings: Settings,
    req: RegisterRequest,
) -> AuthResponse:
    """Register a new learner in `auth.users` and emit `learner.registered` in one transaction
    (ТЗ §2.1, §3; Т-§16.3).
    """
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

    token = issue_access_token(learner_id, settings)
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
) -> AuthResponse:
    """Authenticate an existing active learner and issue a JWT (ТЗ §2.1, §3; Т-§16.3, Т-§19)."""
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
        token = issue_access_token(learner_id, settings)
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
