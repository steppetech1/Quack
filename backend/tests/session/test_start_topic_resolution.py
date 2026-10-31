"""Topic resolution, `read_consistent_slice` and explicit `plan` on session start (РТЗ S-3)."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import httpx
import neo4j
import orjson
import pytest
from neo4j import AsyncDriver
from sqlalchemy import text

import quack.graph.slice as slice_mod
from quack.api.app import AppResources
from quack.config import FoldParams, Settings
from quack.db.neo4j import cypher
from quack.db.postgres import SessionFactory
from quack.events import append_events
from quack.events.reader import EVENT_COLUMNS, row_to_event
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import run_projector_batch
from quack.session import lifecycle
from quack.session.schemas import TeacherOutput
from tests.factories import new_event
from tests.session.helpers import parse_sse

if TYPE_CHECKING:
    from tests.session.fixtures import FakeLLMProvider, SeededDomain

pytest_plugins = ["tests.session.fixtures"]


async def _project_all(neo4j_driver: AsyncDriver, db: SessionFactory, learner_id: UUID) -> None:
    async with db() as session:
        result = await session.execute(
            text(f"SELECT {EVENT_COLUMNS} FROM core.events WHERE learner_id = :l ORDER BY seq"),
            {"l": learner_id},
        )
        events = [row_to_event(r) for r in result.all()]
    store = Neo4jGraphStore(neo4j_driver, str(learner_id))
    outcome = await run_projector_batch(learner_id, events, store, FoldParams())
    assert outcome.applied_seq == events[-1].seq


async def test_start_without_topic_id_stores_and_returns_seeded_topic_and_assigns_practice_task(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_llm: FakeLLMProvider,
) -> None:
    """Starting with `topic_id=None` resolves the seeded topic into `session.started`,
    `core.sessions.topic_id` and the HTTP response, and the next `practice` turn assigns a
    `core.task_bank` task of that topic.
    """
    seed = seeded_domain

    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seed.domain_id),
            "topic_id": None,
            "mode": "practice",
            "idempotency_key": "start-resolve-topic-1",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    start_body = start_resp.json()
    session_id = UUID(start_body["session_id"])
    assert start_body["topic_id"] == str(seed.topic_id)
    assert start_body["domain_id"] == str(seed.domain_id)
    assert start_body["mode"] == "practice"
    assert isinstance(start_body["plan"], list)
    assert 3 <= len(start_body["plan"]) <= 5
    plan_text = orjson.dumps(start_body["plan"]).decode()
    assert any(str(s) in plan_text for s in seed.skill_ids)

    # Verify `session.started.payload["topic_id"]` and `core.sessions.topic_id`
    async with db() as session:
        ev_row = (
            await session.execute(
                text(
                    "SELECT payload FROM core.events "
                    "WHERE learner_id = :l AND session_id = :s AND type = 'session.started'"
                ),
                {"l": seed.learner_id, "s": session_id},
            )
        ).one()
        sess_row = (
            await session.execute(
                text("SELECT topic_id, plan FROM core.sessions WHERE id = :s AND learner_id = :l"),
                {"s": session_id, "l": seed.learner_id},
            )
        ).one()

    assert ev_row.payload["topic_id"] == str(seed.topic_id)
    assert sess_row.topic_id == seed.topic_id
    assert sess_row.plan == start_body["plan"]

    # Active & detail endpoints also report the resolved topic_id
    active_resp = await client.get("/v1/sessions/active", headers=auth_headers)
    assert active_resp.status_code == 200, active_resp.text
    assert active_resp.json()["topic_id"] == str(seed.topic_id)

    # Idempotent retry with `topic_id=None` returns the same session and resolved topic_id
    retry_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seed.domain_id),
            "topic_id": None,
            "mode": "practice",
            "idempotency_key": "start-resolve-topic-1",
        },
        headers=auth_headers,
    )
    assert retry_resp.status_code == 201, retry_resp.text
    assert retry_resp.json()["session_id"] == str(session_id)
    assert retry_resp.json()["topic_id"] == str(seed.topic_id)

    # Next turn in `practice` mode assigns a task from `core.task_bank` of the resolved topic
    await _project_all(neo4j, db, seed.learner_id)
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Here is a practice problem on derivatives.",
            pedagogical_move="explain",
        ),
    )

    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Give me a practice task", "idempotency_key": "turn-resolve-topic-1"},
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200, turn_resp.text
    sse_events = parse_sse(turn_resp.text)
    event_names = [name for name, _ in sse_events]
    assert "task_assigned" in event_names

    assigned_sse = dict(sse_events)["task_assigned"]
    assigned_task_id = UUID(assigned_sse["task_id"])
    assert assigned_task_id in seed.task_ids

    async with db() as session:
        assigned_ev = (
            await session.execute(
                text(
                    "SELECT payload FROM core.events "
                    "WHERE learner_id = :l AND session_id = :s AND type = 'task.assigned'"
                ),
                {"l": seed.learner_id, "s": session_id},
            )
        ).one()
    assert UUID(assigned_ev.payload["task_id"]) == assigned_task_id
    assert UUID(assigned_ev.payload["topic_id"]) == seed.topic_id


async def test_start_with_explicit_plan_stores_exact_plan_in_session_started(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
) -> None:
    """Starting with an explicit `plan` stores that exact plan in `session.started` and
    `core.sessions.plan` while still resolving `topic_id` when `topic_id=None`.
    """
    seed = seeded_domain
    explicit_plan = [
        {"step": 0, "kind": "learn", "skill_id": str(seed.skill_ids[0]), "title": "Custom intro"},
        {"step": 1, "kind": "practice", "title": "Custom drill"},
    ]

    resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seed.domain_id),
            "topic_id": None,
            "mode": "learn",
            "plan": explicit_plan,
            "idempotency_key": "start-explicit-plan-1",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    session_id = UUID(body["session_id"])
    assert body["topic_id"] == str(seed.topic_id)
    assert body["plan"] == explicit_plan

    async with db() as session:
        ev_row = (
            await session.execute(
                text(
                    "SELECT payload FROM core.events "
                    "WHERE learner_id = :l AND session_id = :s AND type = 'session.started'"
                ),
                {"l": seed.learner_id, "s": session_id},
            )
        ).one()
        sess_row = (
            await session.execute(
                text("SELECT topic_id, plan FROM core.sessions WHERE id = :s AND learner_id = :l"),
                {"s": session_id, "l": seed.learner_id},
            )
        ).one()

    assert ev_row.payload["plan"] == explicit_plan
    assert ev_row.payload["topic_id"] == str(seed.topic_id)
    assert sess_row.plan == explicit_plan
    assert sess_row.topic_id == seed.topic_id


async def test_resolve_topic_and_plan_signature_consistent_slice_and_read_access(
    session_app: Any,
    settings: Settings,
    seeded_domain: SeededDomain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_resolve_topic_and_plan` replaces `_default_plan` with the exact РТЗ S-3 signature and
    reads Neo4j through `read_consistent_slice` with `driver.session(default_access_mode=...)`.
    """
    assert not hasattr(lifecycle, "_default_plan")
    assert hasattr(lifecycle, "_resolve_topic_and_plan")

    sig = inspect.signature(lifecycle._resolve_topic_and_plan)
    assert list(sig.parameters) == [
        "resources",
        "settings",
        "learner_id",
        "domain_id",
        "topic_id",
        "explicit_plan",
    ]

    resources: AppResources = session_app.state.resources
    slice_calls: list[UUID] = []
    real_slice = slice_mod.read_consistent_slice

    async def spy_slice(learner_id: UUID, *args: Any, **kwargs: Any) -> Any:
        slice_calls.append(learner_id)
        return await real_slice(learner_id, *args, **kwargs)

    monkeypatch.setattr(slice_mod, "read_consistent_slice", spy_slice)
    if hasattr(lifecycle, "read_consistent_slice"):
        monkeypatch.setattr(lifecycle, "read_consistent_slice", spy_slice)

    topic_query_modes: list[str | None] = []
    real_session = resources.neo4j.session

    class _SessionProxy:
        def __init__(self, inner: Any, mode: str | None) -> None:
            self._inner = inner
            self._mode = mode

        async def __aenter__(self) -> _SessionProxy:
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, *exc: object) -> Any:
            return await self._inner.__aexit__(*exc)

        async def run(self, query: Any, *args: Any, **kwargs: Any) -> Any:
            if "Topic" in str(query):
                topic_query_modes.append(self._mode)
            return await self._inner.run(query, *args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    def spy_session(**kwargs: Any) -> Any:
        mode = kwargs.get("default_access_mode")
        return _SessionProxy(real_session(**kwargs), mode)

    monkeypatch.setattr(resources.neo4j, "session", spy_session)

    resolved_topic_id, plan = await lifecycle._resolve_topic_and_plan(
        resources,
        settings,
        seeded_domain.learner_id,
        seeded_domain.domain_id,
        None,
        None,
    )

    assert resolved_topic_id == seeded_domain.topic_id
    assert isinstance(plan, list)
    assert 3 <= len(plan) <= 5
    assert slice_calls == [seeded_domain.learner_id]
    assert topic_query_modes
    assert all(mode == neo4j.READ_ACCESS for mode in topic_query_modes)


async def test_resolve_topic_prefers_open_display_states_else_any_active_topic(
    session_app: Any,
    settings: Settings,
    learner: UUID,
    db: SessionFactory,
    neo4j: AsyncDriver,
) -> None:
    """When `topic_id is None`, `_resolve_topic_and_plan` picks the first topic with
    `displayState IN ('in_progress', 'available', 'rusty')`, or else falls back to any active
    topic of the domain.
    """
    domain_id = uuid4()
    topic_locked = uuid4()
    topic_rusty = uuid4()
    skill_locked = uuid4()
    skill_rusty = uuid4()

    events = [
        new_event(
            "domain.created",
            {"domain_id": str(domain_id), "name": "Algebra", "slug": "algebra"},
        ),
        new_event(
            "structure.proposed",
            {
                "domain_id": str(domain_id),
                "structure_version": 1,
                "topics": [
                    {"topic_id": str(topic_locked), "name": "01 Locked First"},
                    {"topic_id": str(topic_rusty), "name": "02 Open Second"},
                ],
                "skills": [
                    {
                        "skill_id": str(skill_locked),
                        "topic_id": str(topic_locked),
                        "name": "Locked skill",
                    },
                    {
                        "skill_id": str(skill_rusty),
                        "topic_id": str(topic_rusty),
                        "name": "Rusty skill",
                    },
                ],
                "prereqs": [],
                "skill_deps": [],
                "misconceptions": [],
            },
        ),
        new_event("structure.confirmed", {"domain_id": str(domain_id), "structure_version": 1}),
    ]
    async with db() as session, session.begin():
        stored = await append_events(session, learner, events, publish_wakeup=False)
    store = Neo4jGraphStore(neo4j, str(learner))
    await run_projector_batch(learner, stored, store, FoldParams())

    # Set first topic to displayState='locked' and second topic to displayState='rusty'
    async with neo4j.session() as s:
        await s.run(
            cypher("MATCH (t:Topic {id: $id, learnerId: $l}) SET t.displayState = 'locked'"),
            {"id": str(topic_locked), "l": str(learner)},
        )
        await s.run(
            cypher("MATCH (t:Topic {id: $id, learnerId: $l}) SET t.displayState = 'rusty'"),
            {"id": str(topic_rusty), "l": str(learner)},
        )

    resources: AppResources = session_app.state.resources
    resolved, plan = await lifecycle._resolve_topic_and_plan(
        resources, settings, learner, domain_id, None, None
    )
    assert resolved == topic_rusty
    assert any("Rusty skill" in str(step) for step in plan)

    # Archive `topic_rusty` so only `topic_locked` (displayState='locked', status='active') stays
    async with neo4j.session() as s:
        await s.run(
            cypher("MATCH (t:Topic {id: $id, learnerId: $l}) SET t.status = 'archived'"),
            {"id": str(topic_rusty), "l": str(learner)},
        )

    fallback_resolved, fallback_plan = await lifecycle._resolve_topic_and_plan(
        resources, settings, learner, domain_id, None, None
    )
    assert fallback_resolved == topic_locked
    assert any("Locked skill" in str(step) for step in fallback_plan)
