"""ТЗ Ф4 WP8.3, §8.6 T-ED5 (helpers), §8.16 T-E13 (part: the codes «до» and «после» of the
function; the route is B's): the report «до и после» of a session in codes (D-A-213, D-A-214)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from neo4j import AsyncDriver

from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.graph.projection.runner import project_learner
from quack.graph.read.neo4j_reader import Neo4jGraphReader
from quack.graph.read.summary import SessionNotFoundError, SessionSummary, session_summary
from tests.db.fixtures import create_learner

DOMAIN, TOPIC, SKILL, TASK, FAMILY = (uuid4() for _ in range(5))
FIRST, SECOND = uuid4(), uuid4()


def _new(event_type: str, payload: dict[str, object], key: str, **envelope: object) -> NewEvent:
    return NewEvent(type=event_type, payload=payload, actor="code", idempotency_key=key, **envelope)  # type: ignore[arg-type]


def _session(session: UUID, tag: str) -> list[NewEvent]:
    return [
        _new(
            "session.started",
            {"session_id": str(session), "domain_id": str(DOMAIN), "intent": "learn"},
            f"start-{tag}",
            domain_id=DOMAIN,
            session_id=session,
        )
    ]


def _structure() -> list[NewEvent]:
    d, s = str(DOMAIN), str(SKILL)
    return [
        _new("domain.created", {"domain_id": d, "name": "Algebra"}, "d", domain_id=DOMAIN),
        _new(
            "topic.created",
            {"topic_id": str(TOPIC), "domain_id": d, "name": "F", "origin": "session"},
            "t",
            domain_id=DOMAIN,
        ),
        _new(
            "skill.created",
            {
                "skill_id": s,
                "topic_id": str(TOPIC),
                "domain_id": d,
                "name": "add",
                "summary": "s",
                "status": "active",
                "origin": "session",
            },
            "s",
            domain_id=DOMAIN,
        ),
        _new(
            "task.created",
            {
                "task_id": str(TASK),
                "family_id": str(FAMILY),
                "source": "teacher",
                "verification": "unchecked",
                "markup": {
                    "tests": [{"skill_id": s, "level": 3}],
                    "method_named": False,
                    "answer": {"type": "number", "value": {"latex": "2", "sympy": "2"}},
                },
            },
            "task",
            domain_id=DOMAIN,
        ),
    ]


def _solved(session: UUID) -> list[NewEvent]:
    """Start, an answer, a correct verdict, closing, end. The answer is the 7th event of the
    learner (4 of the structure + the start + the answer)."""
    env = {"domain_id": DOMAIN, "session_id": session}
    return [
        *_session(session, "first"),
        _new(
            "answer.submitted",
            {"task_id": str(TASK), "source": "chat", "answer": {"kind": "number", "value": "2"}},
            "a",
            **env,
        ),
        _new(
            "verdict.recorded",
            {
                "answer_seq": 6,
                "part": "main",
                "outcome": "correct",
                "method": "exact",
                "verification": "code",
            },
            "v",
            **env,
        ),
        _new("task.closed", {"task_id": str(TASK), "how": "solved"}, "c", **env),
        _new("session.ended", {"session_id": str(session), "reason": "learner"}, "e", **env),
    ]


async def _project(db: Db, neo4j: AsyncDriver, settings: Settings, learner: UUID) -> None:
    while (await project_learner(db, neo4j, settings, learner)).status == "applied":
        pass


def reader_of(db: Db, neo4j: AsyncDriver, settings: Settings, learner: UUID) -> Neo4jGraphReader:
    return Neo4jGraphReader(neo4j, settings, learner_id=learner, db=db)


async def solved_journey(
    db: Db, neo4j: AsyncDriver, settings: Settings
) -> tuple[UUID, SessionSummary]:
    learner = await create_learner(db)
    await append_events(db, learner, [*_structure(), *_solved(FIRST)])
    await _project(db, neo4j, settings, learner)
    return learner, await session_summary(
        db, reader_of(db, neo4j, settings, learner), learner, FIRST
    )


async def test_summary_before_and_after_differ_for_solved_task(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """T-E13 (part): the level the solved task tests is untested before the session and proven
    after it; what was done comes from the journal; there is no text, only codes."""
    _, summary = await solved_journey(pg_db, neo4j, settings)
    assert (summary.started_seq, summary.before_seq, summary.ended_reason) == (5, 4, "learner")
    assert (summary.domain_id, summary.intent) == (DOMAIN, "learn")
    (change,) = summary.skills
    assert (change.skill_id, change.topic_id, change.changed) == (SKILL, TOPIC, True)
    assert change.before is not None and change.after is not None
    assert change.before.levels[2].status == "not_tested"
    assert (change.after.levels[2].status, change.after.levels[2].basis) == ("proven", "direct")
    (task,) = summary.tasks
    assert (task.task_id, task.answers, task.last_outcome, task.closed) == (
        TASK,
        1,
        "correct",
        "solved",
    )
    assert "name" not in change.model_dump()


async def test_a_session_of_another_learner_is_not_found(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """Isolation (law 1): the same session id read by another learner, and an unknown one."""
    learner, _ = await solved_journey(pg_db, neo4j, settings)
    other = await create_learner(pg_db)
    for who, session in ((other, FIRST), (learner, uuid4())):
        with pytest.raises(SessionNotFoundError):
            await session_summary(pg_db, reader_of(pg_db, neo4j, settings, who), who, session)


def late_dispute_events() -> list[NewEvent]:
    """Session one proves level 3 (answer 6); session two starts at 10 and touches the skill; the
    learner disputes the verdict of the first answer at 12, after N = 9."""
    env = {"domain_id": DOMAIN, "session_id": SECOND}
    return [
        *_structure(),
        *_solved(FIRST),
        *_session(SECOND, "second"),
        _new("hint.shown", {"task_id": str(TASK), "step": 1, "source": "teacher"}, "h", **env),
        _new(
            "dispute.filed",
            {"target_kind": "verdict", "target_id": "6:main"},
            "disp",
            domain_id=DOMAIN,
        ),
    ]


def level3(summary: SessionSummary, when: str) -> str:
    codes = summary.skills[0].before if when == "before" else summary.skills[0].after
    assert codes is not None
    return codes.levels[2].status


async def late_dispute_journey(
    db: Db, neo4j: AsyncDriver, settings: Settings
) -> tuple[Neo4jGraphReader, UUID, SessionSummary, list[StoredEvent]]:
    learner = await create_learner(db)
    stored = await append_events(db, learner, late_dispute_events())
    await _project(db, neo4j, settings, learner)
    reader = reader_of(db, neo4j, settings, learner)
    return reader, learner, await session_summary(db, reader, learner, SECOND), stored
