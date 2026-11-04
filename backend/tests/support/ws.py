"""A WebSocket client over ASGI for the tests (ТЗ Ф4 WP21.2), without a server or a new library.

`httpx` has no WebSockets and Starlette's `TestClient` runs the app on its own event loop, which
breaks the async pools of the test fixtures. `AsgiWebSocket` calls the app on the test's loop
with the ASGI `websocket` scope and two queues.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import MutableMapping
from types import TracebackType
from typing import Any

from starlette.types import ASGIApp

type Message = MutableMapping[str, Any]


class WebSocketClosed(Exception):
    """The server closed the socket (or refused it before `accept`) with `code`."""

    def __init__(self, code: int) -> None:
        super().__init__(f"websocket closed with code {code}")
        self.code = code


class AsgiWebSocket:
    """`async with AsgiWebSocket(app, "/v1/canvases/…/ws", cookies={...}) as ws:`."""

    def __init__(self, app: ASGIApp, path: str, *, cookies: dict[str, str] | None = None) -> None:
        cookie = "; ".join(f"{name}={value}" for name, value in (cookies or {}).items())
        headers = [(b"host", b"test")] + ([(b"cookie", cookie.encode())] if cookie else [])
        self._scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "server": ("test", 80),
            "client": ("127.0.0.1", 12345),
            "subprotocols": [],
        }
        self._app = app
        self._to_app: asyncio.Queue[Message] = asyncio.Queue()
        self._from_app: asyncio.Queue[Message] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.closed_code: int | None = None

    async def __aenter__(self) -> AsgiWebSocket:
        self._task = asyncio.create_task(
            self._app(self._scope, self._to_app.get, self._from_app.put)  # type: ignore[arg-type]
        )
        await self._to_app.put({"type": "websocket.connect"})
        first = await self._next()
        if first["type"] == "websocket.close":
            await self._task
            raise WebSocketClosed(int(first.get("code", 1000)))
        assert first["type"] == "websocket.accept", first
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.disconnect()

    async def _next(self, timeout_s: float = 5.0) -> Message:
        async with asyncio.timeout(timeout_s):
            return await self._from_app.get()

    async def send_json(self, data: Any) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def send_text(self, text: str) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": text})

    async def receive_json(self, timeout_s: float = 5.0) -> Any:
        """The next message of the server; `WebSocketClosed` if it closed the socket instead,
        `TimeoutError` if nothing came."""
        message = await self._next(timeout_s)
        if message["type"] == "websocket.close":
            self.closed_code = int(message.get("code", 1000))
            raise WebSocketClosed(self.closed_code)
        assert message["type"] == "websocket.send", message
        return json.loads(message["text"])

    async def disconnect(self) -> None:
        """The client goes away; waits until the server's handler has finished."""
        if self._task is None:
            return
        if not self._task.done():
            await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
            async with asyncio.timeout(10):
                await self._task
        self._task = None
