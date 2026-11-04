"""WP23.2: the topic's notes refreshed by `artifact_writer@1` (ТЗ Ф4 WP23.2; Т-§17.3; D-C-139)."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

from quack.artifacts.documents import get_artifact
from quack.artifacts.topic_refresh import refresh_topic_notes, worked_phrasings, writer_input
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.graph.read import TopicMemory, TopicRequires
from quack.graph.read.materials import ChunkHit
from quack.graph.read.models import EvidenceFact, LevelFact, MisconceptionFact, SkillFact
from quack.jobs.runner import JOB_HANDLERS
from tests.db.fixtures import create_learner
from tests.events.samples import sample_payload
from tests.support.graph_read import FixedGraphReader
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources

LIMIT, CHAIN, MISCONCEPTION, CHUNK = uuid4(), uuid4(), uuid4(), uuid4()
DOMAIN, SEQUENCES = uuid4(), uuid4()
HIT = ChunkHit(
    chunk_id=CHUNK, material_id=uuid4(), page=3, text="A limit is…", teaches_focus=True, score=None
)


class TopicReader(FixedGraphReader):
    """The topic «Limits»: `LIMIT` solved on its own after a turn that showed it (Т1, seq
    `success_seq`); `CHAIN` needed a hint and is proven on level 1."""

    def __init__(self, topic_id: UUID, success_seq: int) -> None:
        super().__init__()
        self.topic_id, self.success_seq = topic_id, success_seq

    async def topic_requires(self, domain_id: UUID) -> list[TopicRequires]:
        return [TopicRequires(from_topic_id=self.topic_id, to_topic_id=SEQUENCES, weight=1)]

    async def topic_memory(self, topic_ids: Sequence[UUID]) -> list[TopicMemory]:
        if SEQUENCES in topic_ids:
            return [TopicMemory(topic_id=SEQUENCES, name="Sequences", domain_id=DOMAIN)]
        if self.topic_id not in topic_ids:
            return []
        return [
            TopicMemory(
                topic_id=self.topic_id,
                name="Limits",
                domain_id=DOMAIN,
                skills=[
                    SkillFact(skill_id=LIMIT, name="Limit of a sequence"),
                    SkillFact(
                        skill_id=CHAIN,
                        name="Chain rule",
                        levels=[LevelFact(level=1, status="proven")],
                    ),
                ],
                misconceptions=[
                    MisconceptionFact(
                        misconception_id=MISCONCEPTION, skill_id=LIMIT, status="suspected",
                        statement="the limit is the last term", quotes=["lim = a_100"],
                    )
                ],
            )
        ]  # fmt: skip

    async def skill_history(
        self, skill_id: UUID, level: Any = None, limit: int = 10
    ) -> list[EvidenceFact]:
        def fact(seq: int, **extra: Any) -> EvidenceFact:
            return EvidenceFact(
                evidence_id=uuid4(), skill_id=skill_id, level=1, outcome="correct",
                source="code", seq=seq, occurred_at_ms=seq, **extra,
            )  # fmt: skip

        if skill_id == LIMIT:
            return [fact(self.success_seq)]
        return [fact(self.success_seq, assistance="hint", hint_step=2)]


async def _topic(db: Db, learner: UUID) -> UUID:
    topic_id, domain_id = uuid4(), uuid4()
    created = NewEvent(
        type="topic.created",
        payload={
            "topic_id": topic_id, "domain_id": domain_id, "name": "Limits", "origin": "session"
        },
        actor="code",
        idempotency_key=f"topic:{topic_id}",
        domain_id=domain_id,
    )  # fmt: skip
    await append_events(db, learner, [created])
    return topic_id


async def _turn(db: Db, learner: UUID, text: str, skill: UUID) -> int:
    payload = copy.deepcopy(sample_payload("turn.committed"))
    payload["text"] = text
    payload["markup"]["exposures"] = [
        {"skill_id": str(skill), "kind": "explained", "approach": "example_first"}
    ]
    (stored,) = await append_events(
        db,
        learner,
        [NewEvent(type="turn.committed", payload=payload, actor="teacher@2", idempotency_key=text)],
    )
    return stored.seq


async def _prefetch(skill_ids: Sequence[UUID]) -> list[ChunkHit]:
    return [HIT] if LIMIT in skill_ids else []


async def test_worked_phrasing_is_the_last_showing_before_a_success(pg_db: Db) -> None:
    """Т-§17.3 п.3: the last turn that showed the skill before its Т1 success, not after."""
    learner = await create_learner(pg_db)
    await _turn(pg_db, learner, "Think of the terms piling up.", LIMIT)
    await _turn(pg_db, learner, "The terms approach a number.", LIMIT)
    success = await _turn(pg_db, learner, "Unrelated chain turn.", CHAIN) + 1
    await _turn(pg_db, learner, "After the success.", LIMIT)
    reader = TopicReader(uuid4(), success)
    found = await worked_phrasings(pg_db, reader, learner_id=learner, skill_ids=[LIMIT, CHAIN])
    assert found == {LIMIT: "The terms approach a number."}  # CHAIN's success needed a hint


async def test_writer_input_has_the_four_layers(pg_db: Db, settings: Settings) -> None:
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    success = await _turn(pg_db, learner, "The terms approach a number.", LIMIT) + 1
    context = await writer_input(
        pg_db, TopicReader(topic_id, success), _prefetch, settings,
        learner_id=learner, topic_id=topic_id,
    )  # fmt: skip
    assert context is not None
    v = context.variables
    assert v["artifact_type"] == "notes" and v["learner_skill"] == "None."
    assert "S1: Limit of a sequence" in v["topic_graph"] and "S2: Chain rule" in v["topic_graph"]
    assert "Builds on: Sequences" in v["topic_graph"]
    assert v["material_chunks"].startswith("P1 (page 3):")
    assert v["worked_phrasings"] == "S1: The terms approach a number."
    assert (
        'Misconception M1: the limit is the last term — in the learner\'s words: "lim = a_100"'
        in v["personal"]
    )
    assert "Hints were needed on: S2" in v["personal"] and "Known firmly: S2" in v["personal"]
    assert v["current_blocks"] == "None yet."
    assert await writer_input(
        pg_db, TopicReader(uuid4(), 1), _prefetch, settings, learner_id=learner, topic_id=topic_id
    ) is None  # fmt: skip


async def test_refresh_writes_valid_blocks_and_retries_once(pg_db: Db, settings: Settings) -> None:
    """Т-§17.3: an unknown label is a violation — one more generation, then the element is
    dropped; labels become ids; a repeated job writes no new version."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    good = {"updates": None, "kind": "definition", "content": "A limit…", "sources": ["P1"],
            "skills": ["S1"]}  # fmt: skip
    bad = {"updates": None, "kind": "why", "content": "Because…", "sources": ["P9"], "skills": []}
    provider = ScriptedLLMProvider()
    provider.respond("ArtifactWriterOutput", lambda _r: {"blocks": [good, bad], "cards": []})
    resources = build_app_resources(settings, None, None, db=pg_db, provider=provider)
    reader = TopicReader(topic_id, 1)
    version = await refresh_topic_notes(
        resources.db, resources.llm, reader, _prefetch, settings,
        learner_id=learner, topic_id=topic_id, key="job-1",
    )  # fmt: skip
    assert version == 1
    calls = [c for c in provider.calls if c.schema_name == "ArtifactWriterOutput"]
    assert len(calls) == 2
    notes = await get_artifact(pg_db, learner_id=learner, kind="notes", owner_id=topic_id)
    (block,) = notes.blocks
    assert block.kind == "definition" and block.owner == "system"
    assert block.skills == [str(LIMIT)]
    assert block.sources == [
        {"chunk_id": str(CHUNK), "material_id": str(HIT.material_id), "page": 3}
    ]

    again = await refresh_topic_notes(
        resources.db, resources.llm, reader, _prefetch, settings,
        learner_id=learner, topic_id=topic_id, key="job-1",
    )  # fmt: skip
    assert again == 1  # the same answers through the outbox: no new version


def test_job_is_registered() -> None:
    assert JOB_HANDLERS["artifact.topic_refresh"].__module__ == "quack.artifacts.topic_refresh"
