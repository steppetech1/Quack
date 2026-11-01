"""`EmailSender`: login-link mail over the Resend HTTP API (Т-§19.1, Д-§7, ТЗ Фазы 4 WP18.1).

Seam S1. Only `api` sends mail (key `EMAIL_API_KEY`). The test double is
`tests.support.email.FakeEmailSender`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from quack.config import Settings


class EmailUnavailable(Exception):
    """Resend does not answer: requesting a link is `SERVICE_UNAVAILABLE` (ТЗ §4.4)."""


@runtime_checkable
class EmailSender(Protocol):
    async def send_login_link(self, *, to: str, link: str) -> None: ...

    async def aclose(self) -> None: ...


class ResendEmailSender:
    """`EmailSender` on the Resend HTTP API from `auth_email_from` (WP18.1)."""

    def __init__(self, *, api_key: str, sender: str) -> None:
        self._api_key = api_key
        self._sender = sender

    async def send_login_link(self, *, to: str, link: str) -> None:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


def create_email_sender(settings: Settings) -> EmailSender:
    """`EmailSender` on Resend (Т-§19.1); only for `api`."""
    return ResendEmailSender(
        api_key=settings.email_api_key.get_secret_value(), sender=settings.auth_email_from
    )
