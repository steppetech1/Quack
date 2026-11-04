"""ТЗ Ф4 §8.12 (WP25): the journal of deletions first, the bounds of `Т-§19.5`. T-DL3 — reconcile
and replay: `test_reconcile_and_replay` (graph/test_projection_runner.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from neo4j import AsyncDriver

from quack.config import Settings
from quack.db import Db
from quack.db.neo4j import literal, read_session
from quack.deletion.domain import delete_domain
from quack.events.log import NewEvent, append_events
from quack.graph.projection.runner import project_learner
from tests.support.auth import session_cookie
from tests.support.deletions import FakeDeletionJournal
from tests.support.storage import InMemoryObjectStore

_COUNTS = (
    "SELECT (SELECT count(*) FROM core.learners WHERE learner_id = %(l)s) AS learners, "
    "(SELECT count(*) FROM core.events WHERE learner_id = %(l)s) AS events, "
    "(SELECT count(*) FROM ops.deletion_log WHERE learner_id = %(l)s) AS logged"
)
_EVIDENCE = (
    "MATCH (e:Evidence)-[:ON]->(s:Skill {id: $id}) WHERE e.learnerId = $learner "
    "RETURN e.derivedKind AS kind"
)


def _new(event_type: str, payload: dict[str, object], key: str, domain: UUID) -> NewEvent:
    return NewEvent(
        type=event_type, payload=payload, actor="code", idempotency_key=key, domain_id=domain
    )


def _domain(domain: UUID, topic: UUID, skill: UUID, tag: str) -> list[NewEvent]:
    d = str(domain)
    return [
        _new("domain.created", {"domain_id": d, "name": tag}, f"d-{tag}", domain),
        _new("topic.created", {"topic_id": str(topic), "domain_id": d, "name": tag,
             "origin": "session"}, f"t-{tag}", domain),
        _new("skill.created", {"skill_id": str(skill), "topic_id": str(topic), "domain_id": d,
             "name": tag, "summary": "s", "status": "active", "origin": "session"},
             f"s-{tag}", domain),
    ]  # fmt: skip


async def _project(db: Db, neo4j: AsyncDriver, settings: Settings, learner: UUID) -> None:
    while (await project_learner(db, neo4j, settings, learner)).status == "applied":
        pass


async def test_journal_first(
    api_client: httpx.AsyncClient,
    domains_app: FastAPI,
    pg_db: Db,
    neo4j: AsyncDriver,
    seeded_learner: tuple[UUID, str],
    settings: Settings,
) -> None:
    """T-DL1 (`Д-§8.5`): the journal of R2 is down — no deletion of a domain, a topic or the
    account runs, the data are intact, the answer is `SERVICE_UNAVAILABLE`."""
    learner, token = seeded_learner
    domain, topic = uuid4(), uuid4()
    await append_events(pg_db, learner, _domain(domain, topic, uuid4(), "x"))
    await _project(pg_db, neo4j, settings, learner)
    before = await pg_db.fetch_one(_COUNTS, {"l": learner})
    domains_app.state.resources.deletions = FakeDeletionJournal(available=False)
    for path in (f"/v1/domains/{domain}", f"/v1/topics/{topic}", "/v1/learner"):
        answer = await api_client.delete(path, headers=session_cookie(token))
        assert answer.status_code == 503, (path, answer.text)
        assert answer.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert await pg_db.fetch_one(_COUNTS, {"l": learner}) == before
    assert before is not None and before["learners"] == 1 and before["logged"] == 0


async def test_topic_domain_account_bounds(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings, seeded_learner: tuple[UUID, str]
) -> None:
    """T-DL2 (`Т-§19.5`): the transfer from a deleted domain disappears after the rebuild. The
    bounds of each kind: `test_delete_domain`, `test_delete_topic` (the messages of the sessions
    stay), `test_delete_account` (graph/test_projection_runner.py) and T-E10 of the route."""
    learner, _ = seeded_learner
    gone, kept = uuid4(), uuid4()
    skill, other, task, session = uuid4(), uuid4(), uuid4(), uuid4()
    events = [
        *_domain(gone, uuid4(), skill, "gone"),
        *_domain(kept, uuid4(), other, "kept"),
        _new("edge.added", {"kind": "same_idea", "from_id": str(other), "to_id": str(skill),
             "origin": "model", "confidence": 0.9}, "edge", kept),
        _new("task.created", {"task_id": str(task), "family_id": str(uuid4()),
             "source": "teacher", "verification": "unchecked", "markup": {
                 "tests": [{"skill_id": str(skill), "level": 3}], "method_named": False,
                 "answer": {"type": "number", "value": {"latex": "2", "sympy": "2"}}}},
             "task", gone),
        NewEvent(type="session.started", payload={"session_id": str(session),
                 "domain_id": str(gone), "intent": "learn"}, actor="code",
                 idempotency_key="start", domain_id=gone, session_id=session),
        NewEvent(type="answer.submitted", payload={"task_id": str(task), "source": "chat",
                 "answer": {"kind": "number", "value": "2"}}, actor="learner",
                 idempotency_key="a", domain_id=gone, session_id=session),
    ]  # fmt: skip
    [*_, answer] = await append_events(pg_db, learner, events)
    verdict = {"answer_seq": answer.seq, "part": "main", "outcome": "correct", "method": "exact",
               "verification": "code"}  # fmt: skip
    recorded = NewEvent(
        type="verdict.recorded",
        payload=verdict,
        actor="code",
        idempotency_key="v",
        domain_id=gone,
        session_id=session,
    )
    await append_events(pg_db, learner, [recorded])
    await _project(pg_db, neo4j, settings, learner)

    async def kinds() -> list[str]:
        async with read_session(neo4j) as s:
            result = await s.run(literal(_EVIDENCE), id=str(other), learner=str(learner))
            return [r["kind"] async for r in result]

    assert await kinds() == ["transfer"]
    now = datetime(2026, 11, 4, tzinfo=UTC)
    journal, store = FakeDeletionJournal(), InMemoryObjectStore()
    done = await delete_domain(pg_db, neo4j, settings, journal, store, learner, gone, now)
    assert done is not None
    assert await kinds() == []
