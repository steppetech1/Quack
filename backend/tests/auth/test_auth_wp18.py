"""WP18: login by mail link, Google OIDC, sessions, consents (ТЗ Ф4 §8.11 `T-AU1`–`T-AU4`,
`T-AU6`; Т-§19.1, Т-§19.4). `T-AU5` (no `learner_id` from a body) covers the whole API on
sessions and comes with stage II. Integration tests on the Phase 4 database (`pg_db`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis

from quack.auth.consents import give_consent, has_current_terms, revoke_research
from quack.auth.google import EmailNotVerified, learner_for_google
from quack.auth.links import (
    AuthRateLimited,
    LinkInvalid,
    LinkLimiter,
    redeem_link,
    request_link,
)
from quack.auth.sessions import cookie_params, open_session, resolve_session, revoke_session
from quack.config import Settings
from quack.db import Db
from tests.support.email import FakeEmailSender
from tests.support.oidc import FakeOIDC

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
VERIFY_URL = "https://api.qvack.online/auth/link/verify"


def _token(link: str) -> str:
    parts = urlsplit(link)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == VERIFY_URL
    return parse_qs(parts.query)["token"][0]


async def _learner_by_email(db: Db, email: str) -> UUID | None:
    row = await db.fetch_one(
        "SELECT learner_id FROM core.learners WHERE email = %(email)s", {"email": email}
    )
    return None if row is None else row["learner_id"]


async def _link(db: Db, settings: Settings, mail: FakeEmailSender, email: str) -> str:
    limiter = LinkLimiter(None, settings)
    await request_link(
        db, mail, limiter, settings, email=email, ip="10.0.0.1", verify_url=VERIFY_URL, now=NOW
    )
    return _token(mail.last_link(email.lower()))


# --- T-AU1 -----------------------------------------------------------------------------------


async def test_magic_link_flow(pg_db: Db, settings: Settings) -> None:
    """T-AU1: the link comes to `FakeEmailSender`, is redeemed once, expires after
    `auth_link_ttl_min`; only the token hash is stored; the same answer for a new and a known
    address."""
    mail = FakeEmailSender()
    token = await _link(pg_db, settings, mail, "Ann@Example.com")
    stored = await pg_db.fetch_all("SELECT token_hash FROM core.auth_links")
    assert len(stored) == 1 and stored[0]["token_hash"] != token
    assert await _learner_by_email(pg_db, "ann@example.com") is None

    learner = await redeem_link(pg_db, token=token, now=NOW + timedelta(minutes=1))
    assert await _learner_by_email(pg_db, "ann@example.com") == learner
    with pytest.raises(LinkInvalid):
        await redeem_link(pg_db, token=token, now=NOW + timedelta(minutes=2))

    # A known address: the same flow, the same learner.
    again = await _link(pg_db, settings, mail, "ann@example.com")
    assert await redeem_link(pg_db, token=again, now=NOW + timedelta(minutes=1)) == learner

    late = await _link(pg_db, settings, mail, "bob@example.com")
    with pytest.raises(LinkInvalid):
        await redeem_link(
            pg_db, token=late, now=NOW + timedelta(minutes=settings.auth_link_ttl_min + 1)
        )
    with pytest.raises(LinkInvalid):
        await redeem_link(pg_db, token="not-a-token", now=NOW)


def test_cookie_flags(settings: Settings) -> None:
    """T-AU1: `HttpOnly`, `Secure`, `SameSite=Lax`, `Domain = session_cookie_domain`."""
    params = cookie_params(settings)
    assert params["httponly"] is True and params["secure"] is True
    assert params["samesite"] == "lax"
    assert params["domain"] == settings.session_cookie_domain
    assert params["max_age"] == settings.auth_session_ttl_days * 24 * 3600


# --- T-AU2 -----------------------------------------------------------------------------------


async def _exhaust(limiter: LinkLimiter, settings: Settings, *, email: str, ip: str) -> None:
    for _ in range(settings.auth_link_per_email_per_hour):
        await limiter.hit(email=email, ip=ip, now=NOW)
    with pytest.raises(AuthRateLimited):
        await limiter.hit(email=email, ip=ip, now=NOW)


async def test_link_rate_limits_local(settings: Settings) -> None:
    """T-AU2 without Redis: local counters per address and per IP; the hour passes."""
    limiter = LinkLimiter(None, settings)
    await _exhaust(limiter, settings, email="a@example.com", ip="10.0.0.1")
    await limiter.hit(email="a@example.com", ip="10.0.0.1", now=NOW + timedelta(hours=1, seconds=1))

    per_ip = LinkLimiter(None, settings)
    for n in range(settings.auth_link_per_ip_per_hour):
        await per_ip.hit(email=f"u{n}@example.com", ip="10.0.0.9", now=NOW)
    with pytest.raises(AuthRateLimited):
        await per_ip.hit(email="other@example.com", ip="10.0.0.9", now=NOW)


async def test_link_rate_limits_redis(redis: Redis, settings: Settings) -> None:
    """T-AU2 with Redis: the counters are shared by processes."""
    first, second = LinkLimiter(redis, settings), LinkLimiter(redis, settings)
    for n in range(settings.auth_link_per_email_per_hour):
        await (first if n % 2 else second).hit(email="r@example.com", ip=f"10.1.0.{n}", now=NOW)
    with pytest.raises(AuthRateLimited):
        await first.hit(email="r@example.com", ip="10.1.1.1", now=NOW)


# --- T-AU3 -----------------------------------------------------------------------------------


async def test_google_oidc_callback(pg_db: Db) -> None:
    """T-AU3: the learner by the verified address; an unverified one is refused."""
    oidc = FakeOIDC()
    oidc.script("good", email="Gina@Example.com")
    oidc.script("bad", email="eve@example.com", email_verified=False)
    assert "state=s1" in oidc.authorize_url(state="s1")

    learner = await learner_for_google(pg_db, await oidc.identity(code="good"))
    assert await _learner_by_email(pg_db, "gina@example.com") == learner
    assert await learner_for_google(pg_db, await oidc.identity(code="good")) == learner
    with pytest.raises(EmailNotVerified):
        await learner_for_google(pg_db, await oidc.identity(code="bad"))
    assert await _learner_by_email(pg_db, "eve@example.com") is None


# --- T-AU4 -----------------------------------------------------------------------------------


async def test_session_revocation(pg_db: Db, settings: Settings) -> None:
    """T-AU4: a session resolves to its learner until `logout` or expiry; the token is not
    stored."""
    mail = FakeEmailSender()
    learner = await redeem_link(
        pg_db, token=await _link(pg_db, settings, mail, "s@example.com"), now=NOW
    )
    token = await open_session(pg_db, settings, learner_id=learner, now=NOW)
    stored = await pg_db.fetch_one("SELECT token_hash, scope FROM core.auth_sessions")
    assert stored is not None and stored["token_hash"] != token and stored["scope"] == "full"

    session = await resolve_session(pg_db, token=token, now=NOW + timedelta(hours=1))
    assert session is not None and session.learner_id == learner
    expiry = NOW + timedelta(days=settings.auth_session_ttl_days, seconds=1)
    assert await resolve_session(pg_db, token=token, now=expiry) is None

    # Another learner cannot revoke it (isolation, Т-§3.5).
    await revoke_session(pg_db, learner_id=uuid4(), token=token, now=NOW + timedelta(hours=2))
    assert await resolve_session(pg_db, token=token, now=NOW + timedelta(hours=2)) is not None
    await revoke_session(pg_db, learner_id=learner, token=token, now=NOW + timedelta(hours=2))
    assert await resolve_session(pg_db, token=token, now=NOW + timedelta(hours=3)) is None
    assert await resolve_session(pg_db, token="unknown", now=NOW) is None


# --- T-AU6 -----------------------------------------------------------------------------------


async def test_consent_required(pg_db: Db, settings: Settings) -> None:
    """T-AU6: only `consent.given {kind: terms}` of the current `terms_version` counts;
    `research` is revoked by `consent.revoked`."""
    mail = FakeEmailSender()
    learner = await redeem_link(
        pg_db, token=await _link(pg_db, settings, mail, "c@example.com"), now=NOW
    )
    v1 = settings.model_copy(update={"terms_version": "2026-11"})
    assert await has_current_terms(pg_db, learner, v1) is False

    await give_consent(pg_db, learner, kind="terms", version="2026-11", idempotency_key="c1")
    assert await has_current_terms(pg_db, learner, v1) is True
    v2 = settings.model_copy(update={"terms_version": "2026-12"})
    assert await has_current_terms(pg_db, learner, v2) is False
    unset = settings.model_copy(update={"terms_version": None})
    assert await has_current_terms(pg_db, learner, unset) is False

    await give_consent(pg_db, learner, kind="research", version="2026-11", idempotency_key="c2")
    await revoke_research(pg_db, learner, version="2026-11", idempotency_key="c3")
    # Given again after the revocation: a new event, not a duplicate (law 5).
    await give_consent(pg_db, learner, kind="research", version="2026-11", idempotency_key="c4")
    rows = await pg_db.fetch_all(
        "SELECT type, payload FROM core.events WHERE learner_id = %(id)s ORDER BY seq",
        {"id": learner},
    )
    assert [(r["type"], r["payload"]["kind"]) for r in rows] == [
        ("consent.given", "terms"),
        ("consent.given", "research"),
        ("consent.revoked", "research"),
        ("consent.given", "research"),
    ]
