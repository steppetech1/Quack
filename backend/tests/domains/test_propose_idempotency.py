"""`structure/propose` idempotency (ТЗ Фазы 3 T-E2, Т-§2.1, Т-§2.3; PHASE3_NOTES D-C-025).

The payload of `structure.proposed` is the structurer's answer, not the request, so the key of
the event is derived from its source: the client key and the hash of the request input. The
same body repeats the recorded result; the same client key with another body is 409 before
any model call.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text

from quack.db.postgres import SessionFactory
from quack.domains.schemas import StructurerOutput, StructurerSkill, StructurerTopic
from tests.domains.conftest import FakeLLMProvider
from tests.domains.test_structure_actor_and_delete import _seed_domain


def _body(goals: str, key: str) -> dict[str, Any]:
    return {"goals": goals, "background": "b", "focus_areas": ["Limits"], "idempotency_key": key}


async def _event_count(db: SessionFactory, learner_id: UUID) -> int:
    async with db() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM core.events WHERE learner_id = :l"), {"l": learner_id}
        )
    return int(count or 0)


async def test_propose_same_key_other_body_is_conflict(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    headers = {"Authorization": f"Bearer {token}"}
    domain_id = await _seed_domain(db, learner_id)
    url = f"/v1/domains/{domain_id}/structure/propose"
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

    first = await api_client.post(url, json=_body("Pass the exam", "p:1"), headers=headers)
    assert first.status_code == 200, first.text
    events = await _event_count(db, learner_id)

    again = await api_client.post(url, json=_body("Pass the exam", "p:1"), headers=headers)
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert await _event_count(db, learner_id) == events

    other = await api_client.post(url, json=_body("Understand it", "p:1"), headers=headers)
    assert other.status_code == 409, other.text
    assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert await _event_count(db, learner_id) == events
    assert len(fake_llm_provider.calls) == 1  # the conflict is found before any model call
