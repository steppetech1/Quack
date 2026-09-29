"""Cookie-based student authentication."""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import jwt
import structlog
from fastapi import APIRouter, Depends, Request, Response
from neo4j.exceptions import ServiceUnavailable, SessionExpired
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, StringConstraints
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    client_ip,
    get_current_student,
    get_graph,
    get_redis,
    get_session,
)
from app.config import settings
from app.db.repo.users import create_user, get_user_by_email
from app.errors import Conflict, TooManyRequests, Unauthorized
from app.graph.queries.personal import ensure_student as _ensure_student
from app.keys import login_ratelimit
from app.schemas.auth import LoginIn, StudentCtx

router = APIRouter(prefix="/auth", tags=["auth"])
_passwords = CryptContext(schemes=["bcrypt"], bcrypt__rounds=12)
_logger = structlog.get_logger(__name__)


class RegisterIn(BaseModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ]
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=8, max_length=128)]


class AuthOut(StudentCtx):
    """Identity plus the display name. The name rides in the token so that
    /auth/me stays free of database access."""

    name: str


def _display_name(name: str | None, email: str) -> str:
    return name or email.split("@", 1)[0]


def issue_token(student_id: UUID | str, email: str, name: str | None = None) -> str:
    issued_at = datetime.now(UTC)
    claims = {
        "sub": str(student_id),
        "email": email,
        "iat": issued_at,
        "exp": issued_at + timedelta(days=settings.JWT_TTL_DAYS),
    }
    if name:
        claims["name"] = name
    return jwt.encode(claims, settings.JWT_SECRET.get_secret_value(), algorithm="HS256")


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="quack_token",
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.ENV == "prod",
    )


async def _rate_limit(request: Request, redis: Redis) -> None:
    key = login_ratelimit(client_ip(request))
    attempts = await redis.incr(key)
    if attempts == 1:
        await redis.expire(key, 60)
    if attempts > 10:
        raise TooManyRequests("too many login attempts")


def hash_password(raw: str) -> str:
    return _passwords.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return _passwords.verify(raw, hashed)


@router.post("/register", status_code=201, response_model=AuthOut)
async def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> AuthOut:
    await _rate_limit(request, redis)
    email = body.email.lower()
    user_id = await create_user(session, email, hash_password(body.password), body.name)
    if user_id is None:
        raise Conflict("email already registered")
    _set_session_cookie(response, issue_token(user_id, email, body.name))
    return AuthOut(student_id=user_id, email=email, name=body.name)


@router.post("/login", response_model=AuthOut)
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> AuthOut:
    await _rate_limit(request, redis)

    user = await get_user_by_email(session, body.email.lower())
    if user is None or not verify_password(body.password, user.password_hash):
        raise Unauthorized("invalid credentials")

    name = _display_name(user.name, user.email)
    _set_session_cookie(response, issue_token(user.id, user.email, name))
    return AuthOut(student_id=user.id, email=user.email, name=name)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    student: Annotated[StudentCtx, Depends(get_current_student)],
) -> None:
    response.delete_cookie(
        key="quack_token",
        path="/",
        secure=settings.ENV == "prod",
        httponly=True,
        samesite="lax",
    )


@router.get("/me", response_model=AuthOut)
async def me(
    request: Request,
    student: Annotated[StudentCtx, Depends(get_current_student)],
    graph: Annotated[object, Depends(get_graph)],
) -> AuthOut:
    # graph.client.create_driver returns None when Neo4j is unreachable; the
    # identity answer must not depend on the graph being up (product-logic §6.3).
    #
    # A driver that exists is not a graph that answers: Neo4j can go down after
    # the app started, and then the MERGE raises. Without this the whole sign-in
    # fails on a 500, and the browser cannot even read why — an unhandled error
    # carries no CORS headers. The node is created by the next /auth/me instead.
    if graph is None:
        _logger.warning(
            "student_node_skipped",
            reason="neo4j_unavailable",
            student_id=str(student.student_id),
        )
    else:
        try:
            await _ensure_student(graph, student.student_id)
        except (ServiceUnavailable, SessionExpired) as exc:
            _logger.warning(
                "student_node_skipped",
                reason="neo4j_down",
                student_id=str(student.student_id),
                error=str(exc),
            )
    # get_current_student has already verified the token; only read the claim.
    claims = jwt.decode(
        request.cookies["quack_token"],
        settings.JWT_SECRET.get_secret_value(),
        algorithms=["HS256"],
    )
    return AuthOut(
        student_id=student.student_id,
        email=student.email,
        name=_display_name(claims.get("name"), student.email),
    )
