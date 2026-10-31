"""WP4.2: a cheatsheet route of another learner's topic is indistinguishable from an unknown
topic — `404 NOT_FOUND` (ТЗ Фазы 3 WP4.2, `Т-§19.1`; STREAM3_NOTES риск 9, T-E3)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
from sqlalchemy import text

from quack.db.postgres import SessionFactory

if TYPE_CHECKING:
    from tests.artifacts.conftest import AuthHeaders, Seeder


async def _jobs(db: SessionFactory) -> int:
    async with db() as session:
        return int((await session.execute(text("SELECT count(*) FROM ops.jobs"))).scalar_one())


async def test_cheatsheet_of_foreign_or_unknown_topic_is_not_found(
    client: httpx.AsyncClient, auth: AuthHeaders, seed: Seeder, db: SessionFactory
) -> None:
    owner = await seed.learner()
    other = await seed.learner()
    s = await seed.seed_structure(owner)

    # The owner's topic without a cheatsheet yet: 200 and null (ТЗ-П3 §3).
    own = await client.get(f"/v1/topics/{s.topic_id}/cheatsheet", headers=auth(owner))
    assert own.status_code == 200, own.text
    assert own.json() is None

    jobs_before = await _jobs(db)
    for topic_id in (s.topic_id, uuid4()):
        got = await client.get(f"/v1/topics/{topic_id}/cheatsheet", headers=auth(other))
        assert got.status_code == 404, got.text
        assert got.json()["error"]["code"] == "NOT_FOUND"
        refresh = await client.post(
            f"/v1/topics/{topic_id}/cheatsheet/refresh",
            json={"domain_id": str(s.domain_id)},
            headers=auth(other),
        )
        assert refresh.status_code == 404, refresh.text
        assert refresh.json()["error"]["code"] == "NOT_FOUND"
    assert await _jobs(db) == jobs_before  # nothing is enqueued for a foreign topic
