"""`agent.topic_split`, WP8.2 (ТЗ Ф4; Т-§4.11; issue #245): a topic of several skills becomes
`topic.split` (the biggest part keeps the id); a topic of one skill gets `topic.split` first,
then `skill.split` (D-A-226); a repeat writes nothing; an answer that does not pass the check
writes nothing. D-B-213."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import BaseModel

from quack.agents.schemas import SplitOutput, SplitPart
from quack.agents.topic_split import run_split
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import AgentRequested
from quack.events.log import NewEvent, append_events
from quack.events.records import AgentParamsRecord
from quack.graph.read import GraphReader
from quack.graph.read.models import SkillFact, TopicMemory
from quack.jobs.runner import JobContext
from quack.llm.gateway import LLMResult
from tests.db.fixtures import create_learner
from tests.support import graph_read as fx

SETTINGS = Settings()
TOPIC = UUID(int=0x701)
SKILLS = [UUID(int=0x711), UUID(int=0x712), UUID(int=0x713)]


@dataclass
class _Builder:
    parts: list[SplitPart]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_structured(
        self,
        role: str,
        version: int,
        model: type[BaseModel],
        variables: dict[str, Any],
        *a: Any,
        **kw: Any,
    ) -> LLMResult[Any]:
        self.calls.append(variables)
        return LLMResult(SplitOutput(parts=self.parts), False, "h", "m", None)


class _Reader:
    def __init__(self, skills: list[UUID]) -> None:
        self.skills = skills

    async def topic_memory(self, topic_ids: Any) -> list[TopicMemory]:
        return [
            TopicMemory(
                topic_id=TOPIC,
                name="Series",
                domain_id=fx.DOMAIN,
                skills=[
                    SkillFact(skill_id=s, name=f"skill {i}") for i, s in enumerate(self.skills)
                ],
            )
        ]


async def _run(db: Db, learner_id: UUID, builder: _Builder, skills: list[UUID]) -> list[str]:
    request_id = uuid4()
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="agent.requested",
                payload=AgentRequested(
                    request_id=request_id,
                    kind="topic_split",
                    params=AgentParamsRecord(topic_id=TOPIC),
                    needed_in_session=False,
                ),
                actor="code",
                idempotency_key=f"agent.requested:{request_id}",
                domain_id=fx.DOMAIN,
            )
        ],
    )
    ctx = cast(JobContext, SimpleNamespace(db=db, llm=builder, settings=SETTINGS))
    reader = cast(GraphReader, _Reader(skills))
    first = await run_split(ctx, reader, learner_id=learner_id, request_id=request_id)
    again = await run_split(ctx, reader, learner_id=learner_id, request_id=request_id)
    assert again == [] and len(builder.calls) == 1  # answered once: no second call, no writes
    return first


async def _types(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT type, payload FROM core.events WHERE learner_id = %(l)s "
        "AND type IN ('topic.split', 'skill.split', 'agent.completed') ORDER BY seq",
        {"l": learner_id},
    )


async def test_several_skills_become_topic_split_and_a_repeat_writes_nothing(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    builder = _Builder(
        [SplitPart(name="Tests", skills=["S1"]), SplitPart(name="Sums", skills=["S2", "S3"])]
    )

    written = await _run(pg_db, learner_id, builder, SKILLS)

    assert written == ["topic.split"]
    split, done = await _types(pg_db, learner_id)
    parts = split["payload"]["into"]
    assert split["payload"]["topic_id"] == str(TOPIC)
    assert [p["skill_ids"] for p in parts] == [[str(SKILLS[0])], [str(SKILLS[1]), str(SKILLS[2])]]
    assert parts[1]["topic_id"] == str(TOPIC) and parts[0]["topic_id"] != str(TOPIC)
    assert done["payload"]["ok"] is True


async def test_one_skill_topic_is_split_first_then_the_skill(pg_db: Db) -> None:
    """D-A-226: the topics first (the first part keeps the topic and the old skill), then the
    skill into one narrower skill per part, each in the topic of its part."""
    learner_id = await create_learner(pg_db)
    builder = _Builder([SplitPart(name="Tests", skills=[]), SplitPart(name="Sums", skills=[])])

    written = await _run(pg_db, learner_id, builder, SKILLS[:1])

    assert written == ["topic.split", "skill.split"]
    topic, skill, _ = await _types(pg_db, learner_id)
    parts = topic["payload"]["into"]
    assert [p["skill_ids"] for p in parts] == [[str(SKILLS[0])], []]
    assert parts[0]["topic_id"] == str(TOPIC) and parts[1]["topic_id"] != str(TOPIC)
    assert skill["payload"]["skill_id"] == str(SKILLS[0])
    assert [p["topic_id"] for p in skill["payload"]["into"]] == [p["topic_id"] for p in parts]


async def test_a_split_that_misses_a_skill_writes_nothing(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    builder = _Builder(
        [SplitPart(name="Tests", skills=["S1"]), SplitPart(name="Sums", skills=["S2"])]
    )

    assert await _run(pg_db, learner_id, builder, SKILLS) == []
    (done,) = await _types(pg_db, learner_id)
    assert (done["payload"]["ok"], done["payload"]["error"]) == (False, "split_not_valid")


async def test_a_topic_over_the_turn_limit_is_shown_to_the_model_whole(pg_db: Db) -> None:
    """The split has its own limit `topic_split_skills_k`, not `candidate_skills_k` of a teacher
    turn: a topic of 20 skills shows all 20, and the check «every skill in exactly one part»
    goes over all 20."""
    learner_id = await create_learner(pg_db)
    assert SETTINGS.topic_split_skills_k > SETTINGS.candidate_skills_k
    skills = [UUID(int=0x800 + i) for i in range(20)]
    assert len(skills) > SETTINGS.candidate_skills_k
    builder = _Builder(
        [
            SplitPart(name="Tests", skills=["S1", "S2"]),
            SplitPart(name="Sums", skills=[f"S{i}" for i in range(3, 21)]),
        ]
    )

    written = await _run(pg_db, learner_id, builder, skills)

    assert written == ["topic.split"]
    assert len(builder.calls[0]["candidates"].splitlines()) == 20
    split, done = await _types(pg_db, learner_id)
    parts = split["payload"]["into"]
    assert [len(p["skill_ids"]) for p in parts] == [2, 18]
    assert sorted(s for p in parts for s in p["skill_ids"]) == sorted(str(s) for s in skills)
    assert done["payload"]["ok"] is True


async def test_a_part_that_misses_a_shown_skill_is_not_valid(pg_db: Db) -> None:
    """20 skills are shown, so an answer that covers only the first 15 is not a split."""
    learner_id = await create_learner(pg_db)
    skills = [UUID(int=0x800 + i) for i in range(20)]
    builder = _Builder(
        [
            SplitPart(name="Tests", skills=["S1"]),
            SplitPart(
                name="Sums", skills=[f"S{i}" for i in range(2, SETTINGS.candidate_skills_k + 1)]
            ),
        ]
    )

    assert await _run(pg_db, learner_id, builder, skills) == []
    (done,) = await _types(pg_db, learner_id)
    assert (done["payload"]["ok"], done["payload"]["error"]) == (False, "split_not_valid")


async def test_skills_beyond_the_split_limit_stay_with_the_keeper_part(pg_db: Db) -> None:
    """A topic of 61 skills: 60 are shown (`topic_split_skills_k`), the model sees no `None:`
    lines, the check counts the shown skills, and the last one goes to the part that keeps the
    topic (D-B-223 п.12)."""
    learner_id = await create_learner(pg_db)
    limit = SETTINGS.topic_split_skills_k
    assert limit == 60
    skills = [UUID(int=0x800 + i) for i in range(limit + 1)]
    builder = _Builder(
        [
            SplitPart(name="Tests", skills=["S1"]),
            SplitPart(name="Sums", skills=[f"S{i}" for i in range(2, limit + 1)]),
        ]
    )

    written = await _run(pg_db, learner_id, builder, skills)

    assert written == ["topic.split"]
    assert "None" not in builder.calls[0]["candidates"]
    assert len(builder.calls[0]["candidates"].splitlines()) == limit
    split, done = await _types(pg_db, learner_id)
    parts = split["payload"]["into"]
    assert parts[1]["topic_id"] == str(TOPIC)  # the bigger part keeps the topic
    assert str(skills[limit]) == parts[1]["skill_ids"][-1]
    assert sorted(s for p in parts for s in p["skill_ids"]) == sorted(str(s) for s in skills)
    assert done["payload"]["ok"] is True
