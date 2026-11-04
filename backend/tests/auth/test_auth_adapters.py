"""WP18.1 and WP18.2: the real adapters behind `EmailSender` (Resend HTTP API) and `GoogleOIDC`
(authlib), against a replaced HTTP transport (ТЗ Ф4 §8.1; Т-§19.1). `T-AU1`–`T-AU3` run on
`FakeEmailSender` and `FakeOIDC`; these tests are the ones that run the classes of production."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from pydantic import SecretStr

from quack.api.email import EmailUnavailable, ResendEmailSender, create_email_sender
from quack.auth.google import AuthlibGoogleOIDC, GoogleIdentity, OidcFailed
from quack.config import Settings

LINK = "https://api.qvack.online/auth/link/verify?token=abc"
CLIENT_ID = "client-id.apps.googleusercontent.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUER = "https://accounts.google.com"

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- WP18.1: Resend -------------------------------------------------------------------------


def _sender(handler: Handler) -> ResendEmailSender:
    return ResendEmailSender(
        api_key="re_test_key",
        sender="login@qvack.online",
        link_ttl_min=15,
        client=_client(handler),
    )


async def test_resend_posts_the_link_to_the_address() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "mail-1"})

    await _sender(handler).send_login_link(to="gina@example.com", link=LINK)

    (request,) = seen
    assert request.method == "POST" and str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == "Bearer re_test_key"
    body = json.loads(request.content)
    assert body["from"] == "login@qvack.online"
    assert body["to"] == ["gina@example.com"]
    assert body["subject"]
    assert LINK in body["text"]
    assert "15" in body["text"]  # the lifetime of the link, `auth_link_ttl_min`


@pytest.mark.parametrize("status", [401, 403, 422, 429, 500, 503])
async def test_resend_refusal_is_email_unavailable(status: int) -> None:
    """An answer that is not 2xx — a bad key, an unverified domain, a quota, an outage — means
    the mail did not go: `SERVICE_UNAVAILABLE` for the request of the link (ТЗ §4.4)."""
    sender = _sender(lambda request: httpx.Response(status, json={"message": "no"}))
    with pytest.raises(EmailUnavailable):
        await sender.send_login_link(to="gina@example.com", link=LINK)


async def test_resend_network_failure_is_email_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(EmailUnavailable):
        await _sender(handler).send_login_link(to="gina@example.com", link=LINK)


async def test_the_log_of_a_refusal_does_not_carry_the_link() -> None:
    """The link is a credential: the error of the sender never repeats it."""
    sender = _sender(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(EmailUnavailable) as raised:
        await sender.send_login_link(to="gina@example.com", link=LINK)
    assert "token=abc" not in str(raised.value)


def test_factory_builds_resend_from_settings(settings: Settings) -> None:
    configured = settings.model_copy(update={"auth_link_ttl_min": 20})
    sender = create_email_sender(configured)
    assert isinstance(sender, ResendEmailSender)


# --- WP18.2: Google -------------------------------------------------------------------------


class Google:
    """A scripted Google: its signing key, the token endpoint and the certificates."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, auto_kid=True)
        self.code_claims: dict[str, dict[str, object]] = {}
        self.token_status = 200
        self.requests: list[httpx.Request] = []

    def claims(self, **override: object) -> dict[str, object]:
        base: dict[str, object] = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "1234567890",
            "iat": int(time.time()) - 5,
            "exp": int(time.time()) + 300,
            "email": "Gina@Example.com",
            "email_verified": True,
        }
        return {**base, **override}

    def id_token(self, claims: dict[str, object], key: RSAKey | None = None) -> str:
        signer = key or self.key
        return jwt.encode({"alg": "RS256", "kid": signer.kid}, claims, signer)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == CERTS_URL:
            return httpx.Response(200, json=KeySet([self.key]).as_dict(private=False))
        if str(request.url) == TOKEN_URL:
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            form = parse_qs(request.content.decode())
            claims = self.code_claims[form["code"][0]]
            return httpx.Response(
                200,
                json={
                    "access_token": "ya29.token",
                    "token_type": "Bearer",
                    "expires_in": 3599,
                    "id_token": self.id_token(claims),
                },
            )
        return httpx.Response(404)


@pytest.fixture
def google() -> Google:
    return Google()


@pytest.fixture
def oidc_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "google_oidc_client_id": SecretStr(CLIENT_ID),
            "google_oidc_client_secret": SecretStr("client-secret"),
            "google_oidc_redirect_url": "https://api.qvack.online/auth/google/callback",
        }
    )


@pytest.fixture
def oidc(google: Google, oidc_settings: Settings) -> AuthlibGoogleOIDC:
    return AuthlibGoogleOIDC(oidc_settings, transport=httpx.MockTransport(google.handler))


def test_authorize_url_asks_google_for_a_code_with_the_state(oidc: AuthlibGoogleOIDC) -> None:
    url = urlsplit(oidc.authorize_url(state="state-1"))
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    query = parse_qs(url.query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == ["https://api.qvack.online/auth/google/callback"]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["state-1"]
    assert set(query["scope"][0].split()) == {"openid", "email"}


async def test_identity_of_a_verified_address(google: Google, oidc: AuthlibGoogleOIDC) -> None:
    google.code_claims["good"] = google.claims()
    identity = await oidc.identity(code="good")
    assert identity == GoogleIdentity(email="Gina@Example.com", email_verified=True)
    token_request = next(r for r in google.requests if str(r.url) == TOKEN_URL)
    form = parse_qs(token_request.content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["redirect_uri"] == ["https://api.qvack.online/auth/google/callback"]


async def test_identity_keeps_the_unverified_flag(google: Google, oidc: AuthlibGoogleOIDC) -> None:
    """The refusal of an unverified address is `learner_for_google`'s (T-AU3), not the adapter's."""
    google.code_claims["bad"] = google.claims(email_verified=False)
    assert (await oidc.identity(code="bad")).email_verified is False


async def test_identity_reads_google_style_string_flag(
    google: Google, oidc: AuthlibGoogleOIDC
) -> None:
    google.code_claims["s"] = google.claims(email_verified="true")
    assert (await oidc.identity(code="s")).email_verified is True


@pytest.mark.parametrize(
    "override",
    [
        {"aud": "another-client"},
        {"iss": "https://evil.example"},
        {"exp": 1},
        {"email": None},
    ],
    ids=["wrong-audience", "wrong-issuer", "expired", "no-email"],
)
async def test_identity_refuses_a_bad_token(
    google: Google, oidc: AuthlibGoogleOIDC, override: dict[str, object]
) -> None:
    google.code_claims["x"] = google.claims(**override)
    with pytest.raises(OidcFailed):
        await oidc.identity(code="x")


async def test_identity_refuses_a_foreign_signature(
    google: Google, oidc: AuthlibGoogleOIDC
) -> None:
    stranger = RSAKey.generate_key(2048, kid=google.key.kid)
    token = google.id_token(google.claims(), key=stranger)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "t", "id_token": token})
        return google.handler(request)

    forged = AuthlibGoogleOIDC(
        oidc._settings,  # pyright: ignore[reportPrivateUsage]
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OidcFailed):
        await forged.identity(code="x")


async def test_identity_refuses_a_code_google_rejects(
    google: Google, oidc: AuthlibGoogleOIDC
) -> None:
    google.token_status = 400
    with pytest.raises(OidcFailed):
        await oidc.identity(code="used-twice")


async def test_identity_when_google_is_unreachable(oidc_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    down = AuthlibGoogleOIDC(oidc_settings, transport=httpx.MockTransport(handler))
    with pytest.raises(OidcFailed):
        await down.identity(code="x")
