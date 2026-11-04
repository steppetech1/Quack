"""Holdback buffer of the streamed turn body (Т-§12.2 stage 3; ТЗ Ф4 WP13.4), pure part.

The last `holdback_chars` characters (`spoiler_holdback_chars`) are not shown until the
spoiler check over the whole accumulated text has passed; a spoiler stops the stream
(`SpoilerDetected` → SSE `reset`, the turn is rebuilt with the reason, WP13.2). Nothing that
was released was ever part of a text with a spoiler. No artificial delay: a chunk is released
as soon as it is checked.
"""

from __future__ import annotations

from collections.abc import Callable


class SpoilerDetected(Exception):
    """The accumulated body text reveals the answer of an active task (Т-§12.4)."""


class HoldbackBuffer:
    def __init__(self, holdback_chars: int, has_spoiler: Callable[[str], bool]) -> None:
        self._holdback = holdback_chars
        self._has_spoiler = has_spoiler
        self._text = ""
        self._released = 0

    def feed(self, chunk: str) -> str:
        """Add `chunk`; return the newly releasable text (may be empty). `SpoilerDetected` if
        the accumulated text has a spoiler."""
        self._text += chunk
        return self._release(max(self._released, len(self._text) - self._holdback))

    def finish(self) -> str:
        """End of the body: check the whole text and release the rest."""
        return self._release(len(self._text))

    def _release(self, upto: int) -> str:
        if self._has_spoiler(self._text):
            raise SpoilerDetected
        released, self._released = self._text[self._released : upto], upto
        return released

    @property
    def released(self) -> str:
        """Everything released so far."""
        return self._text[: self._released]
