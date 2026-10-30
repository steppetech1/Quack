"""Plain helpers of the session stream tests: JWT for a learner and an SSE body parser."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import jwt
import orjson

from quack.config import Settings

DEV_JWT_SECRET = "quack-dev-jwt-secret"


def jwt_secret(settings: Settings) -> str:
    """The HS256 secret of ТЗ §2.1."""
    return settings.jwt_secret.get_secret_value() or DEV_JWT_SECRET


def make_token(settings: Settings, learner_id: UUID) -> str:
    return jwt.encode({"sub": str(learner_id)}, jwt_secret(settings), algorithm="HS256")


def parse_sse(body: str | bytes) -> list[tuple[str, dict[str, Any]]]:
    """Split an SSE body (`event: <name>\\ndata: <json>\\n\\n`, spec §8.5) into (event, data)."""
    text = body.decode() if isinstance(body, bytes) else body
    out: list[tuple[str, dict[str, Any]]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        event = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if name == "event":
                event = value
            elif name == "data":
                data_lines.append(value)
        if data_lines:
            out.append((event, orjson.loads("\n".join(data_lines))))
    return out
