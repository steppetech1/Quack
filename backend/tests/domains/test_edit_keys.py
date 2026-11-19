"""ТЗ Ф4 WP8.2, WP6.3, WP17.2 (`Т-§4.11`, D-A-193 п.3): the key of an edit of the learner follows
the log, so a value set again later is a new event; «Сегодня» writes one `today.shown` for one
set of items."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from neo4j import AsyncDriver

from quack.config import Settings
from quack.db import Db
from quack.domains import topic_edits as edits
from quack.review import today as review
from tests.db.fixtures import create_learner


async def _names(db: Db, learner: UUID, kind: str, field: str) -> list[object]:
    rows = await db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(l)s AND type = %(t)s ORDER BY seq",
        {"l": learner, "t": kind},
    )
    return [r["payload"][field] for r in rows]


async def test_a_name_set_again_is_a_new_event(pg_db: Db, neo4j: AsyncDriver) -> None:
    """The key `old->new` repeated with A->B->A->B: the fourth request found the event of the
    second ... of the first, answered success, and the name stayed as it was."""
    learner, topic = await create_learner(pg_db), uuid4()
    async with neo4j.session() as session:
        await session.run(
            "CREATE (:Topic {id: $t, learnerId: $l, name: 'A', domainId: 'd'})",
            t=str(topic),
            l=str(learner),
        )
    seqs = [await edits.rename(pg_db, neo4j, None, learner, topic, n) for n in "BABB"]
    assert seqs == [1, 2, 3, 3]  # the last one is a repeat of the value just set
    assert await _names(pg_db, learner, "topic.renamed", "name") == ["B", "A", "B"]


async def test_a_deadline_and_a_scope_set_again_are_new_events(
    pg_db: Db, neo4j: AsyncDriver
) -> None:
    learner, hub, topic, other = await create_learner(pg_db), uuid4(), uuid4(), uuid4()
    async with neo4j.session() as session:
        await session.run(
            "CREATE (:Hub {id: $h, learnerId: $l, domainId: 'd', deadlineAtMs: 0}), "
            "(:Topic {id: $t, learnerId: $l, domainId: 'd'}), "
            "(:Topic {id: $o, learnerId: $l, domainId: 'd'})",
            h=str(hub),
            t=str(topic),
            o=str(other),
            l=str(learner),
        )
    for value in (1000, 2000, 1000, 2000, 2000):
        await edits.edit_hub(pg_db, neo4j, None, learner, hub, edits.EditHub(deadline_at_ms=value))
    assert await _names(pg_db, learner, "hub.deadline_set", "deadline_at_ms") == [
        1000, 2000, 1000, 2000,
    ]  # fmt: skip
    first = edits.EditHub(topics=[edits.HubTopicLevels(topic_id=topic, required_levels=[3])])
    second = edits.EditHub(topics=[edits.HubTopicLevels(topic_id=other)])
    for body in (first, second, first, first):
        await edits.edit_hub(pg_db, neo4j, None, learner, hub, body)
    scopes = await _names(pg_db, learner, "hub.topics_changed", "topics")
    assert len(scopes) == 3 and scopes[0] == scopes[2]


async def test_a_reassigned_episode_goes_back(pg_db: Db, neo4j: AsyncDriver) -> None:
    learner, first, second = await create_learner(pg_db), uuid4(), uuid4()
    async with neo4j.session() as session:
        await session.run(
            "UNWIND [$a, $b] AS id CREATE (:Topic {id: id, learnerId: $l, domainId: 'd'})",
            a=str(first),
            b=str(second),
            l=str(learner),
        )
    closed = {"from_seq": 1, "to_seq": 1, "has_content": True}
    from quack.events.log import NewEvent, append_events

    await append_events(
        pg_db,
        learner,
        [NewEvent(type="episode.closed", payload=closed, actor="system", idempotency_key="e")],
    )
    for topic in (first, second, first):
        assert await edits.reassign(pg_db, neo4j, None, learner, 1, topic) is not None
    assert await _names(pg_db, learner, "episode.reassigned", "topic_id") == [
        str(first), str(second), str(first),
    ]  # fmt: skip


async def test_today_shown_once_for_one_set_of_items(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The digest of the key held the scores to six places and the priority moves with `now`:
    nearly every GET wrote a new `today.shown` and put a `today.prepare` job."""
    learner, domain, topic = await create_learner(pg_db), uuid4(), uuid4()
    skills = [uuid4(), uuid4()]
    async with neo4j.session() as session:
        await session.run(
            "CREATE (d:Domain {id: $d, learnerId: $l}), (t:Topic {id: $t, learnerId: $l}) "
            "WITH d, t UNWIND $s AS id CREATE (t)-[:COVERS]->"
            "(:Skill {id: id, learnerId: $l, domainId: $d})",
            d=str(domain),
            t=str(topic),
            s=[str(s) for s in skills],
            l=str(learner),
        )
    priorities = {"a": 9.0, "b": 8.0}

    def candidates(count: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(skill_id=skill, priority=prio, reasons=["retention"])
            for skill, prio in zip(skills[:count], priorities.values(), strict=False)
        ]

    shown = {"count": 2}

    class Reader:
        async def review_candidates(self, _learner: UUID, _now_ms: int) -> list[SimpleNamespace]:
            return candidates(shown["count"])

    monkeypatch.setattr(review, "create_graph_reader", lambda *_a, **_k: Reader())

    async def count() -> int:
        row = await pg_db.fetch_one(
            "SELECT count(*) AS n FROM core.events WHERE learner_id = %(l)s "
            "AND type = 'today.shown'",
            {"l": learner},
        )
        assert row is not None
        return int(row["n"])

    for hour, drift in ((9, 0.0), (10, 0.0123), (11, 0.4)):
        priorities["a"], priorities["b"] = 9.0 + drift, 8.0 - drift / 3
        now = datetime(2026, 11, 13, hour, tzinfo=UTC)
        view = await review.today(pg_db, neo4j, None, settings, learner, domain, now)
        assert view is not None and len(view.items) == 2
    assert await count() == 1  # the same items today: one show, one `today.prepare`
    shown["count"] = 1  # another set of items is another show
    await review.today(pg_db, neo4j, None, settings, learner, domain, now)
    assert await count() == 2
