"""Shared worker resources and consistent topic-context read of `turn.extract` (S-5.2, S-5.3)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.jobs.queue import ClaimedJob, claim_next_job
from quack.jobs.runner import JobContext
from quack.session import workers
from quack.session.schemas import ExtractorOutput, TeacherOutput
from tests.session.helpers import parse_sse

pytest_plugins = ["tests.session.fixtures"]


def _job(kind: str, payload: dict[str, Any]) -> ClaimedJob:
    return ClaimedJob(
        id=uuid4(),
        kind=kind,
        payload=payload,
        learner_id=uuid4(),
        lock_key="lock",
        coalesce_key=None,
        priority=0,
        attempt=1,
        max_attempts=3,
    )


async def test_handlers_share_driver_redis_and_gateway(
    db: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = {"driver": 0, "redis": 0, "gateway": 0}
    seen: list[tuple[str, Any, Any]] = []

    def fake_driver(_settings: Settings) -> Any:
        counts["driver"] += 1
        return object()

    def fake_redis(_settings: Settings) -> Any:
        counts["redis"] += 1
        return object()

    def fake_gateway(*_a: Any, **_kw: Any) -> Any:
        counts["gateway"] += 1
        return object()

    async def fake_extract(job: ClaimedJob, ctx: JobContext, *, gateway: Any, redis: Any) -> int:
        seen.append(("extract", gateway, redis))
        return 1

    async def fake_checkpoint(job: ClaimedJob, ctx: JobContext, *, gateway: Any, redis: Any) -> int:
        seen.append(("checkpoint", gateway, redis))
        return 1

    monkeypatch.setattr(workers, "_RESOURCES", {})
    monkeypatch.setattr(workers, "create_driver", fake_driver)
    monkeypatch.setattr(workers, "create_redis", fake_redis)
    monkeypatch.setattr(workers, "HttpLLMProvider", lambda *a, **kw: object())
    monkeypatch.setattr(workers, "LLMGateway", fake_gateway)
    monkeypatch.setattr(workers, "run_turn_extract", fake_extract)
    monkeypatch.setattr(workers, "run_session_checkpoint", fake_checkpoint)

    ctx = JobContext(settings=settings, session_factory=db)
    extract_job = _job("turn.extract", {})
    await workers.handle_turn_extract(extract_job, ctx)
    await workers.handle_turn_extract(extract_job, ctx)
    await workers.handle_session_checkpoint(_job("session.checkpoint", {}), ctx)

    assert counts == {"driver": 1, "redis": 1, "gateway": 1}
    assert len(seen) == 3
    assert len({(id(g), id(r)) for _, g, r in seen}) == 1


async def test_topic_context_read_uses_teacher_seq_as_min_seq(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: Any,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
    settings: Settings,
    fake_llm: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-worker-slice",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])

    fake_llm.push(
        "teacher",
        TeacherOutput(reply_text="Go on.", pedagogical_move="explain"),
    )
    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "The derivative of x^2 is 2x", "idempotency_key": "turn-worker-slice"},
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200, turn_resp.text
    turn_id = UUID(dict(parse_sse(turn_resp.text))["turn_started"]["turn_id"])

    # The DB is shared between tests: skip turn.extract jobs left over by other tests.
    while True:
        async with db() as session, session.begin():
            job = await claim_next_job(session, "test-worker", ["turn.extract"])
        assert job is not None
        if UUID(job.payload["turn_id"]) == turn_id:
            break
    teacher_seq = int(job.payload["teacher_seq"])

    recorded: list[int | None] = []
    real_slice = workers.read_consistent_slice

    async def spy(learner_id: UUID, min_seq: int | None, *args: Any, **kwargs: Any) -> Any:
        recorded.append(min_seq)
        return await real_slice(learner_id, min_seq, *args, **kwargs)

    monkeypatch.setattr(workers, "read_consistent_slice", spy)

    fake_llm.push("extractor", ExtractorOutput(observations=[], misconceptions=[]))
    assert fake_llm.gateway is not None
    ctx = JobContext(settings=settings, session_factory=db)
    await workers.run_turn_extract(job, ctx, gateway=fake_llm.gateway, redis=redis, neo4j=neo4j)

    assert recorded == [teacher_seq]
    assert "Power rule" in fake_llm.calls_for("extractor")[0].prompt

    # Without Redis the graph is read directly, without waiting for the projector.
    recorded.clear()
    skills, _ = await workers._read_topic_context(
        neo4j,
        seeded_domain.learner_id,
        seeded_domain.topic_id,
        min_seq=teacher_seq,
        redis=None,
        ctx=ctx,
    )
    assert recorded == []
    assert any(skill["name"] == "Power rule" for skill in skills)
