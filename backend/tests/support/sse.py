"""Streaming SSE client for tests (ТЗ Фазы 3 §8.1, S9): `httpx.ASGITransport` buffers the
whole response, so open-ended SSE streams are read through this transport, which returns the
response at `http.response.start` and sends `http.disconnect` when the stream is closed."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, MutableMapping
from typing import Any

import httpx
import orjson
from fastapi import FastAPI

KEEPALIVE = ": ping"


class _StreamingBody(httpx.AsyncByteStream):
    def __init__(
        self,
        queue: asyncio.Queue[bytes | None],
        disconnect: asyncio.Event,
        task: asyncio.Task[None],
    ) -> None:
        self._queue = queue
        self._disconnect = disconnect
        self._task = task

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while (chunk := await self._queue.get()) is not None:
            yield chunk

    async def aclose(self) -> None:
        self._disconnect.set()
        if not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task


class StreamingASGITransport(httpx.AsyncBaseTransport):
    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for (k, v) in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port or 80),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
        }
        sent = False
        disconnect = asyncio.Event()
        start: asyncio.Future[tuple[int, list[tuple[bytes, bytes]]]] = (
            asyncio.get_running_loop().create_future()
        )
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        body = await request.aread()

        async def receive() -> MutableMapping[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start" and not start.done():
                start.set_result((int(message["status"]), list(message.get("headers", []))))
            elif message["type"] == "http.response.body":
                if chunk := message.get("body", b""):
                    await queue.put(bytes(chunk))
                if not message.get("more_body", False):
                    await queue.put(None)

        async def run() -> None:
            try:
                await self.app(scope, receive, send)
            except BaseException as exc:
                if not start.done():
                    start.set_exception(exc)
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        status, headers = await start
        return httpx.Response(
            status, headers=headers, stream=_StreamingBody(queue, disconnect, task)
        )


async def next_sse_block(
    lines: AsyncIterator[str], timeout_s: float = 3.0
) -> str | tuple[str, Any]:
    """The next SSE block: `KEEPALIVE` for a comment, else `(event, data)`."""
    block: list[str] = []
    async with asyncio.timeout(timeout_s):
        async for line in lines:
            if line:
                block.append(line)
            elif block:
                break
    if block and all(line.startswith(":") for line in block):
        return KEEPALIVE
    event = next(line[len("event: ") :] for line in block if line.startswith("event: "))
    data = next(line[len("data: ") :] for line in block if line.startswith("data: "))
    return event, orjson.loads(data)


async def next_sse_event(lines: AsyncIterator[str], timeout_s: float = 3.0) -> tuple[str, Any]:
    """The next named SSE event, skipping keepalive comments."""
    async with asyncio.timeout(timeout_s):
        while True:
            block = await next_sse_block(lines, timeout_s)
            if block != KEEPALIVE:
                assert isinstance(block, tuple)
                return block
