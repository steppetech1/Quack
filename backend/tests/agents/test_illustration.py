"""The illustrator, WP16.6 (ТЗ Ф4; D-B-196): the picture goes to the learner's prefix and
`agent.completed` points at it; a repeat adds no second event and no second call; a request
without a description, or an answer that is only a `url`, is `ok: false`."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

from quack.agents.illustration import NO_DESCRIPTION, URL_ONLY, illustrate_request
from quack.db import Db
from quack.events.event_types import AgentRequested
from quack.events.log import NewEvent, append_events
from quack.events.records import AgentParamsRecord
from quack.jobs.runner import JobContext
from quack.llm.gateway import LLMResult
from quack.llm.media import Illustration
from tests.db.fixtures import create_learner
from tests.support.storage import InMemoryObjectStore

PNG = b"\x89PNG-bytes"


class _Gateway:
    def __init__(self, image: Illustration) -> None:
        self.image = image
        self.prompts: list[str] = []

    async def illustrate(self, prompt: str, **kwargs: Any) -> LLMResult[Illustration]:
        self.prompts.append(prompt)
        return LLMResult(self.image, False, "h", "muse-image-1.0", None)


async def _request(db: Db, learner_id: UUID, description: str | None) -> UUID:
    request_id = uuid4()
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="agent.requested",
                payload=AgentRequested(
                    request_id=request_id,
                    kind="illustration",
                    params=AgentParamsRecord(description=description),
                    needed_in_session=False,
                ),
                actor="code",
                idempotency_key=f"agent.requested:{request_id}",
            )
        ],
    )
    return request_id


async def _completed(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(l)s "
        "AND type = 'agent.completed' ORDER BY seq",
        {"l": learner_id},
    )
    return [r["payload"] for r in rows]


def _ctx(db: Db, gateway: _Gateway, store: InMemoryObjectStore) -> JobContext:
    return cast(JobContext, SimpleNamespace(db=db, llm=gateway, storage=store))


async def test_illustration_stored_once_and_failures_are_not_ok(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    store = InMemoryObjectStore()
    gateway = _Gateway(Illustration(image=PNG, url=None, mime="image/png"))
    request_id = await _request(pg_db, learner_id, "a staircase of ideas")
    for _ in range(2):
        await illustrate_request(
            _ctx(pg_db, gateway, store), learner_id=learner_id, request_id=request_id
        )
    (done,) = await _completed(pg_db, learner_id)
    assert done["ok"] is True and done["kind"] == "illustration"
    assert done["request_id"] == str(request_id)
    assert done["result_ref"].startswith(f"learners/{learner_id}/illustrations/")
    assert done["result_ref"].endswith(".png")
    assert await store.get(done["result_ref"]) == PNG
    assert len(gateway.prompts) == 1 and "a staircase of ideas" in gateway.prompts[0]

    bare = await _request(pg_db, learner_id, None)
    await illustrate_request(_ctx(pg_db, gateway, store), learner_id=learner_id, request_id=bare)
    link = await _request(pg_db, learner_id, "a bridge")
    linked = _Gateway(Illustration(image=None, url="https://img.example/x.png", mime="image/png"))
    await illustrate_request(_ctx(pg_db, linked, store), learner_id=learner_id, request_id=link)
    no_desc, url_only = (await _completed(pg_db, learner_id))[1:]
    assert (no_desc["ok"], no_desc["error"], no_desc["result_ref"]) == (False, NO_DESCRIPTION, None)
    assert (url_only["ok"], url_only["error"]) == (False, URL_ONLY)
    assert len(gateway.prompts) == 1 and len(store.objects) == 1
