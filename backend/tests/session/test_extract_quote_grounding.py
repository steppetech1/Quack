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
from quack.jobs.queue import claim_next_job
from quack.session.schemas import ExtractorEntry, ExtractorOutputV2, TeacherOutput
from quack.session.workers import run_turn_extract
from tests.session.helpers import parse_sse, skill_label
from tests.support.resources import build_job_context

pytest_plugins = ["tests.session.fixtures"]


async def test_ungrounded_quotes_are_not_applied(
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

    # §8.9 п.9 (WP12, Т-§7.6): with extractor@2 a quote that is not in the learner's events is
    # written with its rejection reason and not applied; the grounded element is applied.
    student_seq = int(job.payload["student_seq"])
    teacher_seq = int(job.payload["teacher_seq"])
    power = skill_label(seeded_domain.skill_ids, seeded_domain.skill_ids[0])

    def entry(quote: str, seq: int) -> ExtractorEntry:
        return ExtractorEntry(
            kind="skill_observation",
            skill=power,
            misconception=None,
            level=3,
            outcome="failure",
            help="none",
            gap_type="execution_error",
            quote=quote,
            events=[seq],
        )

    fake_llm.push(
        "extractor",
        ExtractorOutputV2(
            entries=[
                entry("derivative is 2x", student_seq),
                entry("the integral is x^2", student_seq),
                entry("Check it again.", teacher_seq),
            ]
        ),
    )
    assert fake_llm.gateway is not None
    ctx = build_job_context(settings, db, neo4j, redis, llm=fake_llm.gateway)
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
    verdicts = [(e["quote"], e["rejected_reason"]) for e in payload["elements"]]
    assert verdicts == [
        ("derivative is 2x", None),
        ("the integral is x^2", "quote_not_in_source"),
        ("Check it again.", "source_outside_episode"),
    ]
