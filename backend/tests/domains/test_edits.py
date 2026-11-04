"""ТЗ Ф4 §8.6 (WP8): the edits of the learner and the state at moment N."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from neo4j import AsyncDriver

from quack.config import Settings
from quack.db import Db
from quack.db.neo4j import literal, read_session
from quack.events.log import NewEvent, append_events
from quack.graph.projection.runner import project_learner
from quack.jobs.rules_p4 import plan_jobs
from tests.review.test_session_summary import (
    DOMAIN,
    SKILL,
    TOPIC,
    late_dispute_journey,
    level3,
    solved_journey,
)

ANSWER_SEQ = 6  # the answer of `solved_journey` (its docstring)
_SKILL = (
    "MATCH (k:Skill {id: $id}) WHERE k.learnerId = $learner "
    "OPTIONAL MATCH (e:Evidence)-[:ON]->(k) "
    "RETURN k.l3Status AS l3, collect([e.sourceSeq, e.status, e.gapType]) AS evidence"
)
_DERIVED = (
    "MATCH (e:Evidence)-[:DERIVED_FROM]->(d:Evidence) WHERE e.learnerId = $learner "
    "RETURN e.status AS status, d.status AS source"
)
_TOPICS = (
    "MATCH (t:Topic) WHERE t.learnerId = $learner RETURN t.id AS id, t.name AS name, "
    "t.status AS status"
)
_ALIAS = (
    "MATCH (a:Alias {id: $id})-[:ALIAS_OF]->(t:Topic) WHERE a.learnerId = $learner "
    "RETURN t.id AS target"
)


def _edit(event_type: str, payload: Mapping[str, object], key: str) -> NewEvent:
    return NewEvent(
        type=event_type,
        payload=dict(payload),
        actor="learner",
        idempotency_key=key,
        domain_id=DOMAIN,
    )


async def _apply(db: Db, neo4j: AsyncDriver, settings: Settings, learner: UUID, *new: NewEvent):
    stored = await append_events(db, learner, list(new))
    while (await project_learner(db, neo4j, settings, learner)).status == "applied":
        pass
    return stored


async def _rows(neo4j: AsyncDriver, query: str, **params: str) -> list[dict[str, Any]]:
    async with read_session(neo4j) as session:
        result = await session.run(literal(query), dict(params))
        return [dict(r) async for r in result]


async def _skill(neo4j: AsyncDriver, learner: UUID) -> tuple[str, list[list[Any]]]:
    (row,) = await _rows(neo4j, _SKILL, id=str(SKILL), learner=str(learner))
    return row["l3"], [e for e in row["evidence"] if e[0] is not None]


async def test_dispute_cascade(pg_db: Db, neo4j: AsyncDriver, settings: Settings) -> None:
    """T-ED1: the disputed verdict's evidence and the evidence derived from it do not count; the
    status of the level is folded again (proven → not proven). The derivation chain of one fold:
    `test_dispute_of_evidence_misconception_or_verdict` (graph/test_projection_rules3.py)."""
    learner, _ = await solved_journey(pg_db, neo4j, settings)
    assert (await _skill(neo4j, learner))[0] == "proven"
    target = {"target_kind": "verdict", "target_id": f"{ANSWER_SEQ}:main"}
    await _apply(pg_db, neo4j, settings, learner, _edit("dispute.filed", target, "disp"))
    status, evidence = await _skill(neo4j, learner)
    assert status != "proven"
    assert evidence and {e[1] for e in evidence if e[0] == ANSWER_SEQ} == {"disputed"}
    derived = await _rows(neo4j, _DERIVED, learner=str(learner))
    assert all(r["status"] == "disputed" for r in derived if r["source"] == "disputed")


async def test_typo_reclassifies(pg_db: Db, neo4j: AsyncDriver, settings: Settings) -> None:
    """T-ED2: «описка» — the verdict's evidence is excluded, its gap type is `slip`."""
    learner, _ = await solved_journey(pg_db, neo4j, settings)
    typo = {"answer_seq": ANSWER_SEQ, "part": "main"}
    await _apply(pg_db, neo4j, settings, learner, _edit("typo.declared", typo, "typo"))
    status, evidence = await _skill(neo4j, learner)
    assert status != "proven"
    of_answer = [e for e in evidence if e[0] == ANSWER_SEQ]
    assert of_answer and all(e[1:] == ["disputed", "slip"] for e in of_answer)


async def test_topic_edits(pg_db: Db, neo4j: AsyncDriver, settings: Settings) -> None:
    """T-ED3: rename, merge (the old id is an alias of the target), hide; `episode.reassigned`
    re-extracts the episode restricted to the topic. The routes and their checks:
    `test_topic_edits_check_the_topic_and_append_one_event` (graph/test_projection_runner.py)."""
    learner, _ = await solved_journey(pg_db, neo4j, settings)
    other = uuid4()
    created = {"topic_id": str(other), "domain_id": str(DOMAIN), "name": "O", "origin": "session"}
    await _apply(
        pg_db,
        neo4j,
        settings,
        learner,
        NewEvent(
            type="topic.created",
            payload=created,
            actor="code",
            idempotency_key="o",
            domain_id=DOMAIN,
        ),
        _edit("topic.renamed", {"topic_id": str(TOPIC), "name": "G"}, "rename"),
        _edit("topic.merged", {"from_id": str(other), "into_id": str(TOPIC)}, "merge"),
        _edit("topic.renamed", {"topic_id": str(other), "name": "H"}, "by-alias"),
        _edit("topic.hidden", {"topic_id": str(TOPIC)}, "hide"),
    )
    (topic,) = await _rows(neo4j, _TOPICS, learner=str(learner))
    assert (topic["id"], topic["name"], topic["status"]) == (str(TOPIC), "H", "hidden")
    alias = await _rows(neo4j, _ALIAS, id=str(other), learner=str(learner))
    assert alias == [{"target": str(TOPIC)}]
    reassigned = {"episode_seq": ANSWER_SEQ, "topic_id": str(TOPIC)}
    [event] = await append_events(
        pg_db, learner, [_edit("episode.reassigned", reassigned, "reassign")]
    )
    (job,) = plan_jobs(event, settings)
    assert (job.kind, job.payload) == ("extract.episode", reassigned | {"trigger_seq": event.seq})


async def test_known_claimed_no_state_change(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """T-ED4: «я это знаю» changes no state of the skills and puts `check.known_claim`; the job
    ends with `agent.completed` and tasks — B's `test_check_known_claim_job`
    (agents/test_task_agents.py)."""
    learner, _ = await solved_journey(pg_db, neo4j, settings)
    before = await _skill(neo4j, learner)
    [event] = await _apply(
        pg_db, neo4j, settings, learner, _edit("known.claimed", {"topic_id": str(TOPIC)}, "known")
    )
    assert await _skill(neo4j, learner) == before
    assert [p.kind for p in plan_jobs(event, settings)] == ["check.known_claim"]


async def test_state_at_n_with_late_dispute(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """T-ED5: evidence disputed after N is valid at N; the `summary` of the session shows it
    «до» (proven) and «после» (the dispute applied — no longer proven)."""
    reader, learner, summary, stored = await late_dispute_journey(pg_db, neo4j, settings)
    assert [e.type for e in stored][-1] == "dispute.filed"
    assert summary.before_seq == 9
    at_n = await reader.state_at(learner, summary.before_seq, [SKILL])
    assert at_n.skills[0].levels[2].status == "proven"
    assert level3(summary, "before") == "proven"
    assert level3(summary, "after") != "proven"
    assert summary.skills[0].changed
