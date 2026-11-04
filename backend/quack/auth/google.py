"""Google OIDC through `authlib` (Т-§19.1, ТЗ Ф4 WP18.2).

The learner is found by the verified mail address of the id token; an unverified address is
refused. Secrets `GOOGLE_OIDC_CLIENT_ID`, `GOOGLE_OIDC_CLIENT_SECRET` live only in `api`.
The test double is `tests.support.oidc.FakeOIDC` (§8.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx2
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oauth2.rfc6749.parameters import prepare_grant_uri
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from quack.auth.links import learner_for_email
from quack.config import Settings
from quack.db import Db

# The public endpoints of Google's OpenID Connect (its discovery document).
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"]
SCOPE = "openid email"
ID_TOKEN_ALGORITHMS = ["RS256"]


class EmailNotVerified(Exception):
    """The id token carries an address Google has not verified."""


class OidcFailed(Exception):
    """Google refused the code, did not answer, or its id token is not valid (signature,
    issuer, audience, expiry, no address): the sign-in is refused."""


@dataclass(frozen=True)
class GoogleIdentity:
    email: str
    email_verified: bool


class GoogleOIDC(Protocol):
    def authorize_url(self, *, state: str) -> str:
        """Where `GET /auth/google/start` redirects."""
        ...

    async def identity(self, *, code: str) -> GoogleIdentity:
        """Exchange the code of `GET /auth/google/callback` for the id token's identity.
        `OidcFailed` when Google refuses or the id token is not valid."""
        ...


class AuthlibGoogleOIDC:
    """`GoogleOIDC` on `authlib` with redirect URI `google_oidc_redirect_url` (Д-§3.1).

    The code is exchanged at Google's token endpoint; the id token is then checked against
    Google's published keys (RS256 signature, issuer, audience, expiry). `transport` lets tests
    replace the network."""

    def __init__(
        self, settings: Settings, *, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._transport = transport

    def authorize_url(self, *, state: str) -> str:
        return prepare_grant_uri(
            GOOGLE_AUTHORIZE_URL,
            self._settings.google_oidc_client_id.get_secret_value(),
            "code",
            redirect_uri=self._settings.google_oidc_redirect_url,
            scope=SCOPE,
            state=state,
        )

    async def identity(self, *, code: str) -> GoogleIdentity:
        client_id = self._settings.google_oidc_client_id.get_secret_value()
        timeout = self._settings.auth_http_timeout_s
        try:
            oauth = AsyncOAuth2Client(
                client_id=client_id,
                client_secret=self._settings.google_oidc_client_secret.get_secret_value(),
                scope=SCOPE,
                redirect_uri=self._settings.google_oidc_redirect_url,
                transport=self._transport,
                timeout=timeout,
            )
            try:
                token: dict[str, Any] = await oauth.fetch_token(
                    GOOGLE_TOKEN_URL, code=code, grant_type="authorization_code"
                )
            finally:
                await oauth.aclose()  # pyright: ignore[reportAttributeAccessIssue]
            async with httpx2.AsyncClient(transport=self._transport, timeout=timeout) as http:
                certs = await http.get(GOOGLE_CERTS_URL)
                certs.raise_for_status()
                keys = KeySet.import_key_set(certs.json())
            id_token = token.get("id_token")
            if not isinstance(id_token, str):
                raise OidcFailed("Google answered without an id token")
            claims = jwt.decode(id_token, keys, algorithms=ID_TOKEN_ALGORITHMS).claims
            JWTClaimsRegistry(
                iss={"essential": True, "values": GOOGLE_ISSUERS},
                aud={"essential": True, "value": client_id},
                exp={"essential": True},
            ).validate(claims)
        except OidcFailed:
            raise
        except (OAuthError, httpx2.HTTPError, JoseError, ValueError, KeyError) as exc:
            raise OidcFailed(f"sign-in with Google failed: {type(exc).__name__}") from exc
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise OidcFailed("the id token of Google carries no address")
        verified = claims.get("email_verified") in (True, "true")
        return GoogleIdentity(email=email, email_verified=verified)


async def learner_for_google(db: Db, identity: GoogleIdentity) -> UUID:
    """The learner of the verified address (a new row for a new one); `EmailNotVerified`."""
    if not identity.email_verified:
        raise EmailNotVerified("Google has not verified this address")
    return await learner_for_email(db, identity.email)
