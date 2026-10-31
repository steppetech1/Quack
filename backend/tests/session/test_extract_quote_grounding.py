"""Quote grounding of `turn.extract` (law 4, Т-§7.5-7.6)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.events.payloads import ExtractedMisconception, ExtractedObservation, SkillRef
from quack.jobs.queue import claim_next_job
from quack.jobs.runner import JobContext
from quack.session.schemas import ExtractorOutput, TeacherOutput
from quack.session.workers import run_turn_extract
from tests.session.helpers import parse_sse, skill_label

pytest_plugins = ["tests.session.fixtures"]


async def test_ungrounded_quotes_are_dropped(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: Any,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
    settings: Settings,
    fake_llm: Any,
) -> None:
    start_resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": "start-grounding",
        },
        headers=auth_headers,
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = UUID(start_resp.json()["session_id"])

    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Check it again.",
            pedagogical_move="explain",
            focus_skill_labels=[skill_label(seeded_domain.skill_ids, seeded_domain.skill_ids[0])],
        ),
    )
    turn_resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "I think the derivative is 2x", "idempotency_key": "turn-grounding"},
        headers=auth_headers,
    )
    assert turn_resp.status_code == 200, turn_resp.text
    turn_id = UUID(dict(parse_sse(turn_resp.text))["turn_started"]["turn_id"])

    # The DB is shared between tests: skip turn.extract jobs left over by other tests.
    job = None
    while True:
        async with db() as session, session.begin():
            claimed = await claim_next_job(session, "test-worker", ["turn.extract"])
        assert claimed is not None
        if UUID(claimed.payload["turn_id"]) == turn_id:
            job = claimed
            break

    skill = SkillRef(skill_id=seeded_domain.skill_ids[0])

    def obs(quote: str) -> ExtractedObservation:
        return ExtractedObservation(
            skill=skill,
            direction="negative",
            strength=0.8,
            independence=1.0,
            tested_depth=0.5,
            polarity_weight=1.0,
            evidence_text=quote,
        )

    def misc(quote: str) -> ExtractedMisconception:
        return ExtractedMisconception(
            name="Wrong derivative",
            skill=skill,
            topic_id=seeded_domain.topic_id,
            confidence=0.9,
            evidence_text=quote,
        )

    fake_llm.push(
        "extractor",
        ExtractorOutput(
            observations=[obs("derivative is 2x"), obs("the integral is x^2")],
            misconceptions=[misc("the integral is x^2")],
        ),
    )
    assert fake_llm.gateway is not None
    ctx = JobContext(settings=settings, session_factory=db)
    await run_turn_extract(job, ctx, gateway=fake_llm.gateway, redis=redis, neo4j=neo4j)

    async with db() as session:
        payload = (
            await session.execute(
                text(
                    "SELECT payload FROM core.events "
                    "WHERE learner_id = :l AND type = 'turn.extracted' AND turn_id = :t"
                ),
                {"l": seeded_domain.learner_id, "t": turn_id},
            )
        ).scalar_one()
    assert [o["evidence_text"] for o in payload["observations"]] == ["derivative is 2x"]
    assert payload["misconceptions"] == []
