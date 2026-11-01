"""Session finish (`POST /v1/sessions/{id}/finish`) and sweep (ТЗ §2.2.3-2.2.4)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import orjson
from redis.asyncio import Redis
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import presence_key
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.jobs import kinds
from quack.jobs.queue import ClaimedJob, enqueue_job
from quack.jobs.runner import JOB_HANDLERS, JobRunner
from quack.session import lifecycle
from tests.session.fixtures import SeededDomain
from tests.session.helpers import make_token
from tests.support.dispatch import dispatch, mark_dispatched
from tests.support.resources import build_job_context

pytest_plugins = ["tests.session.fixtures"]

FINISH_FIELDS = {"session_id", "status", "summary_stats", "end_seq"}
STATS_FIELDS = {"turns_count", "tokens_used", "duration_sec"}


async def _start(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    seed: SeededDomain,
    key: str,
    **body: Any,
) -> httpx.Response:
    payload = {"domain_id": str(seed.domain_id), "idempotency_key": key, **body}
    return await client.post("/v1/sessions", json=payload, headers=headers)


async def _events(db: SessionFactory, learner: UUID) -> list[dict[str, Any]]:
    async with db() as session:
        rows = await session.execute(
            text(
                "SELECT seq, type, actor, session_id, domain_id, idempotency_key, payload "
                "FROM core.events WHERE learner_id = :l ORDER BY seq"
            ),
            {"l": learner},
        )
        return [dict(r._mapping) for r in rows]


async def _session_row(db: SessionFactory, session_id: UUID) -> dict[str, Any]:
    async with db() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, learner_id, domain_id, topic_id, mode, status, "
                    "started_at, last_activity_at, ended_at, start_seq, end_seq "
                    "FROM core.sessions WHERE id = :id"
                ),
                {"id": session_id},
            )
        ).one()
        return dict(row._mapping)


async def _jobs(db: SessionFactory) -> list[dict[str, Any]]:
    async with db() as session:
        rows = await session.execute(
            text(
                "SELECT id, kind, learner_id, lock_key, coalesce_key, priority, status, payload "
                "FROM ops.jobs ORDER BY priority, created_at"
            )
        )
        return [dict(r._mapping) for r in rows]


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    p = row["payload"]
    return orjson.loads(p) if isinstance(p, str | bytes) else dict(p)


async def test_finish_session_writes_completed_enqueues_jobs_deletes_presence_and_is_idempotent(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    redis: Redis,
    settings: Settings,
) -> None:
    seed = seeded_domain
    learner = seed.learner_id

    started = await _start(
        client, auth_headers, seed, "start-fin-1", topic_id=str(seed.topic_id), goal="chain rule"
    )
    assert started.status_code == 201, started.text
    start_body = started.json()
    session_id = UUID(start_body["session_id"])
    start_seq = int(start_body["start_seq"])
    assert await redis.get(presence_key(learner)) == str(session_id)

    # Seed two student/teacher exchanges (turn_index 0..3), 420 tokens and 125s elapsed duration.
    turn1, turn2 = uuid4(), uuid4()
    turn_events = [
        NewEvent(
            type="turn.student_replied",
            payload={
                "session_id": str(session_id),
                "turn_id": str(turn1),
                "turn_index": 0,
                "text": "How does the power rule work?",
            },
            actor="learner",
            idempotency_key="turn-s-0",
        ),
        NewEvent(
            type="turn.teacher_replied",
            payload={
                "session_id": str(session_id),
                "turn_id": str(turn1),
                "turn_index": 1,
                "text": "Multiply by the exponent and subtract 1.",
                "pedagogical_move": "explain",
            },
            actor="teacher@1",
            idempotency_key="turn-t-1",
        ),
        NewEvent(
            type="turn.student_replied",
            payload={
                "session_id": str(session_id),
                "turn_id": str(turn2),
                "turn_index": 2,
                "text": "So d/dx x^3 is 3x^2?",
            },
            actor="learner",
            idempotency_key="turn-s-2",
        ),
        NewEvent(
            type="turn.teacher_replied",
            payload={
                "session_id": str(session_id),
                "turn_id": str(turn2),
                "turn_index": 3,
                "text": "Exactly right.",
                "pedagogical_move": "give_feedback",
            },
            actor="teacher@1",
            idempotency_key="turn-t-3",
        ),
    ]
    async with db() as session, session.begin():
        await append_events(session, learner, turn_events, publish_wakeup=False)
        await session.execute(
            text(
                "UPDATE core.sessions SET tokens_used = 420, "
                "started_at = now() - interval '125 seconds' WHERE id = :id"
            ),
            {"id": session_id},
        )
        await session.execute(
            text(
                "UPDATE core.events SET occurred_at = now() - interval '125 seconds' "
                "WHERE learner_id = :l AND session_id = :s AND type = 'session.started'"
            ),
            {"l": learner, "s": session_id},
        )
        await session.execute(
            text(
                "INSERT INTO ops.llm_ledger (id, learner_id, session_id, turn_id, role, "
                "prompt_version, prompt_hash, model, input_tokens, output_tokens, cached_tokens, "
                "latency_ms, status, error_code, created_at) VALUES (:id, :l, :s, :t, 'teacher', "
                "1, 'hash', 'muse-wonder', 300, 120, 0, 50, 'ok', NULL, now())"
            ),
            {"id": uuid4(), "l": learner, "s": session_id, "t": turn1},
        )

    # The turns above went straight into the log, not through the turn endpoint: their jobs are
    # not this test's subject (WP11.4, §8.9 п.7).
    await mark_dispatched(db, learner)

    # Foreign learner or unknown session id -> 404 NOT_FOUND, leaving the session active.
    stranger = uuid4()
    stranger_headers = {"Authorization": f"Bearer {make_token(settings, stranger)}"}
    for headers, sid in ((stranger_headers, str(session_id)), (auth_headers, str(uuid4()))):
        not_found = await client.post(
            f"/v1/sessions/{sid}/finish",
            json={"idempotency_key": "finish-nf"},
            headers=headers,
        )
        assert not_found.status_code == 404, not_found.text
        assert not_found.json()["error"]["code"] == "NOT_FOUND"
    assert await redis.get(presence_key(learner)) == str(session_id)

    # Finish the session.
    fin = await client.post(
        f"/v1/sessions/{session_id}/finish",
        json={"idempotency_key": "finish-1"},
        headers=auth_headers,
    )
    assert fin.status_code == 200, fin.text
    body = fin.json()
    assert set(body) == FINISH_FIELDS
    assert body["session_id"] == str(session_id)
    assert body["status"] == "completed"
    end_seq = int(body["end_seq"])
    assert end_seq == start_seq + 5

    stats = body["summary_stats"]
    assert isinstance(stats, dict)
    assert set(stats) >= STATS_FIELDS
    assert stats["turns_count"] in (2, 4)
    assert stats["tokens_used"] == 420
    assert isinstance(stats["duration_sec"], int | float)
    assert 110 <= stats["duration_sec"] <= 150

    # Presence key deleted in Redis.
    assert await redis.get(presence_key(learner)) is None

    # Event `session.completed` appended with the exact summary_stats and end_seq.
    events = await _events(db, learner)
    completed = [e for e in events if e["type"] == "session.completed"]
    assert len(completed) == 1
    assert completed[0]["seq"] == end_seq
    assert completed[0]["session_id"] == session_id
    assert completed[0]["domain_id"] == seed.domain_id
    assert _payload(completed[0]) == {
        "session_id": str(session_id),
        "domain_id": str(seed.domain_id),
        "summary_stats": stats,
    }

    # Relational projection updated `core.sessions` to `completed` at `end_seq`.
    row = await _session_row(db, session_id)
    assert row["status"] == "completed"
    assert row["end_seq"] == end_seq
    assert row["ended_at"] is not None

    active = await client.get("/v1/sessions/active", headers=auth_headers)
    assert active.status_code == 200, active.text
    assert active.json() is None

    detail = await client.get(f"/v1/sessions/{session_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "completed"

    await dispatch(settings, db, seeded_domain.learner_id)  # §8.9 п.7 (WP11.4)
    # Both `session.finish_artifacts` and `graph.curate` are queued in `ops.jobs`.
    jobs = await _jobs(db)
    assert [j["kind"] for j in jobs] == ["session.finish_artifacts", "graph.curate"]

    fin_job, cur_job = jobs
    assert fin_job["learner_id"] == learner
    assert fin_job["lock_key"] == f"session:{session_id}:finish"
    assert fin_job["coalesce_key"] == f"finish:{session_id}"
    assert fin_job["priority"] == 30
    assert fin_job["status"] == "queued"
    fin_payload = _payload(fin_job)
    assert fin_payload["domain_id"] == str(seed.domain_id)
    assert fin_payload["topic_id"] == str(seed.topic_id)

    assert cur_job["learner_id"] == learner
    assert cur_job["lock_key"] == f"learner:{learner}:curate"
    assert cur_job["coalesce_key"] == f"curate:{seed.domain_id}"
    assert cur_job["priority"] == 80
    assert cur_job["status"] == "queued"
    cur_payload = _payload(cur_job)
    assert cur_payload["domain_id"] == str(seed.domain_id)

    # Mutate `core.sessions` counters after completion to verify idempotent finish returns stored
    # stats rather than recomputing or appending a duplicate `session.completed`.
    async with db() as session, session.begin():
        await session.execute(
            text(
                "UPDATE core.sessions SET tokens_used = 9999, "
                "started_at = now() - interval '999 seconds' WHERE id = :id"
            ),
            {"id": session_id},
        )

    for replay_key in ("finish-1", "finish-2"):
        again = await client.post(
            f"/v1/sessions/{session_id}/finish",
            json={"idempotency_key": replay_key},
            headers=auth_headers,
        )
        assert again.status_code == 200, again.text
        assert again.json() == body

    assert len([e for e in await _events(db, learner) if e["type"] == "session.completed"]) == 1
    assert len(await _jobs(db)) == 2


async def test_finish_session_without_topic_or_turns_sets_null_topic_and_zero_stats(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    settings: Settings,
) -> None:
    seed = seeded_domain
    started = await _start(client, auth_headers, seed, "start-notopic", mode="practice")
    assert started.status_code == 201, started.text
    session_id = UUID(started.json()["session_id"])

    fin = await client.post(
        f"/v1/sessions/{session_id}/finish",
        json={"idempotency_key": "finish-notopic"},
        headers=auth_headers,
    )
    assert fin.status_code == 200, fin.text
    body = fin.json()
    stats = body["summary_stats"]
    assert stats["turns_count"] == 0
    assert stats["tokens_used"] == 0
    assert 0 <= stats["duration_sec"] < 30

    await dispatch(settings, db, seeded_domain.learner_id)  # §8.9 п.7 (WP11.4)
    jobs = {j["kind"]: j for j in await _jobs(db)}
    assert set(jobs) == {"session.finish_artifacts", "graph.curate"}
    fin_payload = _payload(jobs["session.finish_artifacts"])
    assert fin_payload["domain_id"] == str(seed.domain_id)
    assert fin_payload["topic_id"] == str(seed.topic_id)


async def test_sweep_inactive_abandons_only_expired_active_sessions_and_is_idempotent(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    settings: Settings,
) -> None:
    assert JOB_HANDLERS.get("session.sweep_inactive") is lifecycle.sweep_inactive_sessions

    seed = seeded_domain
    learner1 = seed.learner_id
    resp = await _start(client, auth_headers, seed, "start-stale-1", topic_id=str(seed.topic_id))
    assert resp.status_code == 201, resp.text
    stale1_id = UUID(resp.json()["session_id"])

    # Create a second stale active session, a fresh active session, and an already completed one.
    learner2, domain2, stale2_id = uuid4(), uuid4(), uuid4()
    learner3, domain3, fresh_id = uuid4(), uuid4(), uuid4()
    learner4, domain4, completed_id = uuid4(), uuid4(), uuid4()

    async with db() as session, session.begin():
        for lrn, dom, sid, key_suffix in (
            (learner2, domain2, stale2_id, "2"),
            (learner3, domain3, fresh_id, "3"),
            (learner4, domain4, completed_id, "4"),
        ):
            await append_events(
                session,
                lrn,
                [
                    NewEvent(
                        type="domain.created",
                        payload={
                            "domain_id": str(dom),
                            "name": "Math",
                            "slug": f"math-{key_suffix}",
                        },
                        actor="learner",
                        idempotency_key=f"dom-{key_suffix}",
                    ),
                    NewEvent(
                        type="session.started",
                        payload={
                            "session_id": str(sid),
                            "domain_id": str(dom),
                            "topic_id": None,
                            "mode": "learn",
                            "goal": None,
                            "plan": [{"step": 0, "kind": "learn", "title": "Intro"}],
                        },
                        actor="learner",
                        idempotency_key=f"start-{key_suffix}",
                    ),
                ],
                publish_wakeup=False,
            )
        await append_events(
            session,
            learner4,
            [
                NewEvent(
                    type="session.completed",
                    payload={
                        "session_id": str(completed_id),
                        "domain_id": str(domain4),
                        "summary_stats": {"turns_count": 0, "tokens_used": 0, "duration_sec": 10},
                    },
                    actor="learner",
                    idempotency_key="fin-4",
                )
            ],
            publish_wakeup=False,
        )
        await session.execute(
            text(
                "UPDATE core.sessions SET last_activity_at = now() - interval '31 minutes' "
                "WHERE id = :id"
            ),
            {"id": stale1_id},
        )
        await session.execute(
            text(
                "UPDATE core.sessions SET last_activity_at = now() - interval '65 minutes' "
                "WHERE id = :id"
            ),
            {"id": stale2_id},
        )
        await session.execute(
            text(
                "UPDATE core.sessions SET last_activity_at = now() - interval '10 minutes' "
                "WHERE id = :id"
            ),
            {"id": fresh_id},
        )
        await session.execute(
            text(
                "UPDATE core.sessions SET last_activity_at = now() - interval '90 minutes' "
                "WHERE id = :id"
            ),
            {"id": completed_id},
        )
        await enqueue_job(session, kinds.session_sweep_inactive())

    runner = JobRunner(
        settings,
        db,
        "sweep-worker",
        build_job_context(settings, db, None, None),
        handlers={"session.sweep_inactive": lifecycle.sweep_inactive_sessions},
    )
    assert await runner.run_one() is True

    # Both stale active sessions are abandoned with `inactivity_timeout` and `sweep:{session_id}`.
    for lrn, dom, sid in ((learner1, seed.domain_id, stale1_id), (learner2, domain2, stale2_id)):
        row = await _session_row(db, sid)
        assert row["status"] == "abandoned"
        assert row["ended_at"] is not None
        assert row["end_seq"] is not None

        events = await _events(db, lrn)
        abandoned = [e for e in events if e["type"] == "session.abandoned"]
        assert len(abandoned) == 1
        assert abandoned[0]["seq"] == row["end_seq"]
        assert abandoned[0]["session_id"] == sid
        assert abandoned[0]["domain_id"] == dom
        assert abandoned[0]["idempotency_key"] == f"sweep:{sid}"
        assert _payload(abandoned[0]) == {
            "session_id": str(sid),
            "domain_id": str(dom),
            "reason": "inactivity_timeout",
        }

    # The fresh active session (10 min < 30 min) and the completed session are untouched.
    fresh_row = await _session_row(db, fresh_id)
    assert fresh_row["status"] == "active"
    assert fresh_row["ended_at"] is None
    assert fresh_row["end_seq"] is None
    assert not [e for e in await _events(db, learner3) if e["type"] == "session.abandoned"]

    comp_row = await _session_row(db, completed_id)
    assert comp_row["status"] == "completed"
    assert not [e for e in await _events(db, learner4) if e["type"] == "session.abandoned"]

    # Re-running the sweep with the 30-min timeout is a no-op; lowering timeout to 5 min in
    # Settings abandons `fresh_id` (10 min old) without duplicating earlier abandon events.
    dummy_job = ClaimedJob(
        id=uuid4(),
        kind="session.sweep_inactive",
        learner_id=None,
        lock_key="global:sweep_inactive",
        coalesce_key="global:sweep_inactive",
        priority=90,
        payload={},
        attempt=0,
        max_attempts=5,
    )
    await lifecycle.sweep_inactive_sessions(dummy_job, build_job_context(settings, db, None, None))
    assert (await _session_row(db, fresh_id))["status"] == "active"

    short_timeout = settings.model_copy(update={"session_inactivity_timeout_min": 5})
    await lifecycle.sweep_inactive_sessions(
        dummy_job, build_job_context(short_timeout, db, None, None)
    )

    fresh_after = await _session_row(db, fresh_id)
    assert fresh_after["status"] == "abandoned"
    fresh_abandoned = [e for e in await _events(db, learner3) if e["type"] == "session.abandoned"]
    assert len(fresh_abandoned) == 1
    assert fresh_abandoned[0]["idempotency_key"] == f"sweep:{fresh_id}"
    assert _payload(fresh_abandoned[0]) == {
        "session_id": str(fresh_id),
        "domain_id": str(domain3),
        "reason": "inactivity_timeout",
    }
    assert len([e for e in await _events(db, learner1) if e["type"] == "session.abandoned"]) == 1
    assert len([e for e in await _events(db, learner2) if e["type"] == "session.abandoned"]) == 1
