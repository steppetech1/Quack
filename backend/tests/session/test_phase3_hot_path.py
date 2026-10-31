"""Hot path of Phase 3 (ТЗ Фазы 3 §8.5)."""

from __future__ import annotations

import itertools
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from neo4j import AsyncDriver
from sqlalchemy import text

from quack.api.resources import AppResources, get_compute, get_llm
from quack.config import FoldParams
from quack.db.postgres import SessionFactory
from quack.events import append_events
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import run_projector_batch
from quack.session.schemas import TeacherOutput
from quack.session.turn_policy import MEMORY_UNAVAILABLE_MARKER, resolve_degraded_mode
from tests.factories import new_event
from tests.session.fixtures import FakeComputeClient, FakeLLMProvider, SeededDomain
from tests.session.helpers import parse_sse
from tests.support.broken import BrokenNeo4jDriver

pytest_plugins = ["tests.session.fixtures"]

# Fields of `task.evaluated` that must agree for a turn and a submit (WP6.2, T-H2).
PARITY_FIELDS = (
    "evaluator",
    "is_correct",
    "score",
    "independence",
    "skill_scores",
    "triggered_misconceptions",
)

_FLAGS = ("budget_exceeded", "llm_unavailable", "memory_unavailable", "teacher_fallback")
_ORDER = (*_FLAGS, "projector_lag")


@pytest.mark.parametrize("combo", list(itertools.product((False, True), repeat=5)))
def test_resolve_degraded_mode_priority(combo: tuple[bool, ...]) -> None:
    """T-H3: all 2^5 combinations → the first true reason by the priority of §4.4."""
    flags = dict(zip(_ORDER, combo, strict=True))
    expected = next((name for name in _ORDER if flags[name]), None)
    assert resolve_degraded_mode(**flags) == expected


# --- T-H1 --------------------------------------------------------------------------------


async def test_turn_survives_neo4j_outage(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    session_app: FastAPI,
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm: FakeLLMProvider,
) -> None:
    """T-H1 (WP6.1, Т-§20.7): turn 1 assigns task X; with a broken Neo4j driver turn 2
    returns the full SSE with `degraded_mode = "memory_unavailable"`, its events are written,
    X is not assigned again, the memory slot carries the marker; the graph view → 503."""
    learner = seeded_domain.learner_id
    task_x = seeded_domain.math_task_ids[0]
    start = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "outage-start",
        },
        headers=auth_headers,
    )
    assert start.status_code == 201, start.text
    session_id = UUID(start.json()["session_id"])
    await _project_all(neo4j, db, learner)

    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Try this task.", pedagogical_move="assign_task", assign_task_id=task_x
        ),
    )
    await _turn(client, auth_headers, session_id, "outage-turn-1", "Give me a task.")
    await _project_all(neo4j, db, learner)

    resources: AppResources = session_app.state.resources
    session_app.state.resources = replace(resources, neo4j=BrokenNeo4jDriver())  # type: ignore[arg-type]  # stand-in driver

    # The teacher asks for X again: the overlay must keep it out (session invariant).
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="One more task.", pedagogical_move="assign_task", assign_task_id=task_x
        ),
    )
    response = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Another one?", "idempotency_key": "outage-turn-2"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    sse = parse_sse(response.text)
    names = [name for name, _ in sse]
    assert names[0] == "turn_started"
    assert names[-1] == "done"
    data = dict(sse)
    assert data["turn_completed"]["degraded_mode"] == "memory_unavailable"
    if "task_assigned" in data:
        assert UUID(data["task_assigned"]["task_id"]) != task_x

    async with db() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT type, payload FROM core.events WHERE learner_id = :l "
                    "AND idempotency_key LIKE 'outage-turn-2%' ORDER BY seq"
                ),
                {"l": learner},
            )
        ).all()
        assigned = (
            await session.execute(
                text(
                    "SELECT payload->>'task_id' AS task_id FROM core.events "
                    "WHERE learner_id = :l AND type = 'task.assigned' "
                    "AND payload->>'session_id' = :s"
                ),
                {"l": learner, "s": str(session_id)},
            )
        ).all()
    assert rows[0].type == "turn.student_replied"
    replied = [r.payload for r in rows if r.type == "turn.teacher_replied"]
    assert len(replied) == 1
    assert replied[0]["degraded_mode"] == "memory_unavailable"
    assert [r.task_id for r in assigned].count(str(task_x)) == 1

    prompt = fake_llm.calls_for("teacher")[-1].prompt
    assert MEMORY_UNAVAILABLE_MARKER in prompt

    graph = await client.get(f"/v1/domains/{seeded_domain.domain_id}/graph", headers=auth_headers)
    assert graph.status_code == 503, graph.text
    assert graph.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


# --- T-H2 --------------------------------------------------------------------------------


async def _project_all(neo4j: AsyncDriver, db: SessionFactory, learner_id: UUID) -> None:
    async with db() as session:
        result = await session.execute(
            text(f"SELECT {EVENT_COLUMNS} FROM core.events WHERE learner_id = :l ORDER BY seq"),
            {"l": learner_id},
        )
        events = [row_to_event(r) for r in result.all()]
    store = Neo4jGraphStore(neo4j, str(learner_id))
    await run_projector_batch(learner_id, events, store, FoldParams())


