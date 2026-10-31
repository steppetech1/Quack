"""`graph.curate`: deterministic LLM idempotency key, `actor` split between deterministic
steps (`code`) and model decisions (`curator@1`), and the projector wakeup after commit
(РТЗ D-2, D-4, D-7.4).
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from uuid import UUID, uuid4

import orjson
from redis.asyncio import Redis
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import PROJECTOR_WAKEUP_CHANNEL
from quack.domains.curation import curate_domain_graph
from quack.domains.schemas import CuratorMerge, CuratorOutput
from quack.jobs.queue import ClaimedJob
from quack.jobs.runner import JobContext


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __aiter__(self) -> _Result:
        self._it = iter(self._rows)
        return self

    async def __anext__(self) -> Any:
        try:
            row = next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None
        return _Record(row)

    async def single(self) -> Any:
        return _Record(self._rows[0]) if self._rows else None


class _Record(dict):  # type: ignore[type-arg]
    def data(self) -> dict[str, Any]:
        return dict(self)


class _FakeNeo4jSession:
    def __init__(self, skills: list[dict[str, Any]]) -> None:
        self._skills = skills

    async def __aenter__(self) -> _FakeNeo4jSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def run(self, query: str, params: dict[str, Any]) -> _Result:
        if "RETURN d.name" in query:
            return _Result([{"name": "Algebra"}])
        if "(sk:Skill" in query:
            return _Result(self._skills)
        return _Result([])


class _FakeDriver:
    def __init__(self, skills: list[dict[str, Any]]) -> None:
        self._skills = skills

    def session(self, **_config: Any) -> _FakeNeo4jSession:
        return _FakeNeo4jSession(self._skills)

    async def close(self) -> None:
        return None


class _SpyLLM:
    def __init__(self, output: CuratorOutput) -> None:
        self.output = output
        self.keys: list[str] = []

    async def generate_structured(self, **kwargs: Any) -> Any:
        self.keys.append(kwargs["idempotency_key"])

        class _Res:
            value = self.output

        return _Res()


def _skill(sid: UUID, topic: UUID, name: str, status: str, evidence: int = 0) -> dict[str, Any]:
    return {
        "id": str(sid),
        "props": {
            "status": status,
            "name": name,
            "normName": name.lower(),
            "topicId": str(topic),
            "evidenceCount": evidence,
        },
    }


def _job(learner_id: UUID, domain_id: UUID) -> ClaimedJob:
    return ClaimedJob(
        id=uuid4(),
        kind="graph.curate",
        learner_id=learner_id,
        lock_key=f"curate:{domain_id}",
        coalesce_key=None,
        priority=0,
        payload={"domain_id": str(domain_id)},
        attempt=1,
        max_attempts=3,
    )


async def test_curator_key_actor_and_wakeup(
    db: SessionFactory, settings: Settings, redis: Redis
) -> None:
    learner_id, domain_id, topic = uuid4(), uuid4(), uuid4()
    active, dup, promo, left = uuid4(), uuid4(), uuid4(), uuid4()
    skills = [
        _skill(active, topic, "Fractions", "active"),
        _skill(dup, topic, "Fractions", "provisional"),
        _skill(promo, topic, "Ratios", "provisional", evidence=2),
        _skill(left, topic, "Decimals", "provisional"),
    ]
    output = CuratorOutput(
        skill_merges=[CuratorMerge(source_id=left, target_id=active, reason="same")]
    )
    llm = _SpyLLM(output)
    ctx = JobContext(settings=settings, session_factory=db)
    job = _job(learner_id, domain_id)

    pubsub = redis.pubsub()
    await pubsub.subscribe(PROJECTOR_WAKEUP_CHANNEL)
    await pubsub.get_message(timeout=1)  # subscribe confirmation

    await curate_domain_graph(job, ctx, driver=_FakeDriver(skills), redis=redis, llm=llm)  # type: ignore[arg-type]

    message = None
    for _ in range(20):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.25)
        if message:
            break
        await asyncio.sleep(0.05)
    await pubsub.aclose()  # type: ignore[no-untyped-call]
    assert message is not None, "projector:wakeup not published after commit"

    async with db() as session:
        rows = (
            await session.execute(
                text("SELECT type, actor FROM core.events WHERE learner_id = :l"),
                {"l": learner_id},
            )
        ).all()
    actors = {(r.type, r.actor) for r in rows}
    assert actors == {
        ("skill.merged", "code"),
        ("skill.promoted", "code"),
        ("skill.merged", "curator@1"),
    }
    assert len(rows) == 3

    expected = hashlib.sha256(
        orjson.dumps(
            {
                "prov": sorted(str(i) for i in (dup, promo, left)),
                "act": [str(active)],
            }
        )
    ).hexdigest()[:16]
    assert llm.keys == [f"curator:{domain_id}:{expected}"]

    # Second run over the same candidates: same key; changed candidates: different key.
    await curate_domain_graph(job, ctx, driver=_FakeDriver(skills), redis=redis, llm=llm)  # type: ignore[arg-type]
    assert llm.keys[1] == llm.keys[0]
    extra = [*skills, _skill(uuid4(), topic, "Percent", "provisional")]
    await curate_domain_graph(job, ctx, driver=_FakeDriver(extra), redis=redis, llm=llm)  # type: ignore[arg-type]
    assert llm.keys[2] != llm.keys[0]
