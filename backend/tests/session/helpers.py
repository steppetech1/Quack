"""Plain helpers of the session stream tests: JWT for a learner, an SSE body parser and the
labels the teacher sees (WP14.1)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import jwt
import orjson

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.session.turns import get_available_tasks

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


# --- labels of teacher@1 (WP14.1) ---------------------------------------------------------


def skill_label(topic_skill_ids: Sequence[UUID], skill_id: UUID) -> str:
    """Label of a topic skill in the teacher context: `S<n>` by the order of the slice,
    which lists the skills of the topic sorted by id (`session/context.py::_topic_slice`)."""
    ordered = sorted(str(s) for s in topic_skill_ids)
    return f"S{ordered.index(str(skill_id)) + 1}"


async def task_label(
    db: SessionFactory, learner_id: UUID, topic_id: UUID, session_id: UUID, task_id: UUID
) -> str:
    """Label of a bank task offered to the teacher on the next turn: `K<n>` by the order of
    `get_available_tasks` (difficulty, created_seq, id), tasks of the session excluded."""
    async with db() as session:
        available = await get_available_tasks(session, learner_id, topic_id, session_id=session_id)
    ids = [t.task_id for t in available]
    return f"K{ids.index(task_id) + 1}"
