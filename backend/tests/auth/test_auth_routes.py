"""WP18 routes: link by mail, Google, logout, `/v1/me`, settings, consents (ТЗ Ф4 §8.11
`T-AU1`, `T-AU3`, `T-AU4`, `T-AU6`; Т-§19.1, Т-§19.4, Т-§14.6). The app runs on the Phase 4
database (`pg_db`); cookies go over `https://api.qvack.online` (`Secure`, `Domain`)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends

from quack.api.app import create_app
from quack.api.auth import SESSION_COOKIE, require_terms
from quack.config import Settings
from quack.db import Db
from tests.support.email import FakeEmailSender
from tests.support.oidc import FakeOIDC
from tests.support.resources import build_app_resources

BASE = "https://api.qvack.online"
REDIRECT = "https://app.qvack.online/"


@pytest.fixture
def auth_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"auth_success_redirect_url": REDIRECT, "terms_version": "2026-11"}
    )


@pytest.fixture
async def client(pg_db: Db, auth_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    mail, oidc = FakeEmailSender(), FakeOIDC()
    resources = build_app_resources(auth_settings, None, None, db=pg_db, email=mail)
    app = create_app(auth_settings, resources=resources, google_oidc=oidc)

    @app.get("/test/terms", dependencies=[Depends(require_terms)])
    async def terms_gate() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"ok": "yes"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as test_client:
        test_client.mail = mail  # type: ignore[attr-defined]
        test_client.oidc = oidc  # type: ignore[attr-defined]
        yield test_client


async def _sign_in(client: httpx.AsyncClient, email: str) -> httpx.Response:
    response = await client.post("/auth/link", json={"email": email})
    assert response.status_code == 200 and response.json() == {"status": "sent"}
    link: str = client.mail.last_link(email.lower())  # type: ignore[attr-defined]
    parts = urlsplit(link)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{BASE}/auth/link/verify"
    token = parse_qs(parts.query)["token"][0]
    return await client.get("/auth/link/verify", params={"token": token})


async def test_link_flow_sets_the_cookie_and_logout_ends_it(client: httpx.AsyncClient) -> None:
    """T-AU1, T-AU4 over HTTP: the link signs in, the cookie has the flags of Т-§19.1,
    `GET /v1/me` answers, `logout` makes the next request 401."""
    verified = await _sign_in(client, "Ann@Example.com")
    assert verified.status_code == 303 and verified.headers["location"] == REDIRECT
    cookie = verified.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Domain=qvack.online"):
        assert flag.lower() in cookie.lower(), flag

    me = await client.get("/v1/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "ann@example.com" and body["terms_accepted"] is False
    assert body["client"] == {
        "stroke_batch_ms": 300,
        "line_pause_ms": 1200,
        "capture_still_ms": 700,
    }

    assert (await client.post("/auth/logout")).json() == {"status": "signed_out"}
    after = await client.get("/v1/me")
    assert after.status_code == 401 and after.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_bad_link_and_no_session_are_401(client: httpx.AsyncClient) -> None:
    bad = await client.get("/auth/link/verify", params={"token": "nope"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "UNAUTHENTICATED"
    assert (await client.get("/v1/me")).status_code == 401


async def test_link_limits_and_mail_down(
    client: httpx.AsyncClient, auth_settings: Settings
) -> None:
    """T-AU2 over HTTP: over the limit — 429 with `Retry-After`; mail down — 503 (§4.4)."""
    for _ in range(auth_settings.auth_link_per_email_per_hour):
        sent = await client.post("/auth/link", json={"email": "r@example.com"})
        assert sent.status_code == 200
    limited = await client.post("/auth/link", json={"email": "r@example.com"})
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    client.mail.available = False  # type: ignore[attr-defined]
    down = await client.post("/auth/link", json={"email": "d@example.com"})
    assert down.status_code == 503 and down.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


async def test_google_flow(client: httpx.AsyncClient) -> None:
    """T-AU3 over HTTP: `start` redirects with a state kept in a cookie; the callback with
    that state signs the learner in by the verified address; a wrong state or an unverified
    address is refused."""
    client.oidc.script("good", email="Gina@Example.com")  # type: ignore[attr-defined]
    client.oidc.script("bad", email="eve@example.com", email_verified=False)  # type: ignore[attr-defined]
    start = await client.get("/auth/google/start")
    assert start.status_code == 302
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]

    wrong = await client.get("/auth/google/callback", params={"code": "good", "state": "x"})
    assert wrong.status_code == 401
    unverified = await client.get("/auth/google/callback", params={"code": "bad", "state": state})
    assert unverified.status_code == 401
    refused = await client.get("/auth/google/callback", params={"code": "unknown", "state": state})
    assert refused.status_code == 401  # Google refused the code or its id token is not valid
    done = await client.get("/auth/google/callback", params={"code": "good", "state": state})
    assert done.status_code == 303 and done.headers["location"] == REDIRECT
    assert (await client.get("/v1/me")).json()["email"] == "gina@example.com"


async def test_consents_and_terms_gate(client: httpx.AsyncClient) -> None:
    """T-AU6 over HTTP: without `terms` of the current version — 403 `CONSENT_REQUIRED`;
    after `POST /v1/consents` — allowed; research can be revoked."""
    await _sign_in(client, "c@example.com")
    gated = await client.get("/test/terms")
    assert gated.status_code == 403 and gated.json()["error"]["code"] == "CONSENT_REQUIRED"

    given = await client.post(
        "/v1/consents", json={"kind": "terms", "version": "2026-11", "idempotency_key": "k1"}
    )
    assert given.json() == {"kind": "terms", "version": "2026-11", "given": True}
    assert (await client.get("/test/terms")).status_code == 200
    assert (await client.get("/v1/me")).json()["terms_accepted"] is True

    research = {"kind": "research", "version": "2026-11", "idempotency_key": "k2"}
    assert (await client.post("/v1/consents", json=research)).status_code == 200
    revoked = await client.request(
        "DELETE", "/v1/consents/research", json={"version": "2026-11", "idempotency_key": "k3"}
    )
    assert revoked.json() == {"kind": "research", "version": "2026-11", "given": False}


async def test_learner_settings_timezone(client: httpx.AsyncClient) -> None:
    """Т-§14.6: the browser time zone at sign-in goes to `core.learners.settings`."""
    await _sign_in(client, "t@example.com")
    ok = await client.patch("/v1/learner/settings", json={"timezone": "Asia/Almaty"})
    assert ok.json()["settings"]["timezone"] == "Asia/Almaty"
    assert (await client.get("/v1/me")).json()["settings"]["timezone"] == "Asia/Almaty"
    bad = await client.patch("/v1/learner/settings", json={"timezone": "Mars/Base"})
    assert bad.status_code == 422


async def test_learner_id_in_a_body_is_ignored(client: httpx.AsyncClient, pg_db: Db) -> None:
    """T-AU5 for these routes: the learner comes from the session, a `learner_id` in the body
    changes nothing."""
    await _sign_in(client, "own@example.com")
    stranger = uuid4()
    await client.patch(
        "/v1/learner/settings", json={"timezone": "Europe/Berlin", "learner_id": str(stranger)}
    )
    me = (await client.get("/v1/me")).json()
    assert me["learner_id"] != str(stranger) and me["settings"]["timezone"] == "Europe/Berlin"
