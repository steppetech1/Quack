"""`EmailSender`: login-link mail over the Resend HTTP API (Т-§19.1, Д-§7, ТЗ Фазы 4 WP18.1).

Seam S1. Only `api` sends mail (key `EMAIL_API_KEY`). The test double is
`tests.support.email.FakeEmailSender`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from quack.config import Settings
from quack.logging import get_logger

log = get_logger(__name__)

RESEND_EMAILS_URL = "https://api.resend.com/emails"

LOGIN_SUBJECT = "Your Quack! sign-in link"
LOGIN_TEXT = (
    "Open this link to sign in to Quack!:\n\n{link}\n\n"
    "The link works once and expires in {ttl_min} minutes. "
    "If you did not ask for it, ignore this email."
)


class EmailUnavailable(Exception):
    """Resend does not answer: requesting a link is `SERVICE_UNAVAILABLE` (ТЗ §4.4)."""


@runtime_checkable
class EmailSender(Protocol):
    async def send_login_link(self, *, to: str, link: str) -> None: ...

    async def aclose(self) -> None: ...


class ResendEmailSender:
    """`EmailSender` on the Resend HTTP API from `auth_email_from` (WP18.1).

    Any answer that is not 2xx, and any failure of the connection, is `EmailUnavailable`: the
    mail did not go. Neither the link nor the key reach the error or the log (the link is a
    credential). `client` lets tests pass a `MockTransport`."""

    def __init__(
        self,
        *,
        api_key: str,
        sender: str,
        link_ttl_min: int,
        timeout_s: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._sender = sender
        self._link_ttl_min = link_ttl_min
        self._timeout_s = timeout_s
        self._client = client or httpx.AsyncClient()

    async def send_login_link(self, *, to: str, link: str) -> None:
        body = {
            "from": self._sender,
            "to": [to],
            "subject": LOGIN_SUBJECT,
            "text": LOGIN_TEXT.format(link=link, ttl_min=self._link_ttl_min),
        }
        try:
            response = await self._client.post(
                RESEND_EMAILS_URL,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout_s,
            )
        except httpx.HTTPError as exc:
            log.warning("resend_unreachable", error=type(exc).__name__)
            raise EmailUnavailable(f"resend request failed: {type(exc).__name__}") from exc
        if not response.is_success:
            log.warning("resend_refused", status=response.status_code)
            raise EmailUnavailable(f"resend returned HTTP {response.status_code}")

    async def aclose(self) -> None:
        await self._client.aclose()


def create_email_sender(settings: Settings) -> EmailSender:
    """`EmailSender` on Resend (Т-§19.1); only for `api`."""
    return ResendEmailSender(
        api_key=settings.email_api_key.get_secret_value(),
        sender=settings.auth_email_from,
        link_ttl_min=settings.auth_link_ttl_min,
        timeout_s=settings.auth_http_timeout_s,
    )
