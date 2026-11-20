"""ТЗ Ф4 WP13.10 (D-B-210): the session routes over `Db` — start, list, active, feed, heartbeat —
and the sweep of inactive sessions (the only way a session closes; there is no `/end`). The
library is checked in `test_sessions_db.py`; here the HTTP codes, the isolation of learners and
the presence in Redis."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import httpx
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.redis import presence_key
from quack.jobs.types import ClaimedJob
from quack.session.lifecycle import sweep_inactive_sessions
from quack.session.sessions import end_if_inactive
from tests.session.helpers import Learner, make_learner
from tests.support.resources import build_job_context

pytest_plugins = ["tests.session.fixtures"]


def _body(who: Learner, key: str, **extra: object) -> dict[str, object]:
    return {"domain_id": str(who.domain_id), "intent": "learn", "idempotency_key": key, **extra}


async def _start(client: httpx.AsyncClient, who: Learner, **extra: object) -> str:
    response = await client.post(
        "/v1/sessions", json=_body(who, str(uuid4()), **extra), cookies=who.cookies
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


async def test_start_is_idempotent_and_checks_the_domain(
    client: httpx.AsyncClient, learner: Learner, pg_db: Db, settings: Settings
) -> None:
    key = str(uuid4())
    first = await client.post("/v1/sessions", json=_body(learner, key), cookies=learner.cookies)
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "active"
    again = await client.post("/v1/sessions", json=_body(learner, key), cookies=learner.cookies)
    assert again.json()["session_id"] == first.json()["session_id"]
    other = await client.post(
        "/v1/sessions", json=_body(learner, key, intent="practice"), cookies=learner.cookies
    )
    assert other.status_code == 409
    assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    stranger = await make_learner(pg_db, settings)  # a domain of another learner is not found
    foreign = await client.post(
        "/v1/sessions", json=_body(stranger, str(uuid4())), cookies=learner.cookies
    )
    assert foreign.status_code == 404


async def test_list_active_and_feed_of_own_sessions(
    client: httpx.AsyncClient, learner: Learner, pg_db: Db, settings: Settings
) -> None:
    first, second = await _start(client, learner), await _start(client, learner)
    stranger = await make_learner(pg_db, settings)
    theirs = await _start(client, stranger)

    listed = await client.get("/v1/sessions", cookies=learner.cookies)
    assert [s["session_id"] for s in listed.json()["sessions"]] == [second, first]
    limited = await client.get("/v1/sessions?limit=1", cookies=learner.cookies)
    assert [s["session_id"] for s in limited.json()["sessions"]] == [second]
    assert (await client.get("/v1/sessions?limit=0", cookies=learner.cookies)).status_code == 422
    other_domain = await client.get(f"/v1/sessions?domain_id={uuid4()}", cookies=learner.cookies)
    assert other_domain.json()["sessions"] == []

    active = await client.get("/v1/sessions/active", cookies=learner.cookies)
    assert active.json()["session_id"] == second
    detail = await client.get(f"/v1/sessions/{first}", cookies=learner.cookies)
    assert [e["type"] for e in detail.json()["events"]] == ["session.started"]
    assert (await client.get(f"/v1/sessions/{theirs}", cookies=learner.cookies)).status_code == 404


async def test_heartbeat_sets_presence_and_brings_a_closed_session_back(
    client: httpx.AsyncClient,
    learner: Learner,
    pg_db: Db,
    redis: Redis,
    settings: Settings,
) -> None:
    session_id = await _start(client, learner)
    await redis.delete(presence_key(learner.learner_id))
    beat = await client.post(f"/v1/sessions/{session_id}/heartbeat", cookies=learner.cookies)
    assert beat.json() == {"status": "ok", "ttl_sec": settings.heartbeat_ttl_sec}
    assert await redis.get(presence_key(learner.learner_id)) == session_id

    late = datetime.now(UTC) + timedelta(minutes=settings.session_inactivity_timeout_min + 1)
    assert await end_if_inactive(
        pg_db,
        learner_id=learner.learner_id,
        session_id=UUID(session_id),
        settings=settings,
        now=late,
    )
    assert (await client.get("/v1/sessions/active", cookies=learner.cookies)).json() is None
    await redis.delete(presence_key(learner.learner_id))

    back = await client.post(f"/v1/sessions/{session_id}/heartbeat", cookies=learner.cookies)
    assert back.status_code == 200, back.text
    assert back.json() == {"status": "ok", "ttl_sec": settings.heartbeat_ttl_sec}
    assert await redis.get(presence_key(learner.learner_id)) == session_id
    active = (await client.get("/v1/sessions/active", cookies=learner.cookies)).json()
    assert active["session_id"] == session_id and active["status"] == "active"
    detail = await client.get(f"/v1/sessions/{session_id}", cookies=learner.cookies)
    assert [e["type"] for e in detail.json()["events"]] == [
        "session.started",
        "session.ended",
        "session.started",
    ]


async def test_resuming_an_older_session_does_not_displace_the_newer_open_one(
    client: httpx.AsyncClient, learner: Learner, pg_db: Db, settings: Settings
) -> None:
    """Two sessions: the older one is closed by the sweep, the newer stays open; a heartbeat in
    the older one opens it again, and the panel's «active» session is still the newer one
    (`GET /v1/sessions/active` orders by the start, a resume keeps `started_at`)."""
    older, newer = await _start(client, learner), await _start(client, learner)
    late = datetime.now(UTC) + timedelta(minutes=settings.session_inactivity_timeout_min + 1)
    assert await end_if_inactive(
        pg_db,
        learner_id=learner.learner_id,
        session_id=UUID(older),
        settings=settings,
        now=late,
    )

    back = await client.post(f"/v1/sessions/{older}/heartbeat", cookies=learner.cookies)

    assert back.status_code == 200, back.text
    statuses = {
        s["session_id"]: s["status"]
        for s in (await client.get("/v1/sessions", cookies=learner.cookies)).json()["sessions"]
    }
    assert statuses == {older: "active", newer: "active"}
    active = (await client.get("/v1/sessions/active", cookies=learner.cookies)).json()
    assert active["session_id"] == newer


async def test_heartbeat_of_a_foreign_or_unknown_session_is_404(
    client: httpx.AsyncClient, learner: Learner, pg_db: Db, settings: Settings
) -> None:
    stranger = await make_learner(pg_db, settings)
    theirs = await _start(client, stranger)
    foreign = await client.post(f"/v1/sessions/{theirs}/heartbeat", cookies=learner.cookies)
    assert foreign.status_code == 404
    unknown = await client.post(f"/v1/sessions/{uuid4()}/heartbeat", cookies=learner.cookies)
    assert unknown.status_code == 404


async def test_sweep_ends_only_the_inactive_sessions(
    learner: Learner,
    client: httpx.AsyncClient,
    pg_db: Db,
    settings: Settings,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    stale, fresh = await _start(client, learner), await _start(client, learner)
    await pg_db.execute(
        "UPDATE content.sessions SET last_activity_at = now() - interval '1 day' "
        "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s",
        {"learner_id": learner.learner_id, "session_id": stale},
    )
    ctx = build_job_context(settings, neo4j, redis, db=pg_db)
    await sweep_inactive_sessions(cast(ClaimedJob, None), ctx)
    await sweep_inactive_sessions(cast(ClaimedJob, None), ctx)  # a repeat changes nothing

    statuses = {
        s["session_id"]: s["status"]
        for s in (await client.get("/v1/sessions", cookies=learner.cookies)).json()["sessions"]
    }
    assert statuses == {stale: "ended", fresh: "active"}
    ended = await pg_db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'session.ended'",
        {"learner_id": learner.learner_id},
    )
    assert [e["payload"]["reason"] for e in ended] == ["inactive"]


async def test_session_feed_links_element_responses_and_verdicts_without_order_guessing(
    client: httpx.AsyncClient, learner: Learner, pg_db: Db
) -> None:
    """`GET /v1/sessions/{id}` populates `causation_seq`, `turn_seq`, `element_id` and `outcome`
    on feed events so reload matches responses and verdicts (including delayed verdicts) to
    their exact turn and element even when two turns reuse `e1`."""
    from quack.events import records
    from quack.events.log import NewEvent, append_events
    from quack.events.values import AnswerSpec, MathValue, Verdict
    from quack.practice.role_outputs import TaskCondition
    from quack.practice.task_store import create_task
    from quack.session.actions import ElementResponseAction
    from quack.session.record import record_action

    session_id = UUID(await _start(client, learner))
    num_spec = AnswerSpec(type="number", value=MathValue(latex="1/2", sympy="1/2"))
    free_spec = AnswerSpec(type="free_text", key_ideas=["converges"])
    t1_task, t2_task = uuid4(), uuid4()
    for task_id, spec in ((t1_task, free_spec), (t2_task, num_spec)):
        await create_task(
            pg_db,
            learner_id=learner.learner_id,
            task_id=task_id,
            family_id=task_id,
            source="teacher",
            condition=TaskCondition(text="Solve", latex=None),
            markup=records.TaskMarkupRecord(
                tests=[records.TestedLevelRecord(skill_id=uuid4(), level=2)],
                method_named=False,
                answer=spec,
            ),
            verification="unchecked",
            actor="teacher@3",
            idempotency_key=f"task:{task_id}",
        )

    def _turn_payload(task_id: UUID, spec: AnswerSpec, action_seq: int) -> dict[str, object]:
        markup = records.TaskMarkupRecord(
            tests=[records.TestedLevelRecord(skill_id=uuid4(), level=2)],
            method_named=False,
            answer=spec,
        )
        return {
            "action_seq": action_seq,
            "text": "Try this.",
            "blocks": [{"block_id": "b1", "text": None, "element_id": "e1"}],
            "head": {
                "intent": "practice",
                "target_skill_ids": [],
                "verdict_stance": None,
                "branch_type": None,
                "approach": "none",
            },
            "markup": {
                "tasks": [
                    {**markup.model_dump(mode="json"), "element_id": "e1", "task_id": str(task_id)}
                ],
                "elements": [],
                "exposures": [],
                "targets_misconception_id": None,
                "plan_update": {
                    "goal": None,
                    "current_step": None,
                    "next_steps": None,
                    "landmark_edits": None,
                    "branches": [],
                    "cancel_request_ids": [],
                },
                "agent_requests": [],
                "corrections_acknowledged": [],
                "decision_reason": "r",
                "deadline": None,
            },
            "role": "teacher",
            "model": "m",
            "effort": "low",
            "degraded_mode": None,
            "guard_retries": 0,
            "manifest_id": str(uuid4()),
        }

    [turn1, turn2] = await append_events(
        pg_db,
        learner.learner_id,
        [
            NewEvent(
                type="turn.committed",
                payload=_turn_payload(t1_task, free_spec, 1),
                actor="teacher@3",
                idempotency_key=f"turn1:{uuid4()}",
                session_id=session_id,
            ),
            NewEvent(
                type="turn.committed",
                payload=_turn_payload(t2_task, num_spec, 2),
                actor="teacher@3",
                idempotency_key=f"turn2:{uuid4()}",
                session_id=session_id,
            ),
        ],
    )

    resp1 = await record_action(
        pg_db,
        ElementResponseAction(
            kind="element_response",
            idempotency_key=uuid4(),
            turn_seq=turn1.seq,
            element_id="e1",
            response="because",
        ),
        learner_id=learner.learner_id,
        session_id=session_id,
        domain_id=learner.domain_id,
        settings=Settings(),
    )
    resp2 = await record_action(
        pg_db,
        ElementResponseAction(
            kind="element_response",
            idempotency_key=uuid4(),
            turn_seq=turn2.seq,
            element_id="e1",
            response="0.5",
        ),
        learner_id=learner.learner_id,
        session_id=session_id,
        domain_id=learner.domain_id,
        settings=Settings(),
    )
    # Slow verdict for turn 1 arrives AFTER turn 2's answer and verdict.
    await append_events(
        pg_db,
        learner.learner_id,
        [
            NewEvent(
                type="verdict.recorded",
                payload=Verdict(
                    answer_seq=resp1.seq + 1,
                    part="main",
                    outcome="incorrect",
                    method="model",
                    verification="model",
                ).model_dump(mode="json"),
                actor="code",
                idempotency_key=f"verdict:{resp1.seq + 1}:main",
                domain_id=learner.domain_id,
                session_id=session_id,
                causation_seq=resp1.seq + 1,
            )
        ],
    )

    detail = (await client.get(f"/v1/sessions/{session_id}", cookies=learner.cookies)).json()
    events = detail["events"]
    responded = {e["seq"]: e for e in events if e["type"] == "element.responded"}
    answers = {e["seq"]: e for e in events if e["type"] == "answer.submitted"}
    verdicts = [e for e in events if e["type"] == "verdict.recorded"]

    assert responded[resp1.seq]["turn_seq"] == turn1.seq
    assert responded[resp1.seq]["element_id"] == "e1"
    assert responded[resp1.seq]["outcome"] == "incorrect"

    assert responded[resp2.seq]["turn_seq"] == turn2.seq
    assert responded[resp2.seq]["element_id"] == "e1"
    assert responded[resp2.seq]["outcome"] == "correct"

    assert answers[resp1.seq + 1]["causation_seq"] == resp1.seq
    assert answers[resp1.seq + 1]["turn_seq"] == turn1.seq
    assert answers[resp1.seq + 1]["element_id"] == "e1"

    assert answers[resp2.seq + 1]["causation_seq"] == resp2.seq
    assert answers[resp2.seq + 1]["turn_seq"] == turn2.seq
    assert answers[resp2.seq + 1]["element_id"] == "e1"

    by_turn = {(v["turn_seq"], v["element_id"]): v["outcome"] for v in verdicts}
    assert by_turn == {(turn1.seq, "e1"): "incorrect", (turn2.seq, "e1"): "correct"}
