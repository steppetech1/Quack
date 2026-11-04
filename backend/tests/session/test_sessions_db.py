"""ТЗ Ф4 WP13.10 (library part, on `Db`): the session lifecycle — start (domain, intent, topic
or hub), heartbeat, end (`session.ended`), end by inactivity, return to a session the sweeper
ended (`П-4.10`), reading. The HTTP routes move to it after D-C-119 (D-B-155)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import DomainCreated
from quack.events.log import NewEvent, append_events
from quack.session import sessions
from quack.session.sessions import (
    SessionConflictError,
    StartSession,
    end_if_inactive,
    end_session,
    get_session,
    heartbeat,
    list_sessions,
    resume_if_swept,
    start_session,
)
from tests.db.fixtures import create_learner

SETTINGS = Settings()


async def _domain(db: Db, learner_id: UUID) -> UUID:
    domain_id = uuid4()
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="domain.created",
                payload=DomainCreated(domain_id=domain_id, name="Calculus"),
                actor="learner",
                idempotency_key=f"domain:{domain_id}",
            )
        ],
    )
    return domain_id


async def _events(db: Db, learner_id: UUID, session_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT seq, type, payload FROM core.events WHERE learner_id = %(l)s "
        "AND session_id = %(s)s ORDER BY seq",
        {"l": learner_id, "s": session_id},
    )


def _body(domain_id: UUID, **fields: Any) -> StartSession:
    return StartSession.model_validate(
        {"domain_id": domain_id, "intent": "learn", "idempotency_key": uuid4(), **fields}
    )


async def test_start_writes_the_event_and_the_row(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    domain_id = await _domain(pg_db, learner_id)
    topic_id = uuid4()

    started = await start_session(
        pg_db, learner_id=learner_id, body=_body(domain_id, intent="practice", topic_id=topic_id)
    )

    [event] = await _events(pg_db, learner_id, started.session_id)
    assert event["type"] == "session.started"
    assert event["payload"] == {
        "session_id": str(started.session_id),
        "domain_id": str(domain_id),
        "intent": "practice",
        "topic_id": str(topic_id),
        "hub_id": None,
        "resumed": False,
    }
    row = await get_session(pg_db, learner_id=learner_id, session_id=started.session_id)
    assert (row.status, row.intent, row.topic_id, row.domain_id) == (
        "active",
        "practice",
        topic_id,
        domain_id,
    )
    assert row.start_seq == event["seq"]


async def test_repeat_of_the_start_returns_the_same_session(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    domain_id = await _domain(pg_db, learner_id)
    body = _body(domain_id)

    first = await start_session(pg_db, learner_id=learner_id, body=body)
    second = await start_session(pg_db, learner_id=learner_id, body=body)

    assert first.session_id == second.session_id
    assert len(await _events(pg_db, learner_id, first.session_id)) == 1
    with pytest.raises(SessionConflictError):
        changed = body.model_copy(update={"intent": "review"})
        await start_session(pg_db, learner_id=learner_id, body=changed)


async def test_concurrent_start_with_one_key_returns_the_same_session(
    pg_db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review C #124 п.2 (law 5): two starts with one key at once — the second read the key
    before the first committed and its `append_events` meets the key with another payload; it
    reads the key again and returns the same session, not an error (D-B-155 п.ж)."""
    learner_id = await create_learner(pg_db)
    domain_id = await _domain(pg_db, learner_id)
    body = _body(domain_id)
    first = await start_session(pg_db, learner_id=learner_id, body=body)

    real = sessions._started_by_key
    reads: list[int] = []

    async def missed_once(*args: Any, **kwargs: Any) -> Any:
        reads.append(1)
        if len(reads) == 1:
            return None  # the read of the second start, before the first one committed
        return await real(*args, **kwargs)

    monkeypatch.setattr(sessions, "_started_by_key", missed_once)
    second = await start_session(pg_db, learner_id=learner_id, body=body)

    assert second.session_id == first.session_id
    assert len(await _events(pg_db, learner_id, first.session_id)) == 1
    changed = body.model_copy(update={"intent": "review"})
    reads.clear()
    with pytest.raises(SessionConflictError):
        await start_session(pg_db, learner_id=learner_id, body=changed)


