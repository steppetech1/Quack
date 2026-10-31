"""SSE helpers (spec §8.5): named events `event: <name>\\ndata: <json>\\n\\n`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import orjson
from fastapi.responses import StreamingResponse


def format_sse(event: str, data: dict[str, Any]) -> bytes:
    if "\n" in event or "\r" in event:
        raise ValueError("SSE event name must be a single line")
    return b"event: " + event.encode() + b"\ndata: " + orjson.dumps(data) + b"\n\n"


def sse_response(events: AsyncIterator[tuple[str, dict[str, Any]]]) -> StreamingResponse:
    async def body() -> AsyncIterator[bytes]:
        async for name, data in events:
            yield format_sse(name, data)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# WP6.3: an SSE comment that keeps a silent stream open behind proxies (`Д-§3.1`).
KEEPALIVE_COMMENT = b": ping\n\n"


def sse_bytes_response(chunks: AsyncIterator[bytes]) -> StreamingResponse:
    """A stream of ready SSE frames (named events and keepalive comments)."""
    return StreamingResponse(
        chunks,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