async def _add_twin_tasks(
    db: SessionFactory, seed: SeededDomain, kind: str, reference: dict[str, Any]
) -> tuple[UUID, UUID]:
    """Two bank tasks equal in everything but id; difficulty 0.8 so that `tested_depth`
    of a code verdict is not equal to the difficulty by chance."""
    ids = (uuid4(), uuid4())
    events = [
        new_event(
            "task.generated",
            {
                "task_id": str(task_id),
                "domain_id": str(seed.domain_id),
                "topic_id": str(seed.topic_id),
                "family_id": f"parity-{kind}-{i}",
                "difficulty": 0.8,
                "kind": kind,
                "prompt_text": f"Parity task ({kind})",
                "reference_solution": reference,
                "target_skills": [{"skill": {"skill_id": str(seed.skill_ids[0])}, "weight": 1.0}],
                "target_misconceptions": [],
            },
        )
        for i, task_id in enumerate(ids)
    ]
    async with db() as session, session.begin():
        await append_events(session, seed.learner_id, events, publish_wakeup=False)
    return ids


async def _turn(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    session_id: UUID,
    key: str,
    text_: str,
    related_task_id: UUID | None = None,
) -> None:
    body: dict[str, Any] = {"text": text_, "idempotency_key": key}
    if related_task_id is not None:
        body["related_task_id"] = str(related_task_id)
    response = await client.post(f"/v1/sessions/{session_id}/turns", json=body, headers=headers)
    assert response.status_code == 200, response.text


async def _evaluated(db: SessionFactory, learner_id: UUID, task_id: UUID) -> dict[str, Any]:
    async with db() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT payload FROM core.events WHERE learner_id = :l "
                    "AND type = 'task.evaluated' AND payload->>'task_id' = :t"
                ),
                {"l": learner_id, "t": str(task_id)},
            )
        ).all()
    assert len(rows) == 1
    return dict(rows[0].payload)


@pytest.mark.parametrize(
    ("kind", "reference", "answer"),
    [
        ("math", {"sympy": "3*x**2", "hints": ["h1", "h2", "h3"]}, "3*x^2"),
        ("choice", {"option": "B", "hints": ["h1", "h2", "h3"]}, "B"),
    ],
)
async def test_turn_and_submit_evaluation_parity(
    kind: str,
    reference: dict[str, Any],
    answer: str,
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    session_app: FastAPI,
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm: FakeLLMProvider,
    fake_compute: FakeComputeClient,
) -> None:
    """T-H2: the same task, answer and hints through a turn with `related_task_id` and
    through `POST .../submit` → equal `task.evaluated` fields of `PARITY_FIELDS`."""
    assert fake_llm.gateway is not None
    gateway = fake_llm.gateway
    session_app.dependency_overrides[get_llm] = lambda: gateway
    session_app.dependency_overrides[get_compute] = lambda: fake_compute

    learner = seeded_domain.learner_id
    by_turn, by_submit = await _add_twin_tasks(db, seeded_domain, kind, reference)
    start = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": f"parity-start-{kind}",
        },
        headers=auth_headers,
    )
    assert start.status_code == 201, start.text
    session_id = UUID(start.json()["session_id"])
    await _project_all(neo4j, db, learner)

    # Both tasks are assigned by the teacher, one per turn.
    for i, task_id in enumerate((by_turn, by_submit)):
        fake_llm.push(
            "teacher",
            TeacherOutput(
                reply_text="Try this task.", pedagogical_move="assign_task", assign_task_id=task_id
            ),
        )
        await _turn(client, auth_headers, session_id, f"parity-assign-{kind}-{i}", "Next one?")
        await _project_all(neo4j, db, learner)

    # Two hints for each task, through the practice endpoint.
    for task_id in (by_turn, by_submit):
        for level in (1, 2):
            hint = await client.post(
                f"/v1/sessions/{session_id}/tasks/{task_id}/hint",
                json={"turn_id": str(uuid4()), "idempotency_key": f"parity-hint-{task_id}-{level}"},
                headers=auth_headers,
            )
            assert hint.status_code == 200, hint.text
    await _project_all(neo4j, db, learner)

    if kind == "math":
        fake_compute.push_result(True)
    fake_llm.push("teacher", TeacherOutput(reply_text="Good.", pedagogical_move="give_feedback"))
    await _turn(client, auth_headers, session_id, f"parity-answer-{kind}", answer, by_turn)

    if kind == "math":
        fake_compute.push_result(True)
    submit = await client.post(
        f"/v1/sessions/{session_id}/tasks/{by_submit}/submit",
        json={"turn_id": str(uuid4()), "answer": answer, "idempotency_key": f"parity-{kind}"},
        headers=auth_headers,
    )
    assert submit.status_code == 200, submit.text

    turn_eval = await _evaluated(db, learner, by_turn)
    submit_eval = await _evaluated(db, learner, by_submit)
    for name in PARITY_FIELDS:
        assert turn_eval[name] == submit_eval[name], name
    assert turn_eval["is_correct"] is True
