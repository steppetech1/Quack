"""Steps of the learner journey over HTTP: the onboarding (a domain with a topic and a skill).

The session half of the Phase 3 journey (`/turns`, `tasks/next`, `finish`) left with the
routes (ТЗ Ф4 §8.15; D-B-210); `T-E1` on the Phase 4 path is WP26.1."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx

from quack.events.log import NewEvent, append_events
from tests.e2e.system import Learner, System
from tests.session.helpers import parse_sse

# A word of the learner's own reply, found by full-text search afterwards.
ANSWER_WORD = "gradientwombat"


def ok(response: httpx.Response, *codes: int) -> dict[str, Any]:
    assert response.status_code in (codes or (200,)), (
        f"{response.request.method} {response.request.url.path}: "
        f"{response.status_code} {response.text}"
    )
    return response.json()


def sse_events(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    assert response.status_code == 200, response.text
    return parse_sse(response.content)


def first(events: list[tuple[str, dict[str, Any]]], name: str) -> dict[str, Any] | None:
    return next((data for event, data in events if event == name), None)


async def onboard(system: System, learner: Learner, key: str) -> tuple[UUID, UUID]:
    """The domain by `POST /v1/domains` and a topic with a skill in it, written to the log of
    Phase 4 the way the structure of a session is (`topic.created`, `skill.created`); the projector
    puts them into the graph. The interview of the structure and the diagnostic are removed with
    their routes (ТЗ Ф4 §2.3 row 5, §8.15 п.1). Returns the domain and the topic."""
    domain = ok(
        await system.client.post(
            "/v1/domains",
            json={"name": "Calculus", "idempotency_key": f"{key}:d"},
            headers=learner.headers,
        ),
        201,
    )
    domain_id = UUID(domain["domain_id"])
    topic_id, skill_id = uuid4(), uuid4()
    d = str(domain_id)
    await append_events(
        system.resources.db,
        learner.learner_id,
        [
            NewEvent(
                type="topic.created",
                payload={
                    "topic_id": str(topic_id),
                    "domain_id": d,
                    "name": "Basics",
                    "origin": "session",
                },
                actor="code",
                idempotency_key=f"{key}:topic",
                domain_id=domain_id,
            ),
            NewEvent(
                type="skill.created",
                payload={
                    "skill_id": str(skill_id),
                    "topic_id": str(topic_id),
                    "domain_id": d,
                    "name": "Core idea",
                    "summary": "The core idea of the topic",
                    "status": "active",
                    "origin": "session",
                },
                actor="code",
                idempotency_key=f"{key}:skill",
                domain_id=domain_id,
            ),
        ],
    )
    await system.settle()
    return domain_id, topic_id
