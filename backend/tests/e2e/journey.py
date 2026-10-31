"""The full learner journey over HTTP (scenario of `T-E1`, reused by `T-E4`, `T-E10`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx

from tests.e2e.system import Learner, System
from tests.session.helpers import parse_sse
from tests.support.compute import math_verdict

# A word of the learner's own reply, found by full-text search afterwards.
ANSWER_WORD = "gradientwombat"


@dataclass
class Journey:
    learner: Learner
    domain_id: UUID
    topic_id: UUID
    session_id: UUID
    assigned_task_id: UUID
    practice_task_id: UUID
    turns: list[list[tuple[str, dict[str, Any]]]] = field(default_factory=list)


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
    """Domain → structure propose/confirm → diagnostic (start, 2 answers, finish)."""
    api, h = system.client, learner.headers
    domain = ok(
        await api.post(
            "/v1/domains", json={"name": "Calculus", "idempotency_key": f"{key}:d"}, headers=h
        ),
        201,
    )
    domain_id = UUID(domain["domain_id"])
    proposal = ok(
        await api.post(
            f"/v1/domains/{domain_id}/structure/propose",
            json={
                "goals": "Pass the exam",
                "background": "School algebra",
                "focus_areas": ["Basics", "Next steps"],
                "idempotency_key": f"{key}:propose",
            },
            headers=h,
        ),
        200,
        201,
    )
    ok(
        await api.post(
            f"/v1/domains/{domain_id}/structure/confirm",
            json={
                "structure_version": proposal["structure_version"],
                "idempotency_key": f"{key}:confirm",
            },
            headers=h,
        ),
        200,
        201,
    )
    topic_id = UUID(proposal["topics"][0]["topic_id"])
    await system.settle()

    started = ok(
        await api.post(
            f"/v1/domains/{domain_id}/diagnostic/start",
            json={"idempotency_key": f"{key}:diag"},
            headers=h,
        ),
        200,
        201,
    )
    diag_session = started["session_id"]
    question: dict[str, Any] | None = started["next_question"]
    for n in range(2):
        assert question is not None, "diagnostic ended before two answers"
        answer = ok(
            await api.post(
                f"/v1/domains/{domain_id}/diagnostic/answer",
                json={
                    "session_id": diag_session,
                    "task_id": question["task_id"],
                    "answer": (question.get("options") or ["A"])[0],
                    "confidence_self_report": 0.7,
                    "idempotency_key": f"{key}:diag:a{n}",
                },
                headers=h,
            )
        )
        question = answer.get("next_question")
    ok(
        await api.post(
            f"/v1/domains/{domain_id}/diagnostic/finish",
            json={"session_id": diag_session, "idempotency_key": f"{key}:diag:finish"},
            headers=h,
        )
    )
    await system.settle()
    return domain_id, topic_id


async def turn(
    system: System,
    learner: Learner,
    session_id: UUID,
    text: str,
    key: str,
    *,
    related_task_id: UUID | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    body: dict[str, Any] = {"text": text, "idempotency_key": key}
    if related_task_id is not None:
        body["related_task_id"] = str(related_task_id)
    return sse_events(
        await system.client.post(
            f"/v1/sessions/{session_id}/turns", json=body, headers=learner.headers
        )
    )


async def run_journey(system: System, key: str = "journey") -> Journey:
    """`T-E1` scenario up to the final `settle()`."""
    learner = await system.register()
    api, h = system.client, learner.headers
    domain_id, topic_id = await onboard(system, learner, key)
    system.compute.push(*(math_verdict(True) for _ in range(4)))

    session = ok(
        await api.post(
            "/v1/sessions",
            json={
                "domain_id": str(domain_id),
                "topic_id": str(topic_id),
                "idempotency_key": f"{key}:s",
            },
            headers=h,
        ),
        201,
    )
    session_id = UUID(session["session_id"])

    turns: list[list[tuple[str, dict[str, Any]]]] = []
    turns.append(await turn(system, learner, session_id, "Where do I start?", f"{key}:t1"))
    assigned = first(turns[0], "task_assigned")
    assert assigned is not None, f"no task assigned on the first turn: {turns[0]}"
    assigned_task_id = UUID(assigned["task_id"])
    turns.append(
        await turn(
            system,
            learner,
            session_id,
            f"A, because of the {ANSWER_WORD} rule",
            f"{key}:t2",
            related_task_id=assigned_task_id,
        )
    )
    turns.append(await turn(system, learner, session_id, "What next?", f"{key}:t3"))
    last_turn = first(turns[-1], "turn_started")
    assert last_turn is not None
    turn_id = last_turn["turn_id"]

    nxt = ok(
        await api.post(
            f"/v1/sessions/{session_id}/tasks/next",
            json={"topic_id": str(topic_id), "turn_id": turn_id, "idempotency_key": f"{key}:next"},
            headers=h,
        )
    )
    practice_task_id = UUID(nxt["task_id"])
    ok(
        await api.post(
            f"/v1/sessions/{session_id}/tasks/{practice_task_id}/hint",
            json={"turn_id": turn_id, "idempotency_key": f"{key}:hint"},
            headers=h,
        )
    )
    ok(
        await api.post(
            f"/v1/sessions/{session_id}/tasks/{practice_task_id}/submit",
            json={"turn_id": turn_id, "answer": "A", "idempotency_key": f"{key}:submit"},
            headers=h,
        )
    )
    ok(
        await api.post(
            f"/v1/sessions/{session_id}/finish",
            json={"idempotency_key": f"{key}:finish"},
            headers=h,
        )
    )
    await system.settle()
    return Journey(
        learner, domain_id, topic_id, session_id, assigned_task_id, practice_task_id, turns
    )
