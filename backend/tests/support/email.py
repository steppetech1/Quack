"""`FakeEmailSender` (ТЗ Фазы 4 §8.1, seam S1): a log of sent login links."""

from __future__ import annotations

from dataclasses import dataclass

from quack.api.email import EmailUnavailable


@dataclass(frozen=True)
class SentLink:
    to: str
    link: str


class FakeEmailSender:
    """Keeps every login link in `sent`; `available = False` raises `EmailUnavailable`."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.sent: list[SentLink] = []

    async def send_login_link(self, *, to: str, link: str) -> None:
        if not self.available:
            raise EmailUnavailable("fake email is down")
        self.sent.append(SentLink(to=to, link=link))

    def last_link(self, to: str) -> str:
        return next(s.link for s in reversed(self.sent) if s.to == to)

    async def aclose(self) -> None:
        return None
