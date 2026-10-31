"""`structure.proposed` actor (`code` on degraded fallback, `structurer@1` otherwise) and
`DELETE /v1/learner` cancelling queued and running jobs (РТЗ D-7.4, D-7.5).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text

from quack.db.postgres import SessionFactory
from quack.domains.schemas import StructurerOutput, StructurerSkill, StructurerTopic
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.jobs import kinds
from quack.jobs.queue import enqueue_job
from quack.llm.errors import ProviderUnavailableError
from tests.domains.conftest import FakeLLMProvider


async def _q(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def _seed_domain(db: SessionFactory, learner_id: UUID) -> UUID:
    domain_id = uuid4()
    async with db() as session, session.begin():
        await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="domain.created",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key=f"seed:domain:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "name": "Calculus",
                        "slug": "calculus",
                        "has_materials": False,
                    },
                )
            ],
            publish_wakeup=False,
        )
    return domain_id


async def _propose_actor(
    api_client: httpx.AsyncClient,
    db: SessionFactory,
    learner_id: UUID,
    token: str,
    key: str,
) -> str:
    domain_id = await _seed_domain(db, learner_id)
    resp = await api_client.post(
        f"/v1/domains/{domain_id}/structure/propose",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "goals": "g",
            "background": "b",
            "focus_areas": ["Limits"],
            "idempotency_key": key,
        },
    )
    assert resp.status_code == 200
    [row] = await _q(
        db,
        "SELECT actor FROM core.events WHERE learner_id = :l AND domain_id = :d "
        "AND type = 'structure.proposed'",
        l=learner_id,
        d=domain_id,
    )
    return str(row["actor"])


async def test_structure_actor_code_when_degraded(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    fake_llm_provider.enqueue(ProviderUnavailableError("503"))
    assert await _propose_actor(api_client, db, learner_id, token, "act:degraded") == "code"


async def test_structure_actor_structurer_when_llm_works(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    fake_llm_provider.enqueue(
        StructurerOutput(
            topics=[StructurerTopic(slug="limits", name="Limits", description="d")],
            skills=[
                StructurerSkill(slug="laws", topic_slug="limits", name="Laws", description="d")
            ],
            prereqs=[],
            skill_deps=[],
            misconceptions=[],
        )
    )
    assert await _propose_actor(api_client, db, learner_id, token, "act:ok") == "structurer@1"


async def test_delete_learner_cancels_queued_and_running_jobs(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    other_id = uuid4()
    ids: dict[str, UUID] = {}
    async with db() as session, session.begin():
        for name, lid in (("queued", learner_id), ("running", learner_id), ("other", other_id)):
            ids[name] = await enqueue_job(
                session, kinds.graph_curate(lid, uuid4(), {"domain_id": str(uuid4())})
            )
        await session.execute(
            text("UPDATE ops.jobs SET status = 'running' WHERE id = :id"), {"id": ids["running"]}
        )
        await session.execute(
            text("UPDATE ops.jobs SET status = 'running' WHERE id = :id"), {"id": ids["other"]}
        )

    resp = await api_client.delete("/v1/learner", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200

    async def status(name: str) -> str:
        [row] = await _q(db, "SELECT status FROM ops.jobs WHERE id = :id", id=ids[name])
        return str(row["status"])

    assert await status("queued") == "cancelled"
    assert await status("running") == "cancelled"
    assert await status("other") == "running"