async def test_start_needs_a_domain_of_the_learner(pg_db: Db) -> None:
    """`Т-§22.9`: no session without a domain — and the domain is the learner's."""
    learner_id, other = await create_learner(pg_db), await create_learner(pg_db)
    foreign = await _domain(pg_db, other)

    with pytest.raises(LookupError):
        await start_session(pg_db, learner_id=learner_id, body=_body(foreign))
    with pytest.raises(LookupError):
        await start_session(pg_db, learner_id=learner_id, body=_body(uuid4()))


def test_topic_or_hub_not_both() -> None:
    with pytest.raises(ValueError):
        _body(uuid4(), topic_id=uuid4(), hub_id=uuid4())


async def test_a_new_session_does_not_end_the_open_one(pg_db: Db) -> None:
    """`П-4.10`: a session stays open for a return; starting another does not end it."""
    learner_id = await create_learner(pg_db)
    domain_id = await _domain(pg_db, learner_id)
    first = await start_session(pg_db, learner_id=learner_id, body=_body(domain_id))
    second = await start_session(pg_db, learner_id=learner_id, body=_body(domain_id))

    listed = await list_sessions(pg_db, learner_id=learner_id)
    assert [s.session_id for s in listed] == [second.session_id, first.session_id]
    assert {s.status for s in listed} == {"active"}


