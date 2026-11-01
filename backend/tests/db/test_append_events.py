"""T-DB5 (ТЗ Ф4 §8.2): the append_events contract of Phase 1 holds on `Db` (Т-§2.3).

Gapless seq in commit order, idempotency by key and payload hash, conflict on a changed
payload, `ops.event_types_seen`, work inside the caller's transaction, the `learner:{id}`
signal only after commit. A test payload type is registered for the duration of each test so
the contract does not depend on the catalog (WP2, T-EV*).
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError
from redis.asyncio import Redis

from quack.db import Db
from quack.db.redis import learner_signal_channel
from quack.events.errors import IdempotencyConflictError
from quack.events.hashing import payload_hash
from quack.events.log import LearnerNotFoundError, NewEvent, StoredEvent, append_events
from quack.events.registry import REGISTRY, EventPayload, UnknownEventTypeError
from tests.db.fixtures import create_learner


class Noted(EventPayload):
    text: str


@pytest.fixture(autouse=True)
def _test_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(REGISTRY, ("test.noted", 1), Noted)


def _ev(key: str, text: str = "x", *, actor: str = "learner", **extra: object) -> NewEvent:
    return NewEvent(
        type="test.noted",
        payload={"text": text},
        actor=actor,
        idempotency_key=key,
        **extra,  # type: ignore[arg-type]
    )


async def _counter(db: Db, learner_id: object) -> int:
    row = await db.fetch_one(
        "SELECT seq FROM core.learners WHERE learner_id = %(learner_id)s",
        {"learner_id": learner_id},
    )
    assert row is not None
    return int(row["seq"])


async def _events(db: Db, learner_id: object) -> list[StoredEvent]:
    return await db.fetch_all(
        "SELECT learner_id, seq, id, type, schema_version, actor, domain_id, session_id, "
        "causation_seq, correlation_id, idempotency_key, payload_hash, occurred_at, payload, "
        "source_refs FROM core.events WHERE learner_id = %(learner_id)s ORDER BY seq",
        {"learner_id": learner_id},
        row=StoredEvent,
    )


async def test_append_assigns_monotonic_gapless_seq(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    first = await append_events(pg_db, learner, [_ev("a"), _ev("b"), _ev("c")])
    second = await append_events(pg_db, learner, [_ev("d"), _ev("e")])
    assert [e.seq for e in first + second] == [1, 2, 3, 4, 5]
    assert await _counter(pg_db, learner) == 5


async def test_append_stores_envelope_and_hash(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    session_id, domain_id, correlation_id = uuid4(), uuid4(), uuid4()
    refs = [{"seq": 1}]
    [stored] = await append_events(
        pg_db,
        learner,
        [
            _ev(
                "k",
                "hello",
                actor="extractor@3",
                session_id=session_id,
                domain_id=domain_id,
                causation_seq=7,
                correlation_id=correlation_id,
                source_refs=refs,
            )
        ],
    )
    [read] = await _events(pg_db, learner)
    assert read == stored
    assert (read.type, read.schema_version, read.actor) == ("test.noted", 1, "extractor@3")
    assert (read.session_id, read.domain_id, read.correlation_id) == (
        session_id,
        domain_id,
        correlation_id,
    )
    assert read.causation_seq == 7 and read.source_refs == refs
    assert read.payload == {"text": "hello"}
    assert read.payload_hash == payload_hash({"text": "hello"})
    assert read.occurred_at.tzinfo is not None


async def test_idempotent_retry_returns_existing_event(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    [first] = await append_events(pg_db, learner, [_ev("k", "same")])
    [again] = await append_events(pg_db, learner, [_ev("k", "same")])
    assert again == first
    assert await _counter(pg_db, learner) == 1
    assert len(await _events(pg_db, learner)) == 1


async def test_idempotency_conflict_on_changed_payload(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    await append_events(pg_db, learner, [_ev("k", "one")])
    with pytest.raises(IdempotencyConflictError):
        await append_events(pg_db, learner, [_ev("new"), _ev("k", "two")])
    assert await _counter(pg_db, learner) == 1
    assert [e.idempotency_key for e in await _events(pg_db, learner)] == ["k"]


async def test_mixed_batch_returns_existing_and_appends_new(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    [old] = await append_events(pg_db, learner, [_ev("a")])
    batch = await append_events(pg_db, learner, [_ev("b"), _ev("a"), _ev("c")])
    assert [e.seq for e in batch] == [2, old.seq, 3]
    assert batch[1] == old


async def test_invalid_payload_writes_nothing(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    bad = NewEvent(
        type="test.noted", payload={"text": 1, "x": 2}, actor="code", idempotency_key="b"
    )
    with pytest.raises(ValidationError):
        await append_events(pg_db, learner, [_ev("ok"), bad])
    assert await _counter(pg_db, learner) == 0


async def test_unknown_type_and_duplicate_keys_are_rejected(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    unknown = NewEvent(type="nope.done", payload={}, actor="code", idempotency_key="u")
    with pytest.raises(UnknownEventTypeError):
        await append_events(pg_db, learner, [unknown])
    with pytest.raises(ValueError, match="duplicate idempotency_key"):
        await append_events(pg_db, learner, [_ev("d"), _ev("d")])
    assert await _counter(pg_db, learner) == 0


async def test_learner_row_is_required(pg_db: Db) -> None:
    with pytest.raises(LearnerNotFoundError):
        await append_events(pg_db, uuid4(), [_ev("k")])


def test_actor_versions_and_agents() -> None:
    for actor in ("learner", "code", "system", "teacher@2", "extractor@3", "agent:analyst"):
        assert _ev("k", actor=actor).actor == actor
    for actor in ("teacher", "agent:", "Agent:x", "extractor@0"):
        with pytest.raises(ValidationError):
            _ev("k", actor=actor)


async def test_event_types_seen_recorded(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    await append_events(pg_db, learner, [_ev("a"), _ev("b")])
    rows = await pg_db.fetch_all("SELECT type, schema_version FROM ops.event_types_seen")
    assert rows == [{"type": "test.noted", "schema_version": 1}]


async def test_runs_inside_callers_transaction(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    session_id = uuid4()
    insert_session = (
        "INSERT INTO content.sessions (session_id, learner_id, status) "
        "VALUES (%(session_id)s, %(learner_id)s, 'active')"
    )
    with pytest.raises(RuntimeError):
        async with pg_db.transaction():
            await pg_db.execute(insert_session, {"session_id": session_id, "learner_id": learner})
            await append_events(pg_db, learner, [_ev("a")])
            raise RuntimeError("caller fails after the append")
    assert await _counter(pg_db, learner) == 0
    assert await pg_db.fetch_all("SELECT session_id FROM content.sessions") == []

    async with pg_db.transaction():
        await pg_db.execute(insert_session, {"session_id": session_id, "learner_id": learner})
        await append_events(pg_db, learner, [_ev("a")])
    assert await _counter(pg_db, learner) == 1
    assert len(await pg_db.fetch_all("SELECT session_id FROM content.sessions")) == 1


async def test_concurrent_appends_for_one_learner_are_serialized(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    results = await asyncio.gather(
        *(append_events(pg_db, learner, [_ev(f"k{i}"), _ev(f"m{i}")]) for i in range(10))
    )
    seqs = sorted(e.seq for batch in results for e in batch)
    assert seqs == list(range(1, 21))
    for batch in results:  # one call = consecutive numbers
        assert batch[1].seq == batch[0].seq + 1


async def _next_message(pubsub: object, wait_s: float) -> object:
    get = pubsub.get_message  # type: ignore[attr-defined]
    return await get(ignore_subscribe_messages=True, timeout=wait_s)


async def test_signal_is_published_after_commit(pg_db: Db, redis: Redis) -> None:
    learner = await create_learner(pg_db)
    pubsub = redis.pubsub()
    await pubsub.subscribe(learner_signal_channel(learner))
    try:
        async with pg_db.transaction():
            await append_events(pg_db, learner, [_ev("a"), _ev("b")], redis=redis)
            assert await _next_message(pubsub, 0.2) is None  # not before commit
        message = await _next_message(pubsub, 5.0)
        assert message is not None and message["data"] in (b"2", "2")  # type: ignore[index]
    finally:
        await pubsub.aclose()


async def test_signal_not_published_on_rollback(pg_db: Db, redis: Redis) -> None:
    learner = await create_learner(pg_db)
    pubsub = redis.pubsub()
    await pubsub.subscribe(learner_signal_channel(learner))
    try:
        with pytest.raises(RuntimeError):
            async with pg_db.transaction():
                await append_events(pg_db, learner, [_ev("a")], redis=redis)
                raise RuntimeError("rollback")
        assert await _next_message(pubsub, 0.5) is None
    finally:
        await pubsub.aclose()
