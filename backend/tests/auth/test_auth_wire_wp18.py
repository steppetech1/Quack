"""WP18.1 and WP18.2 on the real adapters, end to end over HTTP (ТЗ Ф4 §8.11 `T-AU1`–`T-AU3`;
Т-§19.1). `test_auth_routes.py` runs the routes on `FakeEmailSender` and `FakeOIDC`, which cannot
fail the way Resend and Google fail; here the app is built with `ResendEmailSender` and
`AuthlibGoogleOIDC` over replaced transports (no network, no real secret): the mail is a request of
the right form to Resend, the link in it signs in, a refusal of Resend is `503`, the `state` of the
Google flow is checked before Google is asked anything, and a bad id token signs nobody in."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import pytest
from joserfc.jwk import RSAKey
from pydantic import SecretStr

from quack.api.app import create_app
from quack.api.auth import SESSION_COOKIE
from quack.api.email import RESEND_EMAILS_URL, ResendEmailSender
from quack.auth.google import AuthlibGoogleOIDC
from quack.auth.router import OIDC_STATE_COOKIE
from quack.config import Settings
from quack.db import Db
from tests.support.google_idp import CLIENT_ID, CLIENT_SECRET, Google
from tests.support.resources import build_app_resources

BASE = "https://api.qvack.online"
REDIRECT = "https://app.qvack.online/"
CALLBACK = f"{BASE}/auth/google/callback"
RESEND_KEY = "re_test_key"


class Resend:
    """A scripted Resend: every request is kept; `status` or `error` make it refuse or fail."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.error: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return httpx.Response(self.status, json={"id": "mail-1"})

    def link_of(self, n: int = -1) -> str:
        """The login link in the text of the n-th mail."""
        text: str = httpx.Response(200, content=self.requests[n].content).json()["text"]
        found = re.search(r"https://\S+", text)
        assert found is not None
        return found.group(0)


@dataclass
class Stack:
    client: httpx.AsyncClient
    resend: Resend
    google: Google
    db: Db


@asynccontextmanager
async def _stack(pg_db: Db, settings: Settings, **override: object) -> AsyncIterator[Stack]:
    cfg = settings.model_copy(
        update={
            "auth_success_redirect_url": REDIRECT,
            "google_oidc_client_id": SecretStr(CLIENT_ID),
            "google_oidc_client_secret": SecretStr(CLIENT_SECRET),
            "google_oidc_redirect_url": CALLBACK,
            **override,
        }
    )
    resend, google = Resend(), Google()
    email = ResendEmailSender(
        api_key=RESEND_KEY,
        sender=cfg.auth_email_from,
        link_ttl_min=cfg.auth_link_ttl_min,
        timeout_s=cfg.auth_http_timeout_s,
        client=httpx.AsyncClient(transport=httpx.MockTransport(resend.handler)),
    )
    oidc = AuthlibGoogleOIDC(cfg, transport=httpx2.MockTransport(google.handler))
    resources = build_app_resources(cfg, None, None, db=pg_db, email=email)
    app = create_app(cfg, resources=resources, google_oidc=oidc)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        yield Stack(client, resend, google, pg_db)
    await email.aclose()


async def _learners(db: Db, email: str) -> int:
    rows = await db.fetch_all(
        "SELECT learner_id FROM core.learners WHERE email = %(email)s", {"email": email}
    )
    return len(rows)


# --- T-AU1: the link by mail through Resend -------------------------------------------------


