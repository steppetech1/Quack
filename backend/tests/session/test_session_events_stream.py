"""SSE relay tests for `GET /v1/sessions/{id}/events` (ТЗ §3, T-014).

Note on Starlette `StreamingResponse` and 404 ownership checks:
`StreamingResponse` sends `http.response.start` (status 200) before advancing `body_iterator`.
Therefore, raising `ApiError("NOT_FOUND", ..., status_code=404)` inside `body_iterator` after
`http.response.start` has already been sent causes Starlette to raise
`RuntimeError("Caught handled exception, but response already started.")`.
Ownership must be verified before `http.response.start` is sent — either by awaiting the
ownership check in `router.py` before `sse_response(...)` (like `turns.open_turn`), or by
having `router.sse_response` return a `StreamingResponse` subclass whose `__call__` awaits an
ownership check hook on `events` before calling `super().__call__(scope, receive, send)`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, MutableMapping
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import httpx
import orjson
from fastapi import FastAPI
from redis.asyncio import Redis

from quack.config import Settings
from quack.db.redis import session_events_channel
from tests.session.helpers import make_token, parse_sse

if TYPE_CHECKING:
    from tests.session.fixtures import SeededDomain

pytest_plugins = ["tests.session.fixtures"]


class _StreamingASGIByteStream(httpx.AsyncByteStream):
    """Incremental byte stream over an active ASGI task that sends `http.disconnect` on close."""

    def __init__(
        self,
        body_queue: asyncio.Queue[bytes | None],
        disconnect: asyncio.Event,
        app_task: asyncio.Task[None],
    ) -> None:
        self._queue = body_queue
        self._disconnect = disconnect
        self._task = app_task

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
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
    """ASGI transport that returns `httpx.Response` as soon as `http.response.start` arrives.

    Unlike `httpx.ASGITransport` (which buffers the entire response until `more_body=False`),
    this allows tests to read incremental SSE events from an open-ended stream and trigger
    `http.disconnect` when closing the stream.
    """

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
        request_sent = False
        disconnect = asyncio.Event()
        loop = asyncio.get_running_loop()
        start_future: asyncio.Future[tuple[int, list[tuple[bytes, bytes]]]] = loop.create_future()
        body_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        request_body = await request.aread()

        async def receive() -> MutableMapping[str, Any]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": request_body, "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                if not start_future.done():
                    start_future.set_result(
                        (int(message["status"]), list(message.get("headers", [])))
                    )
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    await body_queue.put(bytes(body))
                if not message.get("more_body", False):
                    await body_queue.put(None)

        async def run_app() -> None:
            try:
                await self.app(scope, receive, send)
            except BaseException as exc:
                if not start_future.done():
                    start_future.set_exception(exc)
            finally:
                await body_queue.put(None)

        app_task = asyncio.create_task(run_app())
        status_code, headers = await start_future
        return httpx.Response(
            status_code,
            headers=headers,
            stream=_StreamingASGIByteStream(body_queue, disconnect, app_task),
        )


async def _wait_for_subscribers(redis: Redis, channel: str, expected: int) -> None:
    """Wait until Redis `PUBSUB NUMSUB` reports `expected` subscribers on `channel`."""
    async with asyncio.timeout(2.0):
        while True:
            counts = dict(await redis.pubsub_numsub(channel))
            if int(counts.get(channel, 0)) == expected:
                return
            await asyncio.sleep(0.01)


async def _read_one_sse_event(
    lines_iter: AsyncIterator[str],
) -> tuple[str, dict[str, Any]]:
    """Read lines from an SSE response stream until one complete event block is received."""
    block_lines: list[str] = []
    async with asyncio.timeout(2.0):
        async for line in lines_iter:
            if line:
                block_lines.append(line)
            elif block_lines:
                break
    parsed = parse_sse("\n".join(block_lines) + "\n\n")
    assert len(parsed) == 1, f"expected 1 SSE event, got {parsed!r} from {block_lines!r}"
    return parsed[0]


async def test_session_events_stream_relays_published_events_and_unsubscribes_on_disconnect(
    session_app: FastAPI,
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    redis: Redis,
) -> None:
    """T-014: `GET /v1/sessions/{id}/events` subscribes to `session_events_channel(session_id)`,
    forwards published `extractor_done` and `cheatsheet_ready` messages as-is as SSE events,
    and unsubscribes when the client disconnects.
    """
    # 1. Start an owned session
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-sse-events-1",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])
    channel = session_events_channel(session_id)

    # 2. Open the SSE stream over StreamingASGITransport
    async with (
        httpx.AsyncClient(
            transport=StreamingASGITransport(session_app), base_url="http://test"
        ) as stream_client,
        stream_client.stream(
            "GET", f"/v1/sessions/{session_id}/events", headers=auth_headers
        ) as resp,
    ):
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers.get("cache-control") == "no-cache"

        # Wait until the endpoint has subscribed to `session:events:{session_id}`
        await _wait_for_subscribers(redis, channel, expected=1)

        lines_iter = resp.aiter_lines()

        # 3. Publish `extractor_done` to the channel and read it from the SSE stream
        turn_id = uuid4()
        extractor_payload = {"turn_id": str(turn_id), "seq": 42}
        await redis.publish(
            channel,
            orjson.dumps({"event": "extractor_done", "data": extractor_payload}).decode(),
        )
        event_name_1, event_data_1 = await _read_one_sse_event(lines_iter)
        assert event_name_1 == "extractor_done"
        assert event_data_1 == extractor_payload

        # 4. Publish `cheatsheet_ready` to the channel and verify it is also forwarded as-is
        artifact_id = uuid4()
        cheatsheet_payload = {
            "artifact_id": str(artifact_id),
            "topic_id": str(seeded_domain.topic_id),
            "version": 3,
        }
        await redis.publish(
            channel,
            orjson.dumps({"event": "cheatsheet_ready", "data": cheatsheet_payload}).decode(),
        )
        event_name_2, event_data_2 = await _read_one_sse_event(lines_iter)
        assert event_name_2 == "cheatsheet_ready"
        assert event_data_2 == cheatsheet_payload

    # 5. Client disconnected -> subscription to `session_events_channel(session_id)` is cleaned up
    await _wait_for_subscribers(redis, channel, expected=0)


async def test_session_events_foreign_or_unknown_session_returns_404(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    settings: Settings,
) -> None:
    """T-014: `GET /v1/sessions/{id}/events` returns 404 NOT_FOUND for a foreign/unknown session."""
    # 1. Start a session owned by `seeded_domain.learner_id`
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-sse-foreign-1",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])

    # 2. Another learner requesting `/v1/sessions/{session_id}/events` gets 404 NOT_FOUND
    foreign_learner = uuid4()
    foreign_headers = {"Authorization": f"Bearer {make_token(settings, foreign_learner)}"}
    foreign_resp = await client.get(
        f"/v1/sessions/{session_id}/events",
        headers=foreign_headers,
    )
    assert foreign_resp.status_code == 404, foreign_resp.text
    assert foreign_resp.json()["error"]["code"] == "NOT_FOUND"

    # 3. Unknown session_id for the authenticated learner also gets 404 NOT_FOUND
    unknown_resp = await client.get(
        f"/v1/sessions/{uuid4()}/events",
        headers=auth_headers,
    )
    assert unknown_resp.status_code == 404, unknown_resp.text
    assert unknown_resp.json()["error"]["code"] == "NOT_FOUND"
