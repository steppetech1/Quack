"""SSE keepalive of long-lived streams (ТЗ Фазы 3 WP6.3, Д-§3.1).

When no event comes for `sse_keepalive_s`, the stream sends the SSE comment `: ping` and an
empty line. It is not an event: clients ignore it, proxies see a live connection.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import anyio
from fastapi.responses import StreamingResponse

from quack.api.sse import format_sse

PING = b": ping\n\n"


async def with_keepalive(
    events: AsyncIterator[tuple[str, dict[str, Any]]], interval_s: float
) -> AsyncIterator[bytes]:
    """SSE bytes of `events`, with `PING` after every `interval_s` of silence.

    The pending step of `events` is kept across pings (a timeout must not cancel it); on
    exit it is cancelled and awaited, then `events` is closed so its cleanup runs."""
    pending: asyncio.Future[tuple[str, dict[str, Any]]] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(events))
            done, _ = await asyncio.wait({pending}, timeout=interval_s)
            if not done:
                yield PING
                continue
            step, pending = pending, None
            try:
                name, data = step.result()
            except StopAsyncIteration:
                return
            yield format_sse(name, data)
    finally:
        # The client disconnect cancels the stream; shield so the cleanup still runs.
        with anyio.CancelScope(shield=True):
            if pending is not None:
                pending.cancel()
                with contextlib.suppress(BaseException):
                    await pending
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                await aclose()


def sse_keepalive_response(
    events: AsyncIterator[tuple[str, dict[str, Any]]], interval_s: float
) -> StreamingResponse:
    """`quack.api.sse.sse_response` with keepalive comments (same media type and headers)."""
    return StreamingResponse(
        with_keepalive(events, interval_s),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
