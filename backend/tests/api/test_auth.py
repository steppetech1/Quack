"""Authentication and dependency contracts without external services."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api import auth, deps
from app.config import Settings
from app.db.repo.users import UserRow
from app.errors import NotFound
from app.main import create_app

pytestmark = pytest.mark.phase1


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations.append((key, seconds))


@pytest.fixture
def auth_app(monkeypatch):
    app = create_app()
    redis = FakeRedis()
    user = UserRow(
        id=uuid4(),
        email="student@quack.kz",
        password_hash=auth.hash_password("correct-password"),
        created_at=datetime.now(UTC),
    )
    lookup = AsyncMock(return_value=user)
    monkeypatch.setattr(auth, "get_user_by_email", lookup)

    async def session_override():
        yield object()

    app.dependency_overrides[deps.get_session] = session_override
    app.dependency_overrides[deps.get_redis] = lambda: redis
    return app, user, redis, lookup


def test_login_success_sets_cookie_and_jwt(auth_app):
    app, user, redis, lookup = auth_app
    assert user.password_hash.startswith("$2b$12$")
    with TestClient(app) as client:
        response = client.post(
            "/auth/login", json={"email": user.email, "password": "correct-password"}
        )

    assert response.status_code == 200
    assert response.json() == {
        "student_id": str(user.id),
        "email": user.email,
        "name": "student",
    }
    cookie = response.headers["set-cookie"]
    assert "quack_token=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie
    assert redis.expirations == [("quack:auth:rl:testclient", 60)]
    assert lookup.await_args.args[1] == user.email

    claims = jwt.decode(
        response.cookies["quack_token"],
        deps.settings.JWT_SECRET.get_secret_value(),
        algorithms=["HS256"],
    )
    assert claims["sub"] == str(user.id)
    assert claims["email"] == user.email
    assert claims["exp"] - claims["iat"] == 30 * 24 * 60 * 60


def test_unknown_email_and_wrong_password_are_identical(auth_app):
    app, user, _, lookup = auth_app
    with TestClient(app) as client:
        wrong = client.post(
            "/auth/login", json={"email": user.email, "password": "wrong"}
        )
        lookup.return_value = None
        unknown = client.post(
            "/auth/login",
            json={"email": "unknown@quack.kz", "password": "correct-password"},
        )

    assert wrong.status_code == unknown.status_code == 401
    assert (
        wrong.json()
        == unknown.json()
        == {"error": {"code": "unauthorized", "message": "invalid credentials"}}
    )


def test_eleventh_login_is_rate_limited(auth_app):
    app, user, redis, _ = auth_app
    with TestClient(app) as client:
        responses = [
            client.post("/auth/login", json={"email": user.email, "password": "wrong"})
            for _ in range(11)
        ]

    assert all(response.status_code == 401 for response in responses[:10])
    assert responses[10].status_code == 429
    assert responses[10].json()["error"]["code"] == "too_many_requests"
    assert redis.expirations == [("quack:auth:rl:testclient", 60)]


def test_me_missing_or_expired_cookie_is_unauthorized(auth_app):
    app, user, _, _ = auth_app
    expired = jwt.encode(
        {
            "sub": str(user.id),
            "email": user.email,
            "iat": datetime.now(UTC) - timedelta(days=2),
            "exp": datetime.now(UTC) - timedelta(days=1),
        },
        deps.settings.JWT_SECRET.get_secret_value(),
        algorithm="HS256",
    )
    with TestClient(app) as client:
        missing = client.get("/auth/me")
        client.cookies.set("quack_token", expired)
        old = client.get("/auth/me")

    assert missing.status_code == old.status_code == 401
    assert missing.json()["error"]["code"] == "unauthorized"
    assert old.json()["error"]["code"] == "unauthorized"


def test_me_rejects_malformed_and_invalid_claims(auth_app):
    app, user, _, _ = auth_app
    now = datetime.now(UTC)
    bad_subject = jwt.encode(
        {
            "sub": "not-a-uuid",
            "email": user.email,
            "iat": now,
            "exp": now + timedelta(days=1),
        },
        deps.settings.JWT_SECRET.get_secret_value(),
        algorithm="HS256",
    )
    no_expiration = jwt.encode(
        {"sub": str(user.id), "email": user.email, "iat": now},
        deps.settings.JWT_SECRET.get_secret_value(),
        algorithm="HS256",
    )
    with TestClient(app) as client:
        for token in ("malformed", bad_subject, no_expiration):
            client.cookies.set("quack_token", token)
            response = client.get("/auth/me")
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "unauthorized"


def test_logout_clears_cookie_and_me_becomes_unauthorized(auth_app):
    app, user, _, _ = auth_app
    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"email": user.email, "password": "correct-password"}
        )
        before = client.get("/auth/me")
        logout = client.post("/auth/logout")
        after = client.get("/auth/me")

    assert login.status_code == 200
    assert before.json() == {
        "student_id": str(user.id),
        "email": user.email,
        "name": "student",
    }
    assert logout.status_code == 204
    assert "Max-Age=0" in logout.headers["set-cookie"]
    assert after.status_code == 401


def test_prod_cookie_is_secure(auth_app, monkeypatch):
    app, user, _, _ = auth_app
    monkeypatch.setattr(auth, "settings", Settings(ENV="prod", JWT_SECRET="x" * 32))
    with TestClient(app) as client:
        response = client.post(
            "/auth/login", json={"email": user.email, "password": "correct-password"}
        )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_student_identity_only_comes_from_token(auth_app):
    app, user, _, _ = auth_app
    other_id = uuid4()
    token = auth.issue_token(user.id, user.email)
    with TestClient(app) as client:
        client.cookies.set("quack_token", token)
        response = client.get("/auth/me", params={"student_id": str(other_id)})

    assert response.status_code == 200
    assert response.json()["student_id"] == str(user.id)


def test_me_invokes_b1_interface_when_available(auth_app, monkeypatch):
    app, user, _, _ = auth_app
    graph = object()
    ensure_student = AsyncMock()
    monkeypatch.setattr(auth, "_ensure_student", ensure_student)
    with TestClient(app) as client:
        app.state.neo4j = graph
        client.cookies.set("quack_token", auth.issue_token(user.id, user.email))
        response = client.get("/auth/me")

    assert response.status_code == 200
    ensure_student.assert_awaited_once_with(graph, user.id)


def test_me_still_answers_when_graph_is_unavailable(auth_app, monkeypatch):
    app, user, _, _ = auth_app
    ensure_student = AsyncMock()
    monkeypatch.setattr(auth, "_ensure_student", ensure_student)
    with TestClient(app) as client:
        app.state.neo4j = None  # graph.client.create_driver soft-failed
        client.cookies.set("quack_token", auth.issue_token(user.id, user.email))
        response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "student_id": str(user.id),
        "email": user.email,
        "name": "student",
    }
    ensure_student.assert_not_awaited()


def test_ip_resolution_ignores_proxy_header_locally_and_uses_it_in_prod(monkeypatch):
    request = SimpleNamespace(
        headers={"X-Forwarded-For": "203.0.113.1, 198.51.100.2"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    assert deps.client_ip(request) == "127.0.0.1"
    monkeypatch.setattr(deps, "settings", Settings(ENV="prod", JWT_SECRET="x" * 32))
    assert deps.client_ip(request) == "203.0.113.1"
    request.headers = {}
    assert deps.client_ip(request) == "127.0.0.1"


@pytest.mark.parametrize("failed", [False, True])
def test_session_dependency_commits_or_rolls_back(failed):
    class FakeSession:
        def __init__(self) -> None:
            self.commit = AsyncMock()
            self.rollback = AsyncMock()
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            self.closed = True

    session = FakeSession()
    app = create_app()

    @app.get("/session-check")
    async def check(_session: Annotated[object, Depends(deps.get_session)]):
        if failed:
            raise NotFound("not found")
        return {"ok": True}

    with TestClient(app) as client:
        app.state.sessionmaker = lambda: session
        response = client.get("/session-check")

    assert response.status_code == (404 if failed else 200)
    assert session.commit.await_count == (0 if failed else 1)
    assert session.rollback.await_count == (1 if failed else 0)
    assert session.closed


def test_register_creates_user_sets_cookie_and_returns_name(auth_app, monkeypatch):
    app, _, redis, _ = auth_app
    new_id = uuid4()
    create = AsyncMock(return_value=new_id)
    monkeypatch.setattr(auth, "create_user", create)
    with TestClient(app) as client:
        response = client.post(
            "/auth/register",
            json={
                "name": " Аня ",
                "email": "Anya@Quack.kz",
                "password": "long-enough-1",
            },
        )
        me = client.get("/auth/me")

    assert response.status_code == 201
    assert response.json() == {
        "student_id": str(new_id),
        "email": "anya@quack.kz",
        "name": "Аня",
    }
    assert "quack_token=" in response.headers["set-cookie"]
    assert create.await_args.args[1] == "anya@quack.kz"
    assert create.await_args.args[2] != "long-enough-1"
    assert me.json()["name"] == "Аня"


def test_register_duplicate_email_is_conflict(auth_app, monkeypatch):
    app, *_ = auth_app
    monkeypatch.setattr(auth, "create_user", AsyncMock(return_value=None))
    with TestClient(app) as client:
        response = client.post(
            "/auth/register",
            json={"name": "A", "email": "a@quack.kz", "password": "long-enough-1"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_register_rejects_short_password(auth_app):
    app, *_ = auth_app
    with TestClient(app) as client:
        response = client.post(
            "/auth/register",
            json={"name": "A", "email": "a@quack.kz", "password": "short"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"


def test_cors_preflight_allows_frontend_origin_with_credentials(auth_app):
    app, *_ = auth_app
    with TestClient(app) as client:
        response = client.options(
            "/auth/login",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        other = client.get("/health", headers={"Origin": "http://evil.example"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in other.headers


def test_me_answers_when_neo4j_goes_down(auth_app, monkeypatch):
    """Личность не зависит от графа (product-logic §6.3).

    Драйвер есть — значит Neo4j был жив на старте; упасть он может позже, и
    тогда MERGE бросает. Без обработки на этом падал весь вход, а браузер не
    мог даже прочитать причину: у необработанной ошибки нет CORS-заголовков.
    """
    from neo4j.exceptions import ServiceUnavailable

    app, user, _, _ = auth_app

    async def down(_driver, _student_id):
        raise ServiceUnavailable("neo4j is down")

    monkeypatch.setattr(auth, "_ensure_student", down)
    app.dependency_overrides[deps.get_graph] = lambda: object()

    with TestClient(app) as client:
        client.post(
            "/auth/login", json={"email": user.email, "password": "correct-password"}
        )
        response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["student_id"] == str(user.id)
