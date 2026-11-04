"""Plain helpers of the session tests: an SSE body parser."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import orjson

from quack.api.auth import SESSION_COOKIE
from quack.auth.sessions import open_session
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import DomainCreated
from quack.events.log import NewEvent, append_events


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


@dataclass(frozen=True)
class Learner:
    """A learner of the Phase 4 database with a domain and a cookie session."""

    learner_id: UUID
    domain_id: UUID
    cookies: dict[str, str]


async def make_learner(pg_db: Db, settings: Settings) -> Learner:
    learner_id, domain_id = uuid4(), uuid4()
    await pg_db.execute(
        "INSERT INTO core.learners (learner_id, email) VALUES (%(l)s, %(e)s)",
        {"l": learner_id, "e": f"{learner_id}@test.invalid"},
    )
    created = NewEvent(
        type="domain.created",
        payload=DomainCreated(domain_id=domain_id, name="Calculus"),
        actor="learner",
        idempotency_key=f"domain:{domain_id}",
    )
    await append_events(pg_db, learner_id, [created])
    token = await open_session(pg_db, settings, learner_id=learner_id, now=datetime.now(UTC))
    return Learner(learner_id, domain_id, {SESSION_COOKIE: token})
