"""Unit and integration tests for session demo mapping and note/cheatsheet links (Task 2.4)."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from quack.api.app import create_app
from quack.api.ratelimit import enforce_action_rate_limit
from quack.artifacts.documents import Block, SystemBlock, edit_block, get_artifact
from quack.artifacts.hub_cheatsheet import generate_hub_cheatsheet
from quack.artifacts.session_demos import (
    DEMO_BLOCK_HEADING,
    ReadyDemo,
    SessionEvent,
    SkillTopicIndex,
    format_demo_links_content,
    hub_ready_demos,
    merge_demo_links_block,
    resolve_demo_topic,
    topic_ready_demos,
)
from quack.artifacts.topic_refresh import refresh_topic_notes
from quack.compute_client.contracts import RenderPdfResponse
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.graph.ids import deterministic_id
from quack.jobs.runner import JOB_HANDLERS
from quack.jobs.types import ClaimedJob
from tests.artifacts.hubs import create_hub
from tests.artifacts.test_cheatsheet_wp23_5 import PDF, HubReader, Writer, prefetch
from tests.artifacts.test_topic_refresh_wp23 import TopicReader, _prefetch
from tests.db.fixtures import create_learner
from tests.events.samples import sample_payload
from tests.support.auth import session_cookie, session_token
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.graph_read import FixedGraphReader
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources, build_job_context

# --- pure unit tests (no DB) ----------------------------------------------------------------


def _turn_ev(
    seq: int, step_id: str, landmark: dict[str, Any] | None, *, exposures: Sequence[UUID] = ()
) -> SessionEvent:
    return SessionEvent(
        seq=seq,
        type="turn.committed",
        payload={
            "markup": {
                "exposures": [{"skill_id": str(s)} for s in exposures],
                "tasks": [],
                "plan_update": {
                    "goal": "Learn calculus",
                    "current_step": {
                        "id": step_id,
                        "text": f"Step {step_id}",
                        "landmark": landmark,
                    },
                    "next_steps": [],
                    "landmark_edits": None,
                    "branches": [],
                    "cancel_request_ids": [],
                },
            }
        },
    )


def test_resolve_demo_by_topic_and_skill_landmark() -> None:
    session_id = uuid4()
    topic_a, topic_b, topic_b_merged = uuid4(), uuid4(), uuid4()
    skill_b = uuid4()

    index = SkillTopicIndex()
    index.apply("skill.placed", {"skill_id": str(skill_b), "topic_id": str(topic_b)})
    index.apply("topic.merged", {"from_id": str(topic_b), "into_id": str(topic_b_merged)})

    events = [
        SessionEvent(seq=1, type="session.started", payload={"session_id": str(session_id)}),
        _turn_ev(2, "step-topic", {"kind": "topic", "id": str(topic_a)}),
        _turn_ev(4, "step-skill", {"kind": "skill", "id": str(skill_b)}),
    ]
    demo_topic = ReadyDemo(
        request_id=uuid4(),
        seq=3,
        session_id=session_id,
        domain_id=None,
        step_id="step-topic",
        title="Topic demo",
    )
    demo_skill = ReadyDemo(
        request_id=uuid4(),
        seq=5,
        session_id=session_id,
        domain_id=None,
        step_id="step-skill",
        title="Skill demo",
    )
    assert resolve_demo_topic(demo_topic, events, index) == topic_a
    assert resolve_demo_topic(demo_skill, events, index) == topic_b_merged


def test_resolve_demo_free_chat_uses_latest_skill_placed_before_request() -> None:
    session_id = uuid4()
    topic_early, topic_latest, topic_after, session_topic = uuid4(), uuid4(), uuid4(), uuid4()
    skill_1, skill_2, skill_3 = uuid4(), uuid4(), uuid4()

    index = SkillTopicIndex()
    events = [
        SessionEvent(
            seq=1,
            type="session.started",
            payload={"session_id": str(session_id), "topic_id": str(session_topic)},
        ),
        _turn_ev(2, "free-1", None),
        SessionEvent(
            seq=3,
            type="skill.placed",
            payload={"skill_id": str(skill_1), "topic_id": str(topic_early)},
        ),
        SessionEvent(
            seq=4,
            type="skill.placed",
            payload={"skill_id": str(skill_2), "topic_id": str(topic_latest)},
        ),
        # Demo ordered at seq=5 -> must resolve to topic_latest, ignoring seq=6
        SessionEvent(
            seq=6,
            type="skill.placed",
            payload={"skill_id": str(skill_3), "topic_id": str(topic_after)},
        ),
    ]
    demo = ReadyDemo(
        request_id=uuid4(),
        seq=5,
        session_id=session_id,
        domain_id=None,
        step_id="free-1",
        title="Free chat demo",
    )
    assert resolve_demo_topic(demo, events, index) == topic_latest

    # Without any placement before the request, fall back to session.started.topic_id
    demo_early = ReadyDemo(
        request_id=uuid4(),
        seq=2,
        session_id=session_id,
        domain_id=None,
        step_id=None,
        title="Early demo",
    )
    assert resolve_demo_topic(demo_early, events, index) == session_topic


def test_resolve_demo_free_chat_falls_back_to_domain_placement_via_exposed_skill() -> None:
    session_id = uuid4()
    topic_id, skill_id = uuid4(), uuid4()
    index = SkillTopicIndex()
    # Domain-level skill.placed (no session_id in session_events), known via index
    index.apply("skill.placed", {"skill_id": str(skill_id), "topic_id": str(topic_id)})
    events = [
        SessionEvent(seq=1, type="session.started", payload={"session_id": str(session_id)}),
        _turn_ev(2, "free-1", None, exposures=[skill_id]),
    ]
    demo = ReadyDemo(
        request_id=uuid4(),
        seq=3,
        session_id=session_id,
        domain_id=None,
        step_id="free-1",
        title="Exposed skill demo",
    )
    assert resolve_demo_topic(demo, events, index) == topic_id


def test_merge_demo_links_block_preserves_learner_edits_and_is_idempotent() -> None:
    req_1, req_2 = uuid4(), uuid4()
    d1 = ReadyDemo(req_1, 1, None, None, "s1", "Graph [f(x)]")
    d2 = ReadyDemo(req_2, 2, None, None, "s2", "Slope")
    summary_id, existing_demo_id = uuid4(), uuid4()

    content = format_demo_links_content([d1])
    assert content == f"{DEMO_BLOCK_HEADING}\n\n- [Демо: Graph (f(x))](/v1/viz/{req_1})"

    sys_summary = SystemBlock(block_id=summary_id, kind="summary", content="Summary")
    current = (
        Block(block_id=summary_id, kind="summary", content="Summary", owner="system"),
        Block(block_id=existing_demo_id, kind="links", content=content, owner="system"),
    )
    # LLM tried to overwrite the demo block too: our helper replaces it deterministically
    llm_hallucinated = SystemBlock(block_id=existing_demo_id, kind="links", content="Old")
    merged = merge_demo_links_block([sys_summary, llm_hallucinated], current, [d1, d2])
    assert len(merged) == 2
    assert merged[1].block_id == existing_demo_id
    assert f"[Демо: Slope](/v1/viz/{req_2})" in merged[1].content

    # Learner edited the links block: system must not overwrite or attach block_id
    learner_edited = (
        Block(block_id=summary_id, kind="summary", content="Summary", owner="system"),
        Block(block_id=existing_demo_id, kind="links", content="My links", owner="learner"),
    )
    merged_learner = merge_demo_links_block([sys_summary], learner_edited, [d1])
    assert merged_learner[1].block_id is None

    # No demos: returns blocks unchanged
    assert merge_demo_links_block([sys_summary], current, []) == [sys_summary]


# --- integration helpers --------------------------------------------------------------------


async def _append_viz(
    db: Db,
    learner_id: UUID,
    *,
    domain_id: UUID,
    session_id: UUID,
    request_id: UUID,
    step_id: str | None,
    title: str | None,
    ok: bool | None,
    key_suffix: str,
) -> int:
    events = [
        NewEvent(
            type="agent.requested",
            payload={
                "request_id": request_id,
                "kind": "viz",
                "params": {"description": "Plot", "step_id": step_id, "title": title},
                "needed_in_session": False,
            },
            actor="viz_planner@1",
            idempotency_key=f"viz.req:{key_suffix}",
            domain_id=domain_id,
            session_id=session_id,
        )
    ]
    if ok is not None:
        events.append(
            NewEvent(
                type="agent.completed",
                payload={
                    "request_id": request_id,
                    "kind": "viz",
                    "ok": ok,
                    "result_ref": f"learners/{learner_id}/viz/{request_id}.html" if ok else None,
                    "error": None if ok else "viz_check_failed",
                },
                actor="viz_builder@2",
                idempotency_key=f"viz.done:{key_suffix}",
                domain_id=domain_id,
                session_id=session_id,
            )
        )
    stored = await append_events(db, learner_id, events)
    return stored[0].seq


async def _append_plan_turn(
    db: Db,
    learner_id: UUID,
    *,
    domain_id: UUID,
    session_id: UUID,
    step_id: str,
    landmark: dict[str, Any] | None,
    key: str,
) -> int:
    payload = copy.deepcopy(sample_payload("turn.committed"))
    payload["markup"]["plan_update"] = {
        "goal": "Understand limits",
        "current_step": {"id": step_id, "text": f"Step {step_id}", "landmark": landmark},
        "next_steps": [],
        "landmark_edits": None,
        "branches": [],
        "cancel_request_ids": [],
    }
    (ev,) = await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="turn.committed",
                payload=payload,
                actor="teacher@2",
                idempotency_key=key,
                domain_id=domain_id,
                session_id=session_id,
            )
        ],
    )
    return ev.seq


# --- integration tests (pg_db) --------------------------------------------------------------


async def test_refresh_topic_notes_attaches_ready_demos_and_keeps_learner_blocks(
    pg_db: Db, settings: Settings
) -> None:
    learner = await create_learner(pg_db)
    domain_id, topic_id, other_topic = uuid4(), uuid4(), uuid4()
    session_id, free_session_id = uuid4(), uuid4()
    skill_id = uuid4()

    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="topic.created",
                payload={
                    "topic_id": topic_id,
                    "domain_id": domain_id,
                    "name": "Limits",
                    "origin": "session",
                },
                actor="code",
                idempotency_key=f"topic:{topic_id}",
                domain_id=domain_id,
            ),
            NewEvent(
                type="session.started",
                payload={
                    "session_id": session_id,
                    "domain_id": domain_id,
                    "intent": "learn",
                    "topic_id": topic_id,
                },
                actor="learner",
                idempotency_key=f"sess:{session_id}",
                domain_id=domain_id,
                session_id=session_id,
            ),
            NewEvent(
                type="session.started",
                payload={"session_id": free_session_id, "domain_id": domain_id, "intent": "free"},
                actor="learner",
                idempotency_key=f"sess:{free_session_id}",
                domain_id=domain_id,
                session_id=free_session_id,
            ),
        ],
    )
    await _append_plan_turn(
        pg_db,
        learner,
        domain_id=domain_id,
        session_id=session_id,
        step_id="s1",
        landmark={"kind": "topic", "id": str(topic_id)},
        key="t1",
    )
    viz_landmark = uuid4()
    await _append_viz(
        pg_db,
        learner,
        domain_id=domain_id,
        session_id=session_id,
        request_id=viz_landmark,
        step_id="s1",
        title="Epsilon-delta",
        ok=True,
        key_suffix="1",
    )
    # Free-chat session: skill.placed before demo -> maps to topic_id
    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="skill.placed",
                payload={"skill_id": skill_id, "topic_id": topic_id, "domain_id": domain_id},
                actor="builder@1",
                idempotency_key="placed-before",
                domain_id=domain_id,
                session_id=free_session_id,
            )
        ],
    )
    viz_free = uuid4()
    await _append_viz(
        pg_db,
        learner,
        domain_id=domain_id,
        session_id=free_session_id,
        request_id=viz_free,
        step_id=None,
        title="Sequence plot",
        ok=True,
        key_suffix="2",
    )
    # Failed and pending demos must NOT appear; demo after other_topic placement must NOT appear
    await _append_viz(
        pg_db,
        learner,
        domain_id=domain_id,
        session_id=session_id,
        request_id=uuid4(),
        step_id="s1",
        title="Failed",
        ok=False,
        key_suffix="3",
    )
    await _append_viz(
        pg_db,
        learner,
        domain_id=domain_id,
        session_id=session_id,
        request_id=uuid4(),
        step_id="s1",
        title="Pending",
        ok=None,
        key_suffix="4",
    )
    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="skill.placed",
                payload={"skill_id": uuid4(), "topic_id": other_topic, "domain_id": domain_id},
                actor="builder@1",
                idempotency_key="placed-other",
                domain_id=domain_id,
                session_id=free_session_id,
            )
        ],
    )
    viz_other = uuid4()
    await _append_viz(
        pg_db,
        learner,
        domain_id=domain_id,
        session_id=free_session_id,
        request_id=viz_other,
        step_id=None,
        title="Other topic demo",
        ok=True,
        key_suffix="5",
    )

    demos = await topic_ready_demos(pg_db, learner_id=learner, topic_id=topic_id)
    assert [d.request_id for d in demos] == [viz_landmark, viz_free]

    # Learner already wrote a block; refresh keeps it and appends summary + links
    notes = await get_artifact(pg_db, learner_id=learner, kind="notes", owner_id=topic_id)
    mine = uuid4()
    await edit_block(
        pg_db,
        learner_id=learner,
        artifact_id=notes.artifact_id,
        block_id=mine,
        content="My note",
        idempotency_key="mine-1",
    )

    provider = ScriptedLLMProvider()
    provider.respond(
        "ArtifactWriterOutput",
        lambda _r: {
            "blocks": [
                {
                    "updates": None,
                    "kind": "summary",
                    "content": "Key idea",
                    "sources": [],
                    "skills": [],
                }
            ],
            "cards": [],
        },
    )
    resources = build_app_resources(settings, None, None, db=pg_db, provider=provider)
    reader = TopicReader(topic_id, 1)

    v = await refresh_topic_notes(
        resources.db,
        resources.llm,
        reader,
        _prefetch,
        settings,
        learner_id=learner,
        topic_id=topic_id,
        key="job-1",
    )
    assert v == 2
    saved = await get_artifact(pg_db, learner_id=learner, kind="notes", owner_id=topic_id)
    assert saved.blocks[0].owner == "learner" and saved.blocks[0].content == "My note"
    assert saved.blocks[1].kind == "summary" and saved.blocks[1].content == "Key idea"
    links_block = saved.blocks[2]
    assert links_block.kind == "links" and links_block.owner == "system"
    assert f"[Демо: Epsilon-delta](/v1/viz/{viz_landmark})" in links_block.content
    assert f"[Демо: Sequence plot](/v1/viz/{viz_free})" in links_block.content
    assert str(viz_other) not in links_block.content

    # Repeat with same key writes no new version
    v_again = await refresh_topic_notes(
        resources.db,
        resources.llm,
        reader,
        _prefetch,
        settings,
        learner_id=learner,
        topic_id=topic_id,
        key="job-1",
    )
    assert v_again == 2


async def test_refresh_topic_notes_writes_demos_even_when_graph_unknown(
    pg_db: Db, settings: Settings
) -> None:
    learner = await create_learner(pg_db)
    domain_id, topic_id = uuid4(), uuid4()
    session_id = uuid4()
    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="topic.created",
                payload={
                    "topic_id": topic_id,
                    "domain_id": domain_id,
                    "name": "New",
                    "origin": "session",
                },
                actor="code",
                idempotency_key=f"t:{topic_id}",
                domain_id=domain_id,
            ),
            NewEvent(
                type="session.started",
                payload={
                    "session_id": session_id,
                    "domain_id": domain_id,
                    "intent": "learn",
                    "topic_id": topic_id,
                },
                actor="learner",
                idempotency_key=f"s:{session_id}",
                domain_id=domain_id,
                session_id=session_id,
            ),
        ],
    )
    viz_id = uuid4()
    await _append_viz(
        pg_db,
        learner,
        domain_id=domain_id,
        session_id=session_id,
        request_id=viz_id,
        step_id=None,
        title="Fresh demo",
        ok=True,
        key_suffix="fresh",
    )
    provider = ScriptedLLMProvider()
    resources = build_app_resources(settings, None, None, db=pg_db, provider=provider)
    version = await refresh_topic_notes(
        resources.db,
        resources.llm,
        FixedGraphReader(),
        _prefetch,
        settings,
        learner_id=learner,
        topic_id=topic_id,
        key="job-new",
    )
    assert version == 1
    assert provider.calls == []
    notes = await get_artifact(pg_db, learner_id=learner, kind="notes", owner_id=topic_id)
    assert [b.kind for b in notes.blocks] == ["links"]
    assert f"[Демо: Fresh demo](/v1/viz/{viz_id})" in notes.blocks[0].content


async def test_hub_cheatsheet_includes_all_session_and_topic_demos(
    pg_db: Db, settings: Settings
) -> None:
    learner = await create_learner(pg_db)
    hub_id, domain_id = await create_hub(pg_db, learner)
    free_session, other_session = uuid4(), uuid4()

    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="session.started",
                payload={"session_id": free_session, "domain_id": domain_id, "intent": "free"},
                actor="learner",
                idempotency_key=f"s:{free_session}",
                domain_id=domain_id,
                session_id=free_session,
            ),
            NewEvent(
                type="session.started",
                payload={"session_id": other_session, "domain_id": domain_id, "intent": "learn"},
                actor="learner",
                idempotency_key=f"s:{other_session}",
                domain_id=domain_id,
                session_id=other_session,
            ),
        ],
    )
    # Demo 1: free-chat session demo with NO landmark and NO topic at all
    viz_free = uuid4()
    await _append_viz(
        pg_db,
        learner,
        domain_id=domain_id,
        session_id=free_session,
        request_id=viz_free,
        step_id=None,
        title="Free session demo",
        ok=True,
        key_suffix="h1",
    )
    # Demo 2: another session, targeting a topic inside HubReader
    from tests.artifacts.test_cheatsheet_wp23_5 import TOPIC as HUB_TOPIC

    await _append_plan_turn(
        pg_db,
        learner,
        domain_id=domain_id,
        session_id=other_session,
        step_id="s-hub",
        landmark={"kind": "topic", "id": str(HUB_TOPIC)},
        key="turn-hub",
    )
    viz_topic = uuid4()
    await _append_viz(
        pg_db,
        learner,
        domain_id=domain_id,
        session_id=other_session,
        request_id=viz_topic,
        step_id="s-hub",
        title="Hub topic demo",
        ok=True,
        key_suffix="h2",
    )

    demos = await hub_ready_demos(
        pg_db,
        learner_id=learner,
        hub_id=hub_id,
        domain_id=domain_id,
        hub_topic_ids=[HUB_TOPIC],
        is_domain_all=False,
        session_id=free_session,
    )
    assert {d.request_id for d in demos} == {viz_free, viz_topic}

    provider = ScriptedLLMProvider()
    Writer(provider)
    scenario = ComputeScenario()
    scenario.push_for(
        PDF,
        RenderPdfResponse(pdf_b64="", page_count=2),
        RenderPdfResponse(pdf_b64="", page_count=1),
    )
    resources = build_app_resources(
        settings,
        None,
        None,
        db=pg_db,
        provider=provider,
        compute=scripted_compute(scenario),
    )
    res = await generate_hub_cheatsheet(
        resources.db,
        resources.llm,
        resources.compute,
        HubReader(hub_id),
        prefetch,
        settings,
        learner_id=learner,
        hub_id=hub_id,
        key="req-hub-1",
        session_id=free_session,
    )
    assert res.fits is True and res.version == 2
    sheet = await get_artifact(pg_db, learner_id=learner, kind="cheatsheet", owner_id=hub_id)
    links = [b for b in sheet.blocks if b.kind == "links"]
    assert len(links) == 1
    assert f"[Демо: Free session demo](/v1/viz/{viz_free})" in links[0].content
    assert f"[Демо: Hub topic demo](/v1/viz/{viz_topic})" in links[0].content


async def test_notes_and_cheatsheet_routes_accept_optional_session_id(
    pg_db: Db, settings: Settings
) -> None:
    """`POST /v1/topics/{id}/notes` and `POST /v1/hubs/{id}/cheatsheet` accept optional
    `session_id` and record it on `agent.requested`. Also verifies `hub_cheatsheet` currently
    does not take free-form session dialogue unless `domain_goals` / `topic_memory` exist."""
    learner = await create_learner(pg_db)
    domain_id, topic_id, session_id = uuid4(), uuid4(), uuid4()
    [dom_ev] = await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="domain.created",
                payload={"domain_id": domain_id, "name": "Math"},
                actor="learner",
                idempotency_key=f"dom:{domain_id}",
            )
        ],
    )
    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="topic.created",
                payload={
                    "topic_id": topic_id,
                    "domain_id": domain_id,
                    "name": "Derivatives",
                    "origin": "session",
                },
                actor="code",
                idempotency_key=f"top:{topic_id}",
                domain_id=domain_id,
            )
        ],
    )
    domain_all_hub = UUID(deterministic_id(learner, dom_ev.seq, 0))

    app = create_app(settings)
    app.state.resources = build_app_resources(settings, None, None, db=pg_db)
    app.dependency_overrides[enforce_action_rate_limit] = lambda: None
    cookie = session_cookie(await session_token(pg_db, learner, settings))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=cookie
    ) as client:
        r_notes = await client.post(
            f"/v1/topics/{topic_id}/notes",
            json={"idempotency_key": "notes-sess-1", "session_id": str(session_id)},
        )
        assert r_notes.status_code == 202, r_notes.text
        r_cheat = await client.post(
            f"/v1/hubs/{domain_all_hub}/cheatsheet",
            json={"idempotency_key": "cheat-sess-1", "session_id": str(session_id)},
        )
        assert r_cheat.status_code == 202, r_cheat.text

    rows = await pg_db.fetch_all(
        "SELECT session_id, payload FROM core.events WHERE learner_id = %(l)s "
        "AND type = 'agent.requested' ORDER BY seq",
        {"l": learner},
    )
    assert len(rows) == 2
    assert all(row["session_id"] == session_id for row in rows)
    assert rows[0]["payload"]["params"]["artifact"] == "notes"
    assert rows[1]["payload"]["params"]["artifact"] == "cheatsheet"


async def test_cheatsheet_job_handler_delivers_completed_with_session(
    pg_db: Db, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    learner = await create_learner(pg_db)
    hub_id, _ = await create_hub(pg_db, learner)
    session_id, request_id = uuid4(), uuid4()
    provider = ScriptedLLMProvider()
    Writer(provider)
    scenario = ComputeScenario()
    scenario.push_for(PDF, RenderPdfResponse(pdf_b64="", page_count=1))

    async def fake_prefetch(*_args: Any, skill_ids: Any = (), **_kwargs: Any) -> list[Any]:
        return await prefetch(skill_ids)

    monkeypatch.setattr(
        "quack.artifacts.hub_cheatsheet.create_graph_reader",
        lambda *_args, **_kwargs: HubReader(hub_id),
    )
    monkeypatch.setattr("quack.artifacts.hub_cheatsheet.materials_prefetch", fake_prefetch)
    ctx = build_job_context(
        settings,
        None,
        None,
        db=pg_db,
        provider=provider,
        compute=scripted_compute(scenario),
    )
    job = ClaimedJob(
        id=uuid4(),
        kind="artifact.cheatsheet",
        learner_id=learner,
        session_id=session_id,
        lock_key=f"artifact:{hub_id}",
        coalesce_key=None,
        priority=2,
        attempt=1,
        max_attempts=5,
        payload={"hub_id": str(hub_id), "request_id": str(request_id)},
    )
    await JOB_HANDLERS["artifact.cheatsheet"](job, ctx)
    rows = await pg_db.fetch_all(
        "SELECT session_id, payload FROM core.events WHERE learner_id = %(l)s "
        "AND type = 'agent.completed'",
        {"l": learner},
    )
    assert len(rows) == 1
    assert rows[0]["session_id"] == session_id
    assert rows[0]["payload"]["ok"] is True
    assert rows[0]["payload"]["request_id"] == str(request_id)