async def test_link_goes_to_resend_and_signs_in(pg_db: Db, settings: Settings) -> None:
    """The request for a link is one request of the right form to Resend; the link in it signs
    in once; the database keeps the hash of the token, not the token."""
    async with _stack(pg_db, settings) as s:
        sent = await s.client.post("/auth/link", json={"email": "Ann@Example.com"})
        assert sent.status_code == 200 and sent.json() == {"status": "sent"}

        (request,) = s.resend.requests
        assert request.method == "POST" and str(request.url) == RESEND_EMAILS_URL
        assert request.headers["authorization"] == f"Bearer {RESEND_KEY}"
        body = httpx.Response(200, content=request.content).json()
        assert body["to"] == ["ann@example.com"] and body["from"] == settings.auth_email_from
        link = s.resend.link_of()
        parts = urlsplit(link)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{BASE}/auth/link/verify"
        token = parse_qs(parts.query)["token"][0]
        assert token not in sent.text  # the answer does not carry the credential

        stored = await pg_db.fetch_all("SELECT token_hash FROM core.auth_links")
        assert [row["token_hash"] for row in stored] != [token] and len(stored) == 1

        verified = await s.client.get("/auth/link/verify", params={"token": token})
        assert verified.status_code == 303 and verified.headers["location"] == REDIRECT
        assert (await s.client.get("/v1/me")).json()["email"] == "ann@example.com"

        s.client.cookies.clear()
        replay = await s.client.get("/auth/link/verify", params={"token": token})
        assert replay.status_code == 401 and SESSION_COOKIE not in replay.headers.get(
            "set-cookie", ""
        )


@pytest.mark.parametrize("refusal", [401, 422, 429, 500], ids=lambda c: f"http-{c}")
async def test_resend_refusal_is_503_and_leaks_nothing(
    pg_db: Db, settings: Settings, refusal: int
) -> None:
    """Resend refuses (a bad key, an unverified domain, a quota, an outage): the learner gets
    `SERVICE_UNAVAILABLE`; neither the key nor the link is in the answer."""
    async with _stack(pg_db, settings) as s:
        s.resend.status = refusal
        down = await s.client.post("/auth/link", json={"email": "d@example.com"})
        assert down.status_code == 503 and down.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert RESEND_KEY not in down.text and "token=" not in down.text


async def test_resend_unreachable_is_503(pg_db: Db, settings: Settings) -> None:
    async with _stack(pg_db, settings) as s:
        s.resend.error = httpx.ConnectError("no route")
        down = await s.client.post("/auth/link", json={"email": "d@example.com"})
        assert down.status_code == 503 and down.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


# --- T-AU2: limits stop the request before Resend -------------------------------------------


async def test_email_limit_stops_before_resend(pg_db: Db, settings: Settings) -> None:
    """Over the limit of one address (any letter case) — 429 with `Retry-After`; no mail goes
    and no token is stored for the refused request."""
    limit = settings.auth_link_per_email_per_hour
    async with _stack(pg_db, settings) as s:
        for n in range(limit):
            email = "r@example.com" if n % 2 else "R@Example.com"
            assert (await s.client.post("/auth/link", json={"email": email})).status_code == 200
        assert len(s.resend.requests) == limit

        limited = await s.client.post("/auth/link", json={"email": "r@EXAMPLE.com"})
        assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        assert len(s.resend.requests) == limit
        assert len(await pg_db.fetch_all("SELECT 1 FROM core.auth_links")) == limit

        other = await s.client.post("/auth/link", json={"email": "other@example.com"})
        assert other.status_code == 200  # another address is not blocked by this one


async def test_ip_limit_stops_before_resend(pg_db: Db, settings: Settings) -> None:
    """Many addresses from one IP: over `auth_link_per_ip_per_hour` — 429 and no mail."""
    async with _stack(pg_db, settings, auth_link_per_ip_per_hour=3) as s:
        for n in range(3):
            sent = await s.client.post("/auth/link", json={"email": f"u{n}@example.com"})
            assert sent.status_code == 200
        limited = await s.client.post("/auth/link", json={"email": "u9@example.com"})
        assert limited.status_code == 429 and len(s.resend.requests) == 3


# --- T-AU3: Google through the real adapter -------------------------------------------------


async def _start(s: Stack) -> str:
    start = await s.client.get("/auth/google/start")
    assert start.status_code == 302
    return parse_qs(urlsplit(start.headers["location"]).query)["state"][0]


async def test_google_start_binds_state_to_the_browser(pg_db: Db, settings: Settings) -> None:
    async with _stack(pg_db, settings) as s:
        start = await s.client.get("/auth/google/start")
        location = urlsplit(start.headers["location"])
        query = parse_qs(location.query)
        assert f"{location.scheme}://{location.netloc}{location.path}" == (
            "https://accounts.google.com/o/oauth2/v2/auth"
        )
        assert query["client_id"] == [CLIENT_ID] and query["redirect_uri"] == [CALLBACK]
        assert query["response_type"] == ["code"]
        cookie = start.headers["set-cookie"]
        assert cookie.startswith(f"{OIDC_STATE_COOKIE}={query['state'][0]};")
        assert "httponly" in cookie.lower() and "secure" in cookie.lower()
        assert CLIENT_SECRET not in start.headers["location"]
        # A second start is a new state: a state is not reusable across flows.
        assert await _start(s) != query["state"][0]
        assert s.google.requests == []


