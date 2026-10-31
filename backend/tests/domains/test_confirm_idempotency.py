"""`structure/confirm` idempotency (ТЗ Фазы 3 T-E2, Т-§2.1).

The same client key with another body is 409 `IDEMPOTENCY_CONFLICT`, also when the other body
would fail validation: the key is checked before the request is.
"""

from __future__ import annotations

from uuid import UUID

import httpx
from sqlalchemy import text

from quack.db.postgres import SessionFactory
from quack.domains.schemas import StructurerOutput, StructurerSkill, StructurerTopic
from tests.domains.conftest import FakeLLMProvider
from tests.domains.test_structure_actor_and_delete import _seed_domain


async def _event_count(db: SessionFactory, learner_id: UUID) -> int:
    async with db() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM core.events WHERE learner_id = :l"), {"l": learner_id}
        )
    return int(count or 0)


async def test_confirm_same_key_other_version_is_conflict(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    headers = {"Authorization": f"Bearer {token}"}
    domain_id = await _seed_domain(db, learner_id)
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
    proposed = await api_client.post(
        f"/v1/domains/{domain_id}/structure/propose",
        json={"goals": "g", "background": "b", "focus_areas": ["Limits"], "idempotency_key": "p"},
        headers=headers,
    )
    assert proposed.status_code == 200, proposed.text
    version = proposed.json()["structure_version"]
    url = f"/v1/domains/{domain_id}/structure/confirm"

    first = await api_client.post(
        url, json={"structure_version": version, "idempotency_key": "c:1"}, headers=headers
    )
    assert first.status_code == 200, first.text
    events = await _event_count(db, learner_id)

    again = await api_client.post(
        url, json={"structure_version": version, "idempotency_key": "c:1"}, headers=headers
    )
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert await _event_count(db, learner_id) == events

    other = await api_client.post(
        url, json={"structure_version": version + 1, "idempotency_key": "c:1"}, headers=headers
    )
    assert other.status_code == 409, other.text
    assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert await _event_count(db, learner_id) == events
