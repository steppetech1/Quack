"""E2E: deletion of a topic, a domain and an account (ТЗ Ф4 §8.14, T-E12; WP25, Т-§19.5).

After the text journey the learner deletes the topic, then the domain, then the account. Each
deletion writes the record of the journal (R2) and the row `ops.deletion_log`; the messages of the
session stay with the deletion of the topic; the deletion of the account leaves nothing of the
learner but the log row; a new account with the same address starts empty."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from tests.e2e.journey import ok
from tests.e2e.system import Learner, System
from tests.e2e.text_journey import event_types, run_text_journey
from tests.support.deletions import FakeDeletionJournal


async def count(system: System, sql: str, **params: Any) -> int:
    row = await system.resources.db.fetch_one(sql, params)
    assert row is not None
    return int(row["n"])


async def delete_when_free(system: System, learner: Learner, url: str) -> dict[str, Any]:
    """The deletion takes the lock of the learner's projection; while the projector or a job
    holds it the API answers `503 repeat later` (WP25): repeat."""
    response = await system.client.delete(url, headers=learner.headers)
    for _ in range(20):
        if response.status_code != 503:
            break
        await asyncio.sleep(0.5)
        response = await system.client.delete(url, headers=learner.headers)
    return ok(response)


async def nodes_of(system: System, learner_id: UUID) -> int:
    async with system.neo4j.session() as session:
        result = await session.run(
            "MATCH (n) WHERE n.learnerId = $learner OR n.id = $learner RETURN count(n) AS n",
            learner=str(learner_id),
        )
        record = await result.single()
    assert record is not None
    return int(record["n"])


async def test_deletions_end_to_end(system: System) -> None:
    """T-E12."""
    journey = await run_text_journey(system)
    learner = journey.learner
    learner_id = learner.learner_id
    await system.client.post(f"/v1/sessions/{journey.session_id}/end", headers=learner.headers)
    await system.settle()
    journal = system.resources.deletions
    assert isinstance(journal, FakeDeletionJournal)
    messages = await count(
        system,
        "SELECT count(*) AS n FROM core.events WHERE learner_id = %(l)s AND type = 'message.sent'",
        l=learner_id,
    )
    assert messages == 1

    # The topic: the node goes, the messages of the session stay.
    deleted = await delete_when_free(system, learner, f"/v1/topics/{journey.topic_id}")
    assert UUID(deleted["deletion_id"])
    assert [(r.kind, r.target_id) for r in journal.records] == [("topic", journey.topic_id)]
    gone = await system.client.get(f"/v1/topics/{journey.topic_id}", headers=learner.headers)
    assert gone.status_code == 404
    assert (
        await count(
            system,
            "SELECT count(*) AS n FROM core.events WHERE learner_id = %(l)s "
            "AND type = 'message.sent'",
            l=learner_id,
        )
        == messages
    )

    # The domain: its content and its subgraph go.
    await delete_when_free(system, learner, f"/v1/domains/{journey.domain_id}")
    assert [r.kind for r in journal.records] == ["topic", "domain"]
    domains = ok(await system.client.get("/v1/domains", headers=learner.headers))
    assert domains["domains"] == [], domains
    assert (
        await count(
            system,
            "SELECT count(*) AS n FROM content.tasks WHERE learner_id = %(l)s "
            "AND domain_id = %(d)s",
            l=learner_id,
            d=journey.domain_id,
        )
        == 0
    )

    # The account: nothing of the learner is left but the log row of the deletion.
    await delete_when_free(system, learner, "/v1/learner")
    # A repeat after `503` writes the record again (the journal comes first); the one that
    # stands is the last, and the reconciliation removes the others (T-DL3).
    assert [r.kind for r in journal.records][:2] == ["topic", "domain"]
    accounts = [r for r in journal.records if r.kind == "account"]
    assert accounts and {r.kind for r in journal.records} == {"topic", "domain", "account"}
    assert (
        await count(
            system, "SELECT count(*) AS n FROM core.learners WHERE learner_id = %(l)s", l=learner_id
        )
        == 0
    )
    assert (
        await count(
            system, "SELECT count(*) AS n FROM core.events WHERE learner_id = %(l)s", l=learner_id
        )
        == 0
    )
    logged = await system.resources.db.fetch_all(
        "SELECT kind FROM ops.deletion_log WHERE learner_id = %(l)s ORDER BY deleted_at",
        {"l": learner_id},
    )
    assert [r["kind"] for r in logged] == ["topic", "domain", "account"]
    last = await system.resources.db.fetch_one(
        "SELECT deletion_id FROM ops.deletion_log WHERE learner_id = %(l)s AND kind = 'account'",
        {"l": learner_id},
    )
    assert last is not None and last["deletion_id"] == accounts[-1].deletion_id
    assert await nodes_of(system, learner_id) == 0
    after = await system.client.get("/v1/domains", headers=learner.headers)
    assert after.status_code == 401

    # The same address signs in again: a new, empty account.
    again = await system.sign_in_by_link(learner.email)
    assert again.learner_id != learner_id
    fresh = ok(await system.client.get("/v1/domains", headers=again.headers))
    assert fresh["domains"] == [], fresh
    assert [t for _, t in await event_types(system, again)] == ["consent.given"]
