"""ТЗ Ф4 WP25 (`Т-§19.5`, `Д-§8.5`): the audit fixes of the deletion — the objects go before the
commit, the log row comes after the rebuild, the jobs, plans and counters of the deleted data go
with it, the deletion of an account outlives no job of the dispatcher."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, LiteralString
from uuid import UUID, uuid4

import pytest
from neo4j import AsyncDriver

from quack.config import Settings
from quack.db import Db
from quack.deletion import domain as domain_deletion
from quack.deletion.account import apply_account_record
from quack.deletion.domain import DeletionBusy, apply_domain_record, erase_domain
from quack.deletion.journal import reconcile_deletions
from quack.deletion.topic import erase_topic
from quack.events.log import NewEvent, append_events
from quack.graph.projection.rebuild import RebuildOutcome
from quack.jobs.dispatcher_p4 import DispatcherP4
from quack.jobs.queue_p4 import enqueue
from quack.jobs.rules_p4 import JobPlan
from quack.storage.deletions import DeletionRecord
from quack.storage.objects import StorageUnavailable
from tests.db.fixtures import create_learner
from tests.support.deletions import FakeDeletionJournal
from tests.support.storage import BrokenObjectStore, InMemoryObjectStore

NOW = datetime(2026, 11, 13, tzinfo=UTC)
_COUNT_EVENTS = "SELECT count(*) AS n FROM core.events WHERE learner_id = %(l)s"
_COUNT_JOBS = "SELECT count(*) AS n FROM ops.jobs WHERE learner_id = %(l)s"
_LOGGED = "SELECT count(*) AS n FROM ops.deletion_log WHERE learner_id = %(l)s"


async def _count(db: Db, query: LiteralString, learner: UUID) -> int:
    row = await db.fetch_one(query, {"l": learner})
    assert row is not None
    return int(row["n"])


def _event(kind: str, payload: dict[str, Any], key: str, domain: UUID | None) -> NewEvent:
    return NewEvent(type=kind, payload=payload, actor="code", idempotency_key=key, domain_id=domain)


def _job(learner: UUID, key: str, **payload: object) -> JobPlan:
    return JobPlan(
        kind="embedding.compute", job_key=key, priority_class="P2", learner_id=learner,
        payload=dict(payload),
    )  # fmt: skip


async def test_failed_store_keeps_the_rows_for_the_repeat(pg_db: Db) -> None:
    """The keys of the objects are read from the rows: a store that fails after the commit left
    no row to read them from, and the files of the learner stayed in the store for good."""
    learner, domain = await create_learner(pg_db), uuid4()
    material = {
        "material_id": str(uuid4()),
        "object_key": "learners/x/m1",
        "mime": "application/pdf",
    }
    await append_events(pg_db, learner, [_event("material.uploaded", material, "m", domain)])
    with pytest.raises(StorageUnavailable):
        await erase_domain(pg_db, BrokenObjectStore(), learner, domain)
    assert await _count(pg_db, _COUNT_EVENTS, learner) == 1  # rolled back: the keys are still known
    store = InMemoryObjectStore()
    await store.put("learners/x/m1", b"pdf", "application/pdf")
    assert await erase_domain(pg_db, store, learner, domain) == 1
    assert store.objects == {} and await _count(pg_db, _COUNT_EVENTS, learner) == 0


async def test_domain_takes_its_jobs_plans_boards_and_counters(pg_db: Db) -> None:
    learner, other, domain, session = await create_learner(pg_db), uuid4(), uuid4(), uuid4()
    hub, task, kept_domain = uuid4(), uuid4(), uuid4()
    chunk = {"chunk_id": str(uuid4()), "material_id": str(uuid4()), "page": 1, "text": "secret",
             "notation": [], "teaches": [], "uses": []}  # fmt: skip
    hub_created = {"hub_id": str(hub), "domain_id": str(domain), "name": "h", "kind": "domain_all",
                   "origin": "learner", "control_type": "no_goal"}  # fmt: skip
    gone, _hub_event = await append_events(
        pg_db,
        learner,
        [
            _event("chunk.indexed", chunk, "c", domain),
            _event("hub.created", hub_created, "h", domain),
        ],
    )
    nodes = [{"node_kind": "chunk", "node_id": chunk["chunk_id"], "text": "secret"}]
    await enqueue(pg_db, _job(learner, "gone", trigger_seq=gone.seq, nodes=nodes))
    await enqueue(pg_db, _job(learner, "by-domain", trigger_seq=0, domain_id=str(domain)))
    await enqueue(pg_db, _job(learner, "kept", trigger_seq=0))
    await enqueue(pg_db, _job(learner, "kept-too", domain_id=str(kept_domain)))
    await enqueue(pg_db, _job(uuid4(), "foreign", trigger_seq=gone.seq))  # another learner
    await pg_db.execute(
        "INSERT INTO content.study_plans (learner_id, hub_id, plan) VALUES (%(l)s, %(h)s, '{}'), "
        "(%(l)s, %(o)s, '{}')",
        {"l": learner, "h": hub, "o": other},
    )
    await pg_db.execute(
        "INSERT INTO content.tasks (task_id, learner_id, domain_id, source, condition, "
        "answer_spec, created_seq) VALUES (%(t)s, %(l)s, %(d)s, 'teacher', '{}', '{}', 1)",
        {"t": task, "l": learner, "d": domain},
    )
    await pg_db.execute(
        "INSERT INTO content.canvas_boards (canvas_id, learner_id, session_id, task_id) "
        "VALUES (%(c)s, %(l)s, NULL, %(t)s), (%(o)s, %(l)s, NULL, NULL)",
        {"c": uuid4(), "o": uuid4(), "l": learner, "t": task},
    )
    open_episode = {"from_seq": 1, "last_seq": 1, "last_at": NOW.isoformat(),
                    "domain_id": str(domain), "turns": 0}  # fmt: skip
    await pg_db.execute(
        "INSERT INTO ops.dispatcher_offsets (learner_id, counters) VALUES (%(l)s, %(c)s::jsonb)",
        {"l": learner, "c": json.dumps({"episodes": {str(session): open_episode,
                                                str(uuid4()): {**open_episode, "domain_id": None}},
                                   "new_topics": {str(domain): 2, str(kept_domain): 1}})},
    )  # fmt: skip
    await erase_domain(pg_db, InMemoryObjectStore(), learner, domain)
    keys = {r["job_key"] for r in await pg_db.fetch_all("SELECT job_key FROM ops.jobs")}
    assert {"gone", "by-domain"}.isdisjoint(keys)
    assert {"kept", "kept-too", "foreign"} <= keys
    plans = await pg_db.fetch_all("SELECT hub_id FROM content.study_plans")
    assert [r["hub_id"] for r in plans] == [other]
    boards = await pg_db.fetch_all("SELECT task_id FROM content.canvas_boards")
    assert [r["task_id"] for r in boards] == [None]
    row = await pg_db.fetch_one(
        "SELECT counters FROM ops.dispatcher_offsets WHERE learner_id = %(l)s", {"l": learner}
    )
    assert row is not None
    assert row["counters"]["new_topics"] == {str(kept_domain): 1}
    assert [e["domain_id"] for e in row["counters"]["episodes"].values()] == [None]
    # The idle check of the dispatcher closes nothing of the deleted domain.
    later = datetime(2026, 11, 14, tzinfo=UTC)
    dispatcher = DispatcherP4(Settings(), pg_db, clock=lambda: later)
    await dispatcher.close_idle_episodes()
    closed = await pg_db.fetch_all(
        "SELECT domain_id FROM core.events WHERE learner_id = %(l)s AND type = 'episode.closed'",
        {"l": learner},
    )
    assert domain not in {r["domain_id"] for r in closed}


async def test_topic_takes_the_jobs_that_carry_its_data(pg_db: Db) -> None:
    learner, topic, skill, domain = await create_learner(pg_db), uuid4(), uuid4(), uuid4()
    created = {"skill_id": str(skill), "topic_id": str(topic), "domain_id": str(domain),
               "name": "s", "summary": "s", "status": "active", "origin": "session"}  # fmt: skip
    (gone,) = await append_events(pg_db, learner, [_event("skill.created", created, "s", domain)])
    await enqueue(pg_db, _job(learner, "by-event", trigger_seq=gone.seq, nodes=[]))
    await enqueue(pg_db, _job(learner, "by-topic", topic_id=str(topic)))
    nodes = [{"node_kind": "misconception", "node_id": str(skill), "text": "secret"}]
    await enqueue(pg_db, _job(learner, "by-node", nodes=nodes))
    await enqueue(pg_db, _job(learner, "kept", trigger_seq=gone.seq + 5, topic_id=str(uuid4())))
    await erase_topic(pg_db, InMemoryObjectStore(), learner, topic, [str(skill)], [])
    keys = {r["job_key"] for r in await pg_db.fetch_all("SELECT job_key FROM ops.jobs")}
    assert keys == {"kept"}


async def test_rebuild_that_did_not_happen_leaves_the_record(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row of `ops.deletion_log` is written after the rebuild: a rebuild that stayed busy or
    failed raises, the record stays without its row, and the reconcile finishes it."""
    learner, domain = await create_learner(pg_db), uuid4()
    await append_events(
        pg_db,
        learner,
        [_event("domain.created", {"domain_id": str(domain), "name": "d"}, "d", domain)],
    )
    record = DeletionRecord(uuid4(), learner, "domain", domain, NOW)
    journal, store = FakeDeletionJournal(), InMemoryObjectStore()
    journal.records.append(record)
    monkeypatch.setattr(domain_deletion, "_REBUILD_TRIES", 2)
    for status in ("busy", "failed"):

        async def not_rebuilt(*_a: object, _s: str = status, **_k: object) -> RebuildOutcome:
            return RebuildOutcome(_s)  # type: ignore[arg-type]

        monkeypatch.setattr(domain_deletion, "rebuild_learner", not_rebuilt)
        with pytest.raises(DeletionBusy):
            await apply_domain_record(pg_db, neo4j, settings, store, learner, record)
        assert await _count(pg_db, _COUNT_EVENTS, learner) == 0  # the rows are gone ...
        assert await _count(pg_db, _LOGGED, learner) == 0  # ... but the deletion is not done
        with pytest.raises(DeletionBusy):  # the reconcile reports it and goes on
            await reconcile_deletions(pg_db, neo4j, settings, journal, store, NOW)
    monkeypatch.undo()
    since = datetime(2026, 11, 1, tzinfo=UTC)
    assert await reconcile_deletions(pg_db, neo4j, settings, journal, store, since) == 1
    assert await _count(pg_db, _LOGGED, learner) == 1


async def test_account_deletion_outlives_no_job_of_a_batch_in_flight(
    pg_db: Db, neo4j: AsyncDriver
) -> None:
    """A batch of the dispatcher that commits while the account is being deleted leaves a job
    of a learner who is gone; the deletion waits for the mark of the dispatcher first."""
    learner = await create_learner(pg_db)
    dispatcher = DispatcherP4(Settings(), pg_db)
    inside, release = asyncio.Event(), asyncio.Event()

    async def batch() -> None:
        async with pg_db.transaction():
            await dispatcher._lock_mark(learner)
            await enqueue(pg_db, _job(learner, "from-the-batch", trigger_seq=1))
            inside.set()
            await release.wait()

    in_flight = asyncio.create_task(batch())
    await inside.wait()
    record = DeletionRecord(uuid4(), learner, "account", learner, NOW)
    deleting = asyncio.create_task(
        apply_account_record(pg_db, neo4j, InMemoryObjectStore(), learner, record)
    )
    await asyncio.sleep(0.5)
    assert not deleting.done()  # waits for the batch
    release.set()
    await asyncio.wait_for(asyncio.gather(in_flight, deleting), timeout=30)
    assert await _count(pg_db, _COUNT_JOBS, learner) == 0