async def test_google_callback_exchanges_the_code_and_signs_in(
    pg_db: Db, settings: Settings
) -> None:
    async with _stack(pg_db, settings) as s:
        s.google.code_claims["good"] = s.google.claims()
        state = await _start(s)
        done = await s.client.get("/auth/google/callback", params={"code": "good", "state": state})
        assert done.status_code == 303 and done.headers["location"] == REDIRECT

        (token_request,) = s.google.token_requests
        form = parse_qs(token_request.content.decode())
        assert form["code"] == ["good"] and form["grant_type"] == ["authorization_code"]
        assert form["redirect_uri"] == [CALLBACK]
        assert (await s.client.get("/v1/me")).json()["email"] == "gina@example.com"
        assert OIDC_STATE_COOKIE not in {c.name for c in s.client.cookies.jar}

        # The state is spent with its cookie: the same callback again signs nobody in.
        s.client.cookies.delete(SESSION_COOKIE, domain=settings.session_cookie_domain)
        again = await s.client.get("/auth/google/callback", params={"code": "good", "state": state})
        assert again.status_code == 401 and len(s.google.token_requests) == 1


@pytest.mark.parametrize("started", [True, False], ids=["wrong-state", "no-start-cookie"])
async def test_google_callback_with_a_foreign_state_never_reaches_google(
    pg_db: Db, settings: Settings, started: bool
) -> None:
    """A callback whose `state` is not the one of this browser's `start` (a login-CSRF: the
    attacker's own code and state) is refused before Google is asked anything."""
    async with _stack(pg_db, settings) as s:
        s.google.code_claims["attacker"] = s.google.claims(email="eve@example.com")
        if started:
            await _start(s)
        for state in ("someone-elses", ""):
            refused = await s.client.get(
                "/auth/google/callback", params={"code": "attacker", "state": state}
            )
            assert refused.status_code == 401
            assert refused.json()["error"]["code"] == "UNAUTHENTICATED"
            assert SESSION_COOKIE not in refused.headers.get("set-cookie", "")
        assert s.google.requests == []
        assert await _learners(pg_db, "eve@example.com") == 0


async def _stranger_token(s: Stack) -> str:
    kid = s.google.key.kid
    assert kid is not None
    stranger = RSAKey.generate_key(2048, parameters={"kid": kid})
    return s.google.id_token(s.google.claims(), key=stranger)


@pytest.mark.parametrize(
    "case", ["unverified", "wrong-audience", "wrong-issuer", "expired", "rejected-code", "forged"]
)
async def test_google_callback_refuses_a_bad_identity(
    pg_db: Db, settings: Settings, case: str
) -> None:
    """Google's refusal of the code, a bad id token or an unverified address: 401, no session,
    and no learner is created."""
    async with _stack(pg_db, settings) as s:
        match case:
            case "unverified":
                s.google.code_claims["c"] = s.google.claims(email_verified=False)
            case "wrong-audience":
                s.google.code_claims["c"] = s.google.claims(aud="another-client")
            case "wrong-issuer":
                s.google.code_claims["c"] = s.google.claims(iss="https://evil.example")
            case "expired":
                s.google.code_claims["c"] = s.google.claims(exp=1)
            case "rejected-code":
                s.google.code_claims["c"] = s.google.claims()
                s.google.token_status = 400
            case "forged":
                s.google.code_claims["c"] = s.google.claims()
                s.google.forced_id_token = await _stranger_token(s)
        state = await _start(s)
        refused = await s.client.get("/auth/google/callback", params={"code": "c", "state": state})
        assert refused.status_code == 401 and refused.json()["error"]["code"] == "UNAUTHENTICATED"
        assert SESSION_COOKIE not in refused.headers.get("set-cookie", "")
        assert await _learners(pg_db, "gina@example.com") == 0
        assert (await s.client.get("/v1/me")).status_code == 401
