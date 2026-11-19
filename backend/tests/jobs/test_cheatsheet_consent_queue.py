"""ТЗ Ф4 WP7.1, WP23.5 (D-A-139, D-A-199) through the dispatcher and the queue: the consent to a
cheat sheet is not swallowed by the waiting job of `hub.deadline_set`. Needs Postgres only."""

from __future__ import annotations

from uuid import uuid4

from quack.db import Db
from tests.db.fixtures import create_learner
from tests.jobs.test_dispatcher_p4 import Log, dispatcher, drain


async def test_consent_after_a_deadline_keeps_its_request(jobs_db: Db) -> None:
    """`hub.deadline_set` puts a coalescing `artifact.cheatsheet` job on the hub; the consent
    (`agent.requested`) that comes before it runs used to be glued into that waiting job, whose
    payload has no `request_id`: the cheat sheet was not built and `agent.completed` never came."""
    learner = await create_learner(jobs_db)
    log = Log(jobs_db, learner)
    hub, request = uuid4(), uuid4()
    await log.put("hub.deadline_set", {"hub_id": str(hub)}, session=False)
    consent = {
        "request_id": str(request),
        "kind": "artifact",
        "params": {"artifact": "cheatsheet", "hub_id": str(hub)},
        "needed_in_session": False,
    }
    await log.put("agent.requested", consent, session=False)
    await drain(dispatcher(jobs_db), learner)
    rows = await jobs_db.fetch_all(
        "SELECT payload, coalesce_key FROM ops.jobs WHERE kind = 'artifact.cheatsheet'"
    )
    assert len(rows) == 2
    answered = [r for r in rows if r["payload"].get("request_id") == str(request)]
    assert len(answered) == 1 and answered[0]["payload"]["hub_id"] == str(hub)
    waiting = [r for r in rows if "request_id" not in r["payload"]]
    assert [r["coalesce_key"] for r in waiting] == [f"artifact:{hub}"]