async def test_heartbeat_moves_the_activity(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    domain_id = await _domain(pg_db, learner_id)
    started = await start_session(pg_db, learner_id=learner_id, body=_body(domain_id))

    beaten = await heartbeat(pg_db, learner_id=learner_id, session_id=started.session_id)

    assert beaten.last_activity_at >= started.last_activity_at
    other = await create_learner(pg_db)
    with pytest.raises(LookupError):
        await heartbeat(pg_db, learner_id=other, session_id=started.session_id)


async def test_end_writes_session_ended_once(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    domain_id = await _domain(pg_db, learner_id)
    started = await start_session(pg_db, learner_id=learner_id, body=_body(domain_id))

    ended = await end_session(pg_db, learner_id=learner_id, session_id=started.session_id)
    again = await end_session(pg_db, learner_id=learner_id, session_id=started.session_id)

    assert ended.status == again.status == "ended" and ended.ended_at is not None
    events = await _events(pg_db, learner_id, started.session_id)
    assert [(e["type"], e["payload"].get("reason")) for e in events] == [
        ("session.started", None),
        ("session.ended", "learner"),
    ]
    with pytest.raises(LookupError):
        await heartbeat(pg_db, learner_id=learner_id, session_id=started.session_id)


async def test_inactive_session_is_ended_and_returns_on_action(pg_db: Db) -> None:
    """WP13.10: the sweeper ends an inactive session (`session.ended {reason: inactive}`); an
    action in it writes `session.started` of the same session again and goes on."""
    learner_id = await create_learner(pg_db)
    domain_id = await _domain(pg_db, learner_id)
    started = await start_session(pg_db, learner_id=learner_id, body=_body(domain_id))
    soon = started.last_activity_at + timedelta(minutes=1)
    late = started.last_activity_at + timedelta(minutes=SETTINGS.session_inactivity_timeout_min + 1)

    assert not await end_if_inactive(
        pg_db, learner_id=learner_id, session_id=started.session_id, settings=SETTINGS, now=soon
    )
    assert await end_if_inactive(
        pg_db, learner_id=learner_id, session_id=started.session_id, settings=SETTINGS, now=late
    )
    assert (
        await get_session(pg_db, learner_id=learner_id, session_id=started.session_id)
    ).status == ("ended")

    assert await resume_if_swept(pg_db, learner_id=learner_id, session_id=started.session_id)
    assert not await resume_if_swept(pg_db, learner_id=learner_id, session_id=started.session_id)

    events = await _events(pg_db, learner_id, started.session_id)
    assert [(e["type"], e["payload"].get("reason")) for e in events] == [
        ("session.started", None),
        ("session.ended", "inactive"),
        ("session.started", None),
    ]
    assert events[2]["payload"]["session_id"] == str(started.session_id)
    # WP13.10, D-A-121: the return is `session.started {resumed: true}`; the start is not
    assert [e["payload"].get("resumed") for e in events if e["type"] == "session.started"] == [
        False,
        True,
    ]
    row = await get_session(pg_db, learner_id=learner_id, session_id=started.session_id)
    assert row.status == "active" and row.ended_at is None


async def test_session_ended_by_the_learner_does_not_return(pg_db: Db) -> None:
    """Only a session the sweeper ended is open for a return (WP13.10)."""
    learner_id = await create_learner(pg_db)
    domain_id = await _domain(pg_db, learner_id)
    started = await start_session(pg_db, learner_id=learner_id, body=_body(domain_id))
    await end_session(pg_db, learner_id=learner_id, session_id=started.session_id)

    assert not await resume_if_swept(pg_db, learner_id=learner_id, session_id=started.session_id)
    assert len(await _events(pg_db, learner_id, started.session_id)) == 2


async def test_sessions_of_another_learner_are_not_seen(pg_db: Db) -> None:
    learner_id, other = await create_learner(pg_db), await create_learner(pg_db)
    domain_id = await _domain(pg_db, learner_id)
    started = await start_session(pg_db, learner_id=learner_id, body=_body(domain_id))

    with pytest.raises(LookupError):
        await get_session(pg_db, learner_id=other, session_id=started.session_id)
    with pytest.raises(LookupError):
        await end_session(pg_db, learner_id=other, session_id=started.session_id)
    assert await list_sessions(pg_db, learner_id=other) == []
    assert datetime.now(UTC) >= started.started_at


async def test_session_of_a_today_item_takes_the_prepared_plan(pg_db: Db) -> None:
    """T-E9 (#279, D-B-222): the session started on the topic (or by `today_item`) of an item of
    the latest `today.shown` takes the id of the prepared plan; the next start does not."""
    from psycopg.types.json import Jsonb

    from quack.events.event_types import TodayItem, TodayShown
    from quack.practice.today_plan import prepared_session_id

    learner_id = await create_learner(pg_db)
    domain_id = await _domain(pg_db, learner_id)
    topic, skill = uuid4(), uuid4()
    [shown] = await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="today.shown",
                payload=TodayShown(
                    domain_id=domain_id,
                    items=[TodayItem(skill_id=skill, topic_id=topic, reasons=["due"], score=1.0)],
                ),
                actor="system",
                idempotency_key=f"today:{uuid4()}",
                domain_id=domain_id,
            )
        ],
    )
    planned = prepared_session_id(learner_id, shown.seq, skill)
    await pg_db.execute(
        "INSERT INTO content.session_plans (session_id, learner_id, plan, status) "
        "VALUES (%(s)s, %(l)s, %(p)s, 'prepared')",
        {"s": planned, "l": learner_id, "p": Jsonb({"current_step": "Recall the ratio"})},
    )

    first = await start_session(pg_db, learner_id=learner_id, body=_body(domain_id, topic_id=topic))
    assert first.session_id == planned
    again = await start_session(pg_db, learner_id=learner_id, body=_body(domain_id, topic_id=topic))
    assert again.session_id != planned  # the plan is taken once
    other = await start_session(
        pg_db, learner_id=learner_id, body=_body(domain_id, topic_id=uuid4())
    )
    assert other.session_id not in (planned, again.session_id)
