"""WP23.5: routes of the hub cheat sheet (ТЗ Ф4 §6.4 «Артефакты»; Т-§17.6, Т-§17.7; D-C-144)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID, uuid4

import httpx
import pytest

from quack.api.app import create_app
from quack.api.ratelimit import enforce_action_rate_limit
from quack.artifacts.hub_cheatsheet_routes import Memory, get_memory
from quack.config import Settings
from quack.db import Db
from tests.artifacts.hubs import DAY_MS, create_hub, set_deadline
from tests.artifacts.test_cheatsheet_wp23_5 import HubReader, prefetch
from tests.db.fixtures import create_learner
from tests.support.auth import session_cookie, session_token
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources

type ClientFor = Callable[[UUID, UUID], Awaitable[httpx.AsyncClient]]


@pytest.fixture
def provider() -> ScriptedLLMProvider:
    return ScriptedLLMProvider()


@pytest.fixture
async def client_for(
    settings: Settings, pg_db: Db, provider: ScriptedLLMProvider
) -> AsyncIterator[ClientFor]:
    """The API over `pg_db`, signed in with a real session; the memory of the hub is fixed."""
    opened: list[httpx.AsyncClient] = []

    async def make(learner_id: UUID, hub_id: UUID) -> httpx.AsyncClient:
        app = create_app(settings)
        app.state.resources = build_app_resources(settings, None, None, db=pg_db, provider=provider)
        app.dependency_overrides[enforce_action_rate_limit] = lambda: None
        app.dependency_overrides[get_memory] = lambda: Memory(HubReader(hub_id), prefetch)
        cookie = session_cookie(await session_token(pg_db, learner_id, settings))
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=cookie
        )
        opened.append(client)
        return client

    yield make
    for client in opened:
        await client.aclose()


async def test_get_cheatsheet_with_the_offer(
    pg_db: Db, client_for: ClientFor, settings: Settings
) -> None:
    """`GET /v1/hubs/{id}/cheatsheet`: the document of the hub and the state of the offer
    (D-C-144: read, not a job); another learner's hub — 404."""
    learner, stranger = await create_learner(pg_db), await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    client = await client_for(learner, hub)
    sheet = (await client.get(f"/v1/hubs/{hub}/cheatsheet")).json()
    assert (sheet["kind"], sheet["hub_id"], sheet["version"], sheet["blocks"]) == (
        "cheatsheet", str(hub), 0, []
    )  # fmt: skip
    assert sheet["offer"] is False
    other = await client_for(stranger, hub)
    assert (await other.get(f"/v1/hubs/{hub}/cheatsheet")).status_code == 404


async def test_get_cheatsheet_offer_is_true_before_the_deadline(
    pg_db: Db, client_for: ClientFor
) -> None:
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    await set_deadline(pg_db, learner, hub, int(time.time() * 1000) + 2 * DAY_MS)
    client = await client_for(learner, hub)
    assert (await client.get(f"/v1/hubs/{hub}/cheatsheet")).json()["offer"] is True


async def test_post_cheatsheet_writes_the_consent(pg_db: Db, client_for: ClientFor) -> None:
    """`POST /v1/hubs/{id}/cheatsheet`: the consent is `agent.requested`; a repeat with the same
    key returns the same request and writes nothing."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    client = await client_for(learner, hub)
    body = {"idempotency_key": "agree-1"}
    first = await client.post(f"/v1/hubs/{hub}/cheatsheet", json=body)
    assert first.status_code == 202, first.text
    again = await client.post(f"/v1/hubs/{hub}/cheatsheet", json=body)
    assert again.json() == first.json()
    rows = await pg_db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'agent.requested'",
        {"learner_id": learner},
    )
    assert len(rows) == 1 and rows[0]["payload"]["request_id"] == first.json()["request_id"]


async def test_post_cheatsheet_of_a_foreign_hub_is_404(pg_db: Db, client_for: ClientFor) -> None:
    learner, stranger = await create_learner(pg_db), await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    client = await client_for(stranger, hub)
    response = await client.post(f"/v1/hubs/{hub}/cheatsheet", json={"idempotency_key": "k"})
    assert response.status_code == 404
    assert (
        await client.post(f"/v1/hubs/{uuid4()}/cheatsheet", json={"idempotency_key": "k"})
    ).status_code == 404


async def test_review_route_returns_the_remarks(
    pg_db: Db, client_for: ClientFor, provider: ScriptedLLMProvider
) -> None:
    """`POST /v1/hubs/{id}/cheatsheet/review`: the learner writes the sheet by `PUT` of blocks,
    the agent answers with remarks as supplements; the learner's block is untouched."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    client = await client_for(learner, hub)
    sheet = (await client.get(f"/v1/hubs/{hub}/cheatsheet")).json()
    block_id = str(uuid4())
    put = await client.put(
        f"/v1/artifacts/{sheet['artifact_id']}/blocks/{block_id}",
        json={"content": "Chain rule: f'g", "idempotency_key": "w1"},
    )
    assert put.status_code == 200, put.text
    remark = {
        "updates": 1, "kind": "my_traps", "content": "Add g'.", "sources": [], "skills": ["S1"],
    }  # fmt: skip
    provider.respond("ArtifactWriterOutput", lambda _r: {"blocks": [remark], "cards": []})
    body = {"idempotency_key": "review-1"}
    first = await client.post(f"/v1/hubs/{hub}/cheatsheet/review", json=body)
    assert first.status_code == 200, first.text
    data = first.json()
    (note,) = data["remarks"]
    assert note["supplementTo"] == block_id and note["content"] == "Add g'."
    mine = next(b for b in data["blocks"] if b["block_id"] == block_id)
    assert mine["owner"] == "learner" and mine["content"] == "Chain rule: f'g"
    again = await client.post(f"/v1/hubs/{hub}/cheatsheet/review", json=body)
    assert again.json() == data  # same key: the same remarks, no new version


async def test_review_route_of_a_foreign_hub_is_404(pg_db: Db, client_for: ClientFor) -> None:
    learner, stranger = await create_learner(pg_db), await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    client = await client_for(stranger, hub)
    response = await client.post(f"/v1/hubs/{hub}/cheatsheet/review", json={"idempotency_key": "k"})
    assert response.status_code == 404
