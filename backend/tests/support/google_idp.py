"""A scripted Google for `AuthlibGoogleOIDC` (ТЗ Ф4 §8.1): the token endpoint and the
certificates over `httpx2.MockTransport`, ID tokens signed by its own RSA key. No real network,
no real client secret."""

from __future__ import annotations

import time
from urllib.parse import parse_qs

import httpx2
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

CLIENT_ID = "client-id.apps.googleusercontent.com"
CLIENT_SECRET = "client-secret"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUER = "https://accounts.google.com"


class Google:
    """A scripted Google: its signing key, the token endpoint and the certificates.
    `code_claims[code]` are the claims of the ID token given for `code`; an unknown code is
    refused with `invalid_grant`, as Google does for a used or foreign code."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, auto_kid=True)
        self.code_claims: dict[str, dict[str, object]] = {}
        self.token_status = 200
        self.forced_id_token: str | None = None  # given instead of the signed one (forgeries)
        self.requests: list[httpx2.Request] = []

    @property
    def token_requests(self) -> list[httpx2.Request]:
        return [r for r in self.requests if str(r.url) == TOKEN_URL]

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

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if str(request.url) == CERTS_URL:
            return httpx2.Response(200, json=KeySet([self.key]).as_dict(private=False))
        if str(request.url) == TOKEN_URL:
            form = parse_qs(request.content.decode())
            claims = self.code_claims.get(form.get("code", [""])[0])
            if self.token_status != 200 or claims is None:
                return httpx2.Response(self.token_status or 400, json={"error": "invalid_grant"})
            return httpx2.Response(
                200,
                json={
                    "access_token": "ya29.token",
                    "token_type": "Bearer",
                    "expires_in": 3599,
                    "id_token": self.forced_id_token or self.id_token(claims),
                },
            )
        return httpx2.Response(404)
