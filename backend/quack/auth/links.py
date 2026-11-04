"""Login by a link sent to the mail (Т-§19.1, ТЗ Ф4 WP18.1).

The token is 128 random bits; only its hash is stored (`core.auth_links`). The link lives
`auth_link_ttl_min` and is redeemed once. The answer to a request is the same for a known and
a new address. Limits per address and per IP: `LinkLimiter` (Redis, local without it).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from quack.api.email import EmailSender
from quack.config import Settings
from quack.db import Db

_TOKEN_BYTES = 16  # 128 bits (WP18.1)
# Before login the learner is unknown: the row is found by its address (D-C-111).
_LEARNER_BY_EMAIL = "SELECT learner_id FROM core.learners WHERE email = %(email)s"
_HOUR_S = 3600


class LinkInvalid(Exception):
    """The token is unknown, expired or already redeemed."""


class AuthRateLimited(Exception):
    """Too many link requests for this address or IP within the hour (Т-§19.1)."""

    def __init__(self, retry_after_s: int) -> None:
        super().__init__(f"too many login link requests, retry in {retry_after_s} s")
        self.retry_after_s = retry_after_s


def new_token() -> str:
    """128 random bits, URL-safe."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def token_hash(token: str) -> str:
    """What the database keeps instead of a token."""
    return hashlib.sha256(token.encode()).hexdigest()


class LinkLimiter:
    """`auth_link_per_email_per_hour` and `auth_link_per_ip_per_hour`: Redis counters, local
    counters when Redis is `None` or down (Т-§19.1, Д-§10). A window is a clock hour
    (D-C-111): counters of an hour expire with it."""

    def __init__(self, redis: Redis | None, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings
        self._local: dict[str, int] = {}
        self._local_hour: int | None = None

    async def hit(self, *, email: str, ip: str, now: datetime) -> None:
        """Count one request; `AuthRateLimited` over either limit."""
        hour = int(now.timestamp()) // _HOUR_S
        if hour != self._local_hour:
            # Local counters of past hours are not needed any more (review of #97).
            self._local.clear()
            self._local_hour = hour
        retry_after_s = (hour + 1) * _HOUR_S - int(now.timestamp())
        limits = {
            f"auth:link:email:{email.lower()}:{hour}": self._settings.auth_link_per_email_per_hour,
            f"auth:link:ip:{ip}:{hour}": self._settings.auth_link_per_ip_per_hour,
        }
        counts = await self._count(list(limits))
        if any(count > limits[key] for key, count in zip(limits, counts, strict=True)):
            raise AuthRateLimited(retry_after_s)

    async def _count(self, keys: list[str]) -> list[int]:
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                for key in keys:
                    pipe.incr(key)
                    pipe.expire(key, _HOUR_S, nx=True)
                results = await pipe.execute()
                return [int(results[2 * n]) for n in range(len(keys))]
            except RedisError:
                pass  # Д-§10: without Redis the limits are local
        counts = []
        for key in keys:
            self._local[key] = self._local.get(key, 0) + 1
            counts.append(self._local[key])
        return counts


async def request_link(
    db: Db,
    email_sender: EmailSender,
    limiter: LinkLimiter,
    settings: Settings,
    *,
    email: str,
    ip: str,
    verify_url: str,
    now: datetime,
) -> None:
    """Store the hash of a new token and mail `verify_url?token=…` to `email` (lower case).
    `verify_url` is the absolute URL of `GET /auth/link/verify` built by the router."""
    address = email.strip().lower()
    await limiter.hit(email=address, ip=ip, now=now)
    token = new_token()
    await db.execute(
        "INSERT INTO core.auth_links (token_hash, email, created_at, expires_at) "
        "VALUES (%(hash)s, %(email)s, %(now)s, %(expires)s)",
        {
            "hash": token_hash(token),
            "email": address,
            "now": now,
            "expires": now + timedelta(minutes=settings.auth_link_ttl_min),
        },
    )
    await email_sender.send_login_link(to=address, link=f"{verify_url}?token={token}")


async def learner_for_email(db: Db, email: str, learner_id: UUID | None = None) -> UUID:
    """The learner of the address; a new `core.learners` row for a new one, with `learner_id`
    (a fresh id by default). A known address keeps its own id."""
    address = email.strip().lower()
    await db.execute(
        "INSERT INTO core.learners (learner_id, email) VALUES (%(learner_id)s, %(email)s) "
        "ON CONFLICT (email) DO NOTHING",
        {"learner_id": learner_id or uuid4(), "email": address},
    )
    row = await db.fetch_one(_LEARNER_BY_EMAIL, {"email": address})
    assert row is not None
    return row["learner_id"]


async def redeem_link(db: Db, *, token: str, now: datetime) -> UUID:
    """Redeem the token once; the learner of its address, a new `core.learners` row for a new
    address. `LinkInvalid` if unknown, expired or used."""
    async with db.transaction():
        row = await db.fetch_one(
            "UPDATE core.auth_links SET used_at = %(now)s "
            "WHERE token_hash = %(hash)s AND used_at IS NULL AND expires_at > %(now)s "
            "RETURNING email",
            {"hash": token_hash(token), "now": now},
        )
        if row is None:
            raise LinkInvalid("login link is invalid or expired")
        return await learner_for_email(db, row["email"])
