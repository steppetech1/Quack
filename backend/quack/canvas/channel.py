"""The Redis channel of a canvas (ТЗ Ф4 WP21.2; Т-§15.2): one channel per canvas, a server message
of any process (the API socket, the photo job of `workers`) to every device of the canvas."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import orjson
from redis.asyncio import Redis


def canvas_channel(canvas_id: UUID) -> str:
    """Т-§15.2: one channel per canvas."""
    return f"canvas:{canvas_id}"


async def publish_canvas(redis: Redis, canvas_id: UUID, message: dict[str, Any]) -> None:
    """A server message (`line_recognized`, `mark`, `hint`, `layer_update`, `task_closed`) to
    every socket of the canvas in every API process."""
    await redis.publish(canvas_channel(canvas_id), orjson.dumps(message))
