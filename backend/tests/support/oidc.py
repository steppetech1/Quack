"""`FakeOIDC` (ТЗ Фазы 4 §8.1): the answer of Google replaced by a scripted identity."""

from __future__ import annotations

from quack.auth.google import GoogleIdentity, OidcFailed


class FakeOIDC:
    """`GoogleOIDC` that redirects to a fake URL and maps codes to scripted identities."""

    def __init__(self) -> None:
        self.identities: dict[str, GoogleIdentity] = {}
        self.exchanges: list[tuple[str, str, str]] = []  # (code, nonce, code_verifier)

    def script(self, code: str, *, email: str, email_verified: bool = True) -> None:
        self.identities[code] = GoogleIdentity(email=email, email_verified=email_verified)

    def authorize_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        return (
            "https://accounts.google.test/authorize"
            f"?state={state}&nonce={nonce}&code_challenge={code_challenge}"
            "&code_challenge_method=S256"
        )

    async def identity(self, *, code: str, nonce: str, code_verifier: str) -> GoogleIdentity:
        self.exchanges.append((code, nonce, code_verifier))
        if code not in self.identities:
            raise OidcFailed("Google refused the code")
        return self.identities[code]
