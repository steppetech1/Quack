"""Integration tests for Stream 1 Contour 1: Auth, JWT, Rate-Limit, Preferences, and
Two-Phase Account Deletion (ТЗ §2.1, §3, §5; Т-§16.3, Т-§17.3, Т-§19, Т-§22.2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import jwt
from redis.asyncio import Redis
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import ratelimit_key
from quack.domains.auth import DEV_JWT_SECRET, JWT_ALGORITHM
from quack.jobs import kinds
from quack.jobs.queue import enqueue_job


async def _q(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def test_register_normalizes_email_and_rejects_duplicate(
    api_client: httpx.AsyncClient,
    db: SessionFactory,
    settings: Settings,
) -> None:
    """1. Registration normalizes email, hashes password with Argon2, emits `learner.registered`,
    and rejects duplicate registration with 409 IDEMPOTENCY_CONFLICT.
    """
    resp = await api_client.post(
        "/v1/auth/register",
        json={
            "email": "  Alice.Learner@Example.COM  ",
            "password": "correct-horse-battery-staple",
            "display_name": "Alice",
            "preferences": {"pace": "normal", "daily_goal_min": 30},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    learner_id = UUID(body["learner_id"])
    assert body["email"] == "alice.learner@example.com"
    assert body["display_name"] == "Alice"
    assert body["preferences"] == {"pace": "normal", "daily_goal_min": 30}

    secret = settings.jwt_secret.get_secret_value() or DEV_JWT_SECRET
    claims = jwt.decode(body["token"], secret, algorithms=[JWT_ALGORITHM])
    assert claims["sub"] == str(learner_id)
    assert "iat" in claims and "exp" in claims
    assert claims["exp"] > claims["iat"]

    [user_row] = await _q(
        db,
        "SELECT id, email, password_hash, display_name, preferences, deleted_at "
        "FROM auth.users WHERE id = :id",
        id=learner_id,
    )
    assert user_row["email"] == "alice.learner@example.com"
    assert user_row["display_name"] == "Alice"
    assert user_row["preferences"] == {"pace": "normal", "daily_goal_min": 30}
    assert user_row["deleted_at"] is None
    assert user_row["password_hash"] != "correct-horse-battery-staple"
    assert str(user_row["password_hash"]).startswith("$argon2")

    [event_row] = await _q(
        db,
        "SELECT seq, type, actor, idempotency_key, payload FROM core.events WHERE learner_id = :id",
        id=learner_id,
    )
    assert event_row["seq"] == 1
    assert event_row["type"] == "learner.registered"
    assert event_row["actor"] == "learner"
    assert event_row["idempotency_key"] == f"register:{learner_id}"
    assert event_row["payload"] == {
        "email": "alice.learner@example.com",
        "display_name": "Alice",
        "preferences": {"pace": "normal", "daily_goal_min": 30},
    }

    dup_resp = await api_client.post(
        "/v1/auth/register",
        json={
            "email": "ALICE.LEARNER@example.com",
            "password": "another-password-123",
            "display_name": "Alice Duplicate",
        },
    )
    assert dup_resp.status_code == 409
    dup_error = dup_resp.json()["error"]
    assert dup_error["code"] == "IDEMPOTENCY_CONFLICT"
    assert isinstance(dup_error["message"], str) and dup_error["message"]
    assert "request_id" in dup_error


async def test_login_and_me_jwt_validation(
    api_client: httpx.AsyncClient,
    settings: Settings,
) -> None:
    """2. Login verifies credentials and issues JWT; `/v1/auth/me` accepts valid Bearer JWT
    and rejects missing, malformed, or expired tokens with 401 UNAUTHORIZED.
    """
    reg_resp = await api_client.post(
        "/v1/auth/register",
        json={
            "email": "bob@example.com",
            "password": "super-secret-password",
            "display_name": "Bob",
            "preferences": {"style": "visual"},
        },
    )
    assert reg_resp.status_code == 201
    learner_id = UUID(reg_resp.json()["learner_id"])

    login_ok = await api_client.post(
        "/v1/auth/login",
        json={"email": "  BOB@Example.com ", "password": "super-secret-password"},
    )
    assert login_ok.status_code == 200
    login_body = login_ok.json()
    token = login_body["token"]
    assert UUID(login_body["learner_id"]) == learner_id
    assert login_body["email"] == "bob@example.com"
    assert login_body["display_name"] == "Bob"
    assert login_body["preferences"] == {"style": "visual"}

    bad_pw = await api_client.post(
        "/v1/auth/login",
        json={"email": "bob@example.com", "password": "wrong-password-123"},
    )
    assert bad_pw.status_code == 401
    assert bad_pw.json()["error"]["code"] == "UNAUTHORIZED"

    unknown_email = await api_client.post(
        "/v1/auth/login",
        json={"email": "nobody@example.com", "password": "super-secret-password"},
    )
    assert unknown_email.status_code == 401
    assert unknown_email.json()["error"]["code"] == "UNAUTHORIZED"

    me_ok = await api_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_ok.status_code == 200
    me_body = me_ok.json()
    assert UUID(me_body["learner_id"]) == learner_id
    assert me_body["email"] == "bob@example.com"
    assert me_body["display_name"] == "Bob"
    assert me_body["preferences"] == {"style": "visual"}
    assert me_body["created_at"]

    no_header = await api_client.get("/v1/auth/me")
    assert no_header.status_code == 401
    assert no_header.json()["error"]["code"] == "UNAUTHORIZED"

    corrupted = await api_client.get(
        "/v1/auth/me",
        headers={"Authorization": "Bearer malformed.jwt.token"},
    )
    assert corrupted.status_code == 401
    assert corrupted.json()["error"]["code"] == "UNAUTHORIZED"

    past = datetime.now(UTC) - timedelta(days=8)
    secret = settings.jwt_secret.get_secret_value() or DEV_JWT_SECRET
    expired_token = jwt.encode(
        {
            "sub": str(learner_id),
            "iat": int(past.timestamp()),
            "exp": int((past + timedelta(hours=1)).timestamp()),
        },
        secret,
        algorithm=JWT_ALGORITHM,
    )
    expired_resp = await api_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert expired_resp.status_code == 401
    assert expired_resp.json()["error"]["code"] == "UNAUTHORIZED"


async def test_update_preferences_and_idempotency(
    api_client: httpx.AsyncClient,
    db: SessionFactory,
    seeded_learner: tuple[UUID, str],
) -> None:
    """3. `PATCH /v1/learner/preferences` updates `auth.users.preferences`, appends
    `learner.preferences_updated`, and handles idempotent retries with the same key.
    """
    learner_id, token = seeded_learner
    new_prefs = {"pace": "fast", "style": "socratic", "daily_goal_min": 45}

    resp = await api_client.patch(
        "/v1/learner/preferences",
        headers={"Authorization": f"Bearer {token}"},
        json={"preferences": new_prefs, "idempotency_key": "prefs-key-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["preferences"] == new_prefs
    assert body["seq"] == 2

    [user_row] = await _q(
        db,
        "SELECT preferences FROM auth.users WHERE id = :id",
        id=learner_id,
    )
    assert user_row["preferences"] == new_prefs

    events = await _q(
        db,
        "SELECT seq, type, actor, idempotency_key, payload "
        "FROM core.events WHERE learner_id = :id AND type = 'learner.preferences_updated'",
        id=learner_id,
    )
    assert len(events) == 1
    assert events[0]["seq"] == 2
    assert events[0]["actor"] == "learner"
    assert events[0]["idempotency_key"] == "prefs-key-1"
    assert events[0]["payload"] == {"preferences": new_prefs}

    retry_resp = await api_client.patch(
        "/v1/learner/preferences",
        headers={"Authorization": f"Bearer {token}"},
        json={"preferences": new_prefs, "idempotency_key": "prefs-key-1"},
    )
    assert retry_resp.status_code == 200
    assert retry_resp.json() == {"preferences": new_prefs, "seq": 2}

    events_after = await _q(
        db,
        "SELECT seq FROM core.events "
        "WHERE learner_id = :id AND type = 'learner.preferences_updated'",
        id=learner_id,
    )
    assert len(events_after) == 1


async def test_auth_rate_limiting_per_ip(
    api_client: httpx.AsyncClient,
    redis: Redis,
) -> None:
    """4. Rate-limiting on `/v1/auth/login` (and `/v1/auth/register`): first 10 requests/min
    succeed or fail by credentials, 11th request from the same IP returns 429 RATE_LIMITED,
    and Redis stores `ratelimit:auth:<ip>` with a positive TTL.
    """
    reg = await api_client.post(
        "/v1/auth/register",
        json={"email": "ratelimit@example.com", "password": "valid-password-123"},
    )
    assert reg.status_code == 201
    await redis.flushdb()

    for i in range(10):
        pw = "valid-password-123" if i % 2 == 0 else "wrong-password-123"
        expected_status = 200 if i % 2 == 0 else 401
        r = await api_client.post(
            "/v1/auth/login",
            json={"email": "ratelimit@example.com", "password": pw},
        )
        assert r.status_code == expected_status

    eleventh = await api_client.post(
        "/v1/auth/login",
        json={"email": "ratelimit@example.com", "password": "valid-password-123"},
    )
    assert eleventh.status_code == 429
    assert eleventh.json()["error"]["code"] == "RATE_LIMITED"

    reg_blocked = await api_client.post(
        "/v1/auth/register",
        json={"email": "another@example.com", "password": "valid-password-123"},
    )
    assert reg_blocked.status_code == 429
    assert reg_blocked.json()["error"]["code"] == "RATE_LIMITED"

    rl_key = ratelimit_key("auth", "127.0.0.1")
    val = await redis.get(rl_key)
    ttl = await redis.ttl(rl_key)
    assert val is not None and int(val) >= 10
    assert 0 < ttl <= 60


async def test_two_phase_account_deletion(
    api_client: httpx.AsyncClient,
    db: SessionFactory,
    seeded_learner: tuple[UUID, str],
) -> None:
    """5. `DELETE /v1/learner` marks `auth.users.deleted_at`, cancels queued jobs in `ops.jobs`,
    inserts `pending_purge` into `ops.deletion_tombstones`, and invalidates subsequent JWT calls.
    """
    learner_id, token = seeded_learner
    async with db() as session, session.begin():
        job_id = await enqueue_job(
            session,
            kinds.graph_curate(learner_id, uuid4(), {"domain_id": str(uuid4())}),
        )

    del_resp = await api_client.delete(
        "/v1/learner",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 200
    del_body = del_resp.json()
    assert del_body["status"] == "pending_purge"
    assert del_body["requested_at"]

    [user_row] = await _q(
        db,
        "SELECT deleted_at FROM auth.users WHERE id = :id",
        id=learner_id,
    )
    assert user_row["deleted_at"] is not None

    [tombstone] = await _q(
        db,
        "SELECT learner_id, requested_at, status FROM ops.deletion_tombstones "
        "WHERE learner_id = :id",
        id=learner_id,
    )
    assert tombstone["learner_id"] == learner_id
    assert tombstone["status"] == "pending_purge"
    assert tombstone["requested_at"] is not None

    [job_row] = await _q(
        db,
        "SELECT status FROM ops.jobs WHERE id = :id",
        id=job_id,
    )
    assert job_row["status"] == "cancelled"

    me_after = await api_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_after.status_code == 401
    assert me_after.json()["error"]["code"] == "UNAUTHORIZED"
