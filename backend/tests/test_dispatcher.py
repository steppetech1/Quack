"""Log dispatcher (ТЗ Фазы 3 §8.3a, WP11): T-D1…T-D8."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from quack.api.app import create_app
from quack.api.auth import issue_access_token
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.events import append_events
from quack.events.models import NewEvent, StoredEvent
from quack.graph.worker import ProjectorWorker
from quack.jobs import dispatcher as dispatcher_module
from quack.jobs import kinds
from quack.jobs.dispatcher import Dispatcher, supervise
from quack.jobs.dispatcher_rules import DISPATCHER_RULES
from quack.jobs.queue import JobSpec
from tests.factories import new_event
from tests.support.auth import ensure_user
from tests.support.resources import build_app_resources

# Rows compared with what the transaction of the event used to enqueue (WP11.2).
_JOB_COLUMNS = "kind, priority, lock_key, coalesce_key, required_seq, payload"


async def _q(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def _exec(db: SessionFactory, sql: str, **params: Any) -> None:
    async with db() as session, session.begin():
        await session.execute(text(sql), params)


async def _append(db: SessionFactory, learner: UUID, *events: NewEvent) -> list[StoredEvent]:
    async with db() as session, session.begin():
        return await append_events(session, learner, list(events), False)


async def _jobs(db: SessionFactory, learner: UUID) -> list[dict[str, Any]]:
    return await _q(
        db,
        f"SELECT {_JOB_COLUMNS} FROM ops.jobs WHERE learner_id = :l ORDER BY created_at, id",
        l=learner,
    )


async def _offset(db: SessionFactory, learner: UUID) -> int | None:
    rows = await _q(
        db,
        "SELECT last_dispatched_seq FROM ops.dispatcher_offsets WHERE learner_id = :l",
        l=learner,
    )
    return None if not rows else int(rows[0]["last_dispatched_seq"])


async def _last_seq(db: SessionFactory, learner: UUID) -> int:
    [row] = await _q(db, "SELECT last_seq FROM core.learner_seq WHERE learner_id = :l", l=learner)
    return int(row["last_seq"])


def _row(spec: JobSpec) -> dict[str, Any]:
    return {
        "kind": spec.kind,
        "priority": spec.priority,
        "lock_key": spec.lock_key,
        "coalesce_key": spec.coalesce_key,
        "required_seq": spec.required_seq,
        "payload": spec.payload,
    }


@dataclass
class Story:
    """One session of a learner: started, one turn pair, completed, digest with a topic."""

    learner: UUID
    domain: UUID
    topic: UUID
    session: UUID
    turn: UUID
    student_seq: int
    teacher_seq: int
    completed_seq: int
    digest_seq: int
    turn_index: int


async def _story(db: SessionFactory, learner: UUID | None = None, *, turn_index: int = 7) -> Story:
    learner = learner or uuid4()
    domain, topic, sid, turn = uuid4(), uuid4(), uuid4(), uuid4()
    d, t, s = str(domain), str(topic), str(sid)
    await _append(
        db,
        learner,
        new_event(
            "session.started",
            {
                "session_id": s,
                "domain_id": d,
                "topic_id": t,
                "mode": "learn",
                "goal": None,
                "plan": [],
            },
            session_id=sid,
            domain_id=domain,
        ),
    )
    # D-B-018: the student reply is written before the teacher turn, in its own transaction.
    [student] = await _append(
        db,
        learner,
        new_event(
            "turn.student_replied",
            {"session_id": s, "turn_id": str(turn), "turn_index": turn_index - 1, "text": "hi"},
            actor="learner",
            session_id=sid,
            turn_id=turn,
        ),
    )
    [teacher] = await _append(
        db,
        learner,
        new_event(
            "turn.teacher_replied",
            {
                "session_id": s,
                "turn_id": str(turn),
                "turn_index": turn_index,
                "text": "hello",
                "pedagogical_move": "explain",
            },
            actor="teacher@1",
            session_id=sid,
            turn_id=turn,
        ),
    )
    [completed] = await _append(
        db,
        learner,
        new_event(
            "session.completed",
            {"session_id": s, "domain_id": d, "summary_stats": {}},
            actor="learner",
        ),
    )
    [digest] = await _append(
        db,
        learner,
        new_event(
            "session.digest_created",
            {
                "artifact_id": str(uuid4()),
                "session_id": s,
                "domain_id": d,
                "topic_id": t,
                "title": "recap",
                "text": "recap",
                "covered_skill_ids": [],
            },
            actor="system",
        ),
    )
    return Story(
        learner,
        domain,
        topic,
        sid,
        turn,
        student.seq,
        teacher.seq,
        completed.seq,
        digest.seq,
        turn_index,
    )


def _expected(story: Story) -> list[dict[str, Any]]:
    """The jobs the transactions of the events enqueued before WP11 (PHASE3_NOTES, S11)."""
    sid, s = story.session, str(story.session)
    return [
        _row(
            kinds.turn_extract(
                story.learner,
                sid,
                {
                    "session_id": s,
                    "turn_id": str(story.turn),
                    "student_seq": story.student_seq,
                    "teacher_seq": story.teacher_seq,
                },
                required_seq=story.teacher_seq,
            )
        ),
        _row(
            kinds.session_checkpoint(
                story.learner, sid, {"session_id": s, "up_to_turn_index": story.turn_index}
            )
        ),
        _row(
            kinds.session_finish_artifacts(
                story.learner,
                sid,
                {"session_id": s, "domain_id": str(story.domain), "topic_id": str(story.topic)},
                required_seq=story.completed_seq,
            )
        ),
        _row(
            kinds.graph_curate(
                story.learner,
                story.domain,
                {"domain_id": str(story.domain)},
                required_seq=story.completed_seq,
            )
        ),
        _row(
            kinds.artifact_refresh_cheatsheet(
                story.learner,
                story.topic,
                {"domain_id": str(story.domain), "session_id": s},
                required_seq=story.digest_seq,
            )
        ),
    ]


def _dispatcher(settings: Settings, db: SessionFactory) -> Dispatcher:
    return Dispatcher(settings, db)


# --- T-D1, T-D3 -------------------------------------------------------------------------


async def test_dispatcher_turns_events_into_jobs(db: SessionFactory, settings: Settings) -> None:
    story = await _story(db)
    assert await _jobs(db, story.learner) == []
    assert await _dispatcher(settings, db).run_once() > 0
    assert await _jobs(db, story.learner) == _expected(story)
    assert await _offset(db, story.learner) == await _last_seq(db, story.learner)
    # Every row of the S11 table is covered by the story.
    kinds_made = {row["kind"] for row in await _jobs(db, story.learner)}
    assert kinds_made == {rule.job_kind for rule in DISPATCHER_RULES}


async def test_dispatcher_checkpoint_follows_last_checkpoint_turn(
    db: SessionFactory, settings: Settings
) -> None:
    """D-A-003: no `session.checkpoint` while `turn_index − last_checkpoint_turn` is short."""
    story = await _story(db, turn_index=settings.checkpoint_every_turns - 2)
    await _dispatcher(settings, db).run_once()
    kinds_made = [row["kind"] for row in await _jobs(db, story.learner)]
    assert "session.checkpoint" not in kinds_made and "turn.extract" in kinds_made


async def test_dispatcher_required_seq_is_trigger_seq(
    db: SessionFactory, settings: Settings
) -> None:
    story = await _story(db)
    await _dispatcher(settings, db).run_once()
    got = {row["kind"]: row["required_seq"] for row in await _jobs(db, story.learner)}
    assert got == {
        "turn.extract": story.teacher_seq,
        "session.checkpoint": None,
        "session.finish_artifacts": story.completed_seq,
        "graph.curate": story.completed_seq,
        "artifact.refresh_cheatsheet": story.digest_seq,
    }


# --- T-D2 -------------------------------------------------------------------------------


async def test_dispatcher_offset_and_jobs_commit_together(
    db: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    story = await _story(db)
    real = dispatcher_module.enqueue_job
    calls = 0

    async def flaky(session: Any, spec: JobSpec) -> UUID:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("enqueue failed")
        return await real(session, spec)

    monkeypatch.setattr(dispatcher_module, "enqueue_job", flaky)
    await _dispatcher(settings, db).run_once()
    assert await _jobs(db, story.learner) == []
    assert await _offset(db, story.learner) in (None, 0)

    monkeypatch.setattr(dispatcher_module, "enqueue_job", real)
    await _dispatcher(settings, db).run_once()
    assert await _jobs(db, story.learner) == _expected(story)
    assert await _offset(db, story.learner) == await _last_seq(db, story.learner)


# --- T-D4 -------------------------------------------------------------------------------


async def test_dispatcher_replay_is_idempotent(db: SessionFactory, settings: Settings) -> None:
    story = await _story(db)
    dispatcher = _dispatcher(settings, db)
    await dispatcher.run_once()
    before = await _jobs(db, story.learner)
    # Replay while the jobs are still queued: the coalesce keys and trigger seqs match.
    await _exec(
        db,
        "UPDATE ops.dispatcher_offsets SET last_dispatched_seq = 0 WHERE learner_id = :l",
        l=story.learner,
    )
    await dispatcher.run_once()
    assert await _jobs(db, story.learner) == before
    # The jobs ran meanwhile, the coalesce key of a queued job no longer protects them; the
    # checkpoint wrote its event (as `session.checkpoint` does) and moved last_checkpoint_turn.
    await _exec(db, "UPDATE ops.jobs SET status = 'done' WHERE learner_id = :l", l=story.learner)
    await _append(
        db,
        story.learner,
        new_event(
            "session.checkpointed",
            {
                "session_id": str(story.session),
                "up_to_turn_index": story.turn_index,
                "rolling_summary": "",
                "open_threads": [],
                "key_observations": [],
            },
            session_id=story.session,
        ),
    )
    await _exec(
        db,
        "UPDATE ops.dispatcher_offsets SET last_dispatched_seq = 0 WHERE learner_id = :l",
        l=story.learner,
    )
    await dispatcher.run_once()
    assert await _jobs(db, story.learner) == before
    assert await _offset(db, story.learner) == await _last_seq(db, story.learner)


# --- T-D5 -------------------------------------------------------------------------------


async def test_dispatcher_orders_per_learner_and_isolates_failures(
    db: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = uuid4(), uuid4()
    stories_b: list[Story] = []
    for _ in range(2):  # interleaved: A, B, A, B
        await _story(db, a)
        stories_b.append(await _story(db, b))

    real = dispatcher_module.rules_for

    def broken_for_a(event: StoredEvent, *args: Any, **kwargs: Any) -> list[JobSpec]:
        if event.learner_id == a and event.type == "session.completed":
            raise RuntimeError("rule failed")
        return real(event, *args, **kwargs)

    monkeypatch.setattr(dispatcher_module, "rules_for", broken_for_a)
    with capture_logs() as logs:
        await _dispatcher(settings, db).run_once()
    assert any(entry["event"] == "dispatcher_learner_failed" for entry in logs)
    assert await _jobs(db, a) == []
    assert await _offset(db, a) in (None, 0)
    assert await _jobs(db, b) == _expected(stories_b[0]) + _expected(stories_b[1])
    assert await _offset(db, b) == await _last_seq(db, b)


# --- T-D6 -------------------------------------------------------------------------------


async def test_dispatcher_crash_does_not_stop_projector(
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
) -> None:
    story = await _story(db)
    fast = settings.model_copy(update={"dispatcher_restart_pause_s": 0.05, "consumer_poll_ms": 20})
    dispatcher = Dispatcher(fast, db)
    projector = ProjectorWorker(fast, pg_engine, db, neo4j, None)
    starts = 0

    async def crashing(stop: asyncio.Event) -> None:
        nonlocal starts
        starts += 1
        if starts == 1:
            raise RuntimeError("dispatcher crashed")
        await dispatcher.run_forever(stop)

    stop = asyncio.Event()

    async def until_done() -> None:
        last = await _last_seq(db, story.learner)
        for _ in range(200):
            applied = await _q(
                db,
                "SELECT applied_seq FROM ops.projector_offsets WHERE learner_id = :l",
                l=story.learner,
            )
            if (
                applied
                and applied[0]["applied_seq"] == last
                and (await _offset(db, story.learner) == last)
            ):
                break
            await asyncio.sleep(0.05)
        stop.set()

    with capture_logs() as logs:
        await asyncio.wait_for(
            asyncio.gather(
                supervise("dispatcher", crashing, stop, restart_pause_s=0.05),
                supervise("projector", projector.run_forever, stop, restart_pause_s=0.05),
                until_done(),
            ),
            timeout=15,
        )
    crashes = [e for e in logs if e["event"] == "supervised_task_crashed"]
    assert [e["task"] for e in crashes] == ["dispatcher"]
    assert starts == 2
    [row] = await _q(
        db, "SELECT applied_seq FROM ops.projector_offsets WHERE learner_id = :l", l=story.learner
    )
    assert row["applied_seq"] == await _last_seq(db, story.learner)
    assert await _jobs(db, story.learner) == _expected(story)


# --- T-D7 -------------------------------------------------------------------------------


async def test_dispatcher_skips_unknown_event(db: SessionFactory, settings: Settings) -> None:
    story = await _story(db)
    # The teacher reply now carries a schema version this code does not know.
    await _exec(
        db,
        "UPDATE core.events SET schema_version = 99 WHERE learner_id = :l AND seq = :s",
        l=story.learner,
        s=story.teacher_seq,
    )
    with capture_logs() as logs:
        await _dispatcher(settings, db).run_once()
    unknown = [e for e in logs if e["event"] == "dispatcher_unknown_event"]
    assert [e["seq"] for e in unknown] == [story.teacher_seq]
    assert await _offset(db, story.learner) == await _last_seq(db, story.learner)
    assert [row["kind"] for row in await _jobs(db, story.learner)] == [
        "session.finish_artifacts",
        "graph.curate",
        "artifact.refresh_cheatsheet",
    ]


# --- WP11.5: lag for `ops status` --------------------------------------------------------


async def test_dispatcher_lag(db: SessionFactory, settings: Settings) -> None:
    story = await _story(db)
    dispatcher = _dispatcher(settings, db)
    lagging, max_lag = await dispatcher.lag()
    assert lagging >= 1 and max_lag >= await _last_seq(db, story.learner)
    await dispatcher.run_once()
    assert await dispatcher.lag() == (0, 0)


# --- T-D8 -------------------------------------------------------------------------------


async def test_cheatsheet_refresh_endpoint_enqueues_directly(
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    learner = uuid4()
    await ensure_user(db, learner)
    domain, topic = uuid4(), uuid4()
    d, t = str(domain), str(topic)
    await _append(
        db,
        learner,
        new_event(
            "domain.created", {"domain_id": d, "name": "D", "slug": f"d-{d[:8]}"}, actor="learner"
        ),
        new_event(
            "structure.proposed",
            {
                "domain_id": d,
                "structure_version": 1,
                "topics": [{"topic_id": t, "name": "T"}],
                "skills": [],
                "prereqs": [],
                "skill_deps": [],
                "misconceptions": [],
            },
            actor="structurer@1",
        ),
        new_event("structure.confirmed", {"domain_id": d, "structure_version": 1}, actor="learner"),
    )
    app = create_app(
        settings,
        resources=build_app_resources(settings, pg_engine, neo4j, redis, session_factory=db),
    )
    headers = {"Authorization": f"Bearer {issue_access_token(learner, settings)}"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            f"/v1/topics/{t}/cheatsheet/refresh", json={"domain_id": d}, headers=headers
        )
        second = await client.post(
            f"/v1/topics/{t}/cheatsheet/refresh", json={"domain_id": d}, headers=headers
        )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["job_id"] == second.json()["job_id"]
    rows = await _q(db, "SELECT kind, required_seq FROM ops.jobs WHERE learner_id = :l", l=learner)
    assert rows == [
        {"kind": "artifact.refresh_cheatsheet", "required_seq": await _last_seq(db, learner)}
    ]
