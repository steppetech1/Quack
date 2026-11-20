"""`retry_turn` (Т-§12.1, law 1): the turn runs again on the latest learner input that has no
`turn.committed`. It writes no event of its own, is idempotent (an answered input is not asked
again; one `turn.committed` per action even if two retries race) and works on the journal alone.

A lost turn is made here the way it is lost in production: the write of `turn.committed`
fails after the model answered (a connection dropped in the middle of the write)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.api.app import create_app
from quack.config import Settings
from quack.db.db import Db
from quack.session import action_turn
from quack.session.retry_turn import RetryTarget
from quack.session.router import get_turn_context_source
from tests.session.helpers import parse_sse
from tests.session.test_actions_route import (
    FixedSource,
    Learner,
    _learner,  # pyright: ignore[reportPrivateUsage]
    _names,  # pyright: ignore[reportPrivateUsage]
    _post,  # pyright: ignore[reportPrivateUsage]
    _types,  # pyright: ignore[reportPrivateUsage]
)

pytest_plugins = ["tests.session.fixtures"]


@pytest.fixture
async def app(settings: Settings, neo4j: AsyncDriver, redis: Redis, pg_db: Db) -> FastAPI:
    from tests.support.resources import build_app_resources

    return create_app(settings, resources=build_app_resources(settings, neo4j, redis, db=pg_db))


@pytest.fixture
def source(app: FastAPI, settings: Settings) -> FixedSource:
    fixed = FixedSource(settings)
    app.dependency_overrides[get_turn_context_source] = lambda: fixed
    return fixed


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _turns(pg_db: Db, who: Learner) -> list[dict[str, Any]]:
    return await pg_db.fetch_all(
        "SELECT seq, payload ->> 'action_seq' AS action_seq FROM core.events "
        "WHERE learner_id = %(l)s AND type = 'turn.committed' ORDER BY seq",
        {"l": who.learner_id},
    )


async def _message_seqs(pg_db: Db, who: Learner) -> list[int]:
    rows = await pg_db.fetch_all(
        "SELECT seq FROM core.events WHERE learner_id = %(l)s AND type = 'message.sent' "
        "ORDER BY seq",
        {"l": who.learner_id},
    )
    return [r["seq"] for r in rows]


@pytest.fixture
def commit_fails(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """While `broken["on"]`, the write of the turn fails as a dropped connection would."""
    broken = {"on": True}
    real = action_turn.commit_turn

    async def commit(*args: Any, **kwargs: Any) -> int:
        if broken["on"]:
            raise ConnectionError("the connection closed in the middle of the write")
        return await real(*args, **kwargs)

    monkeypatch.setattr(action_turn, "commit_turn", commit)
    return broken


async def test_retry_after_a_lost_turn_answers_the_input_once(
    client: httpx.AsyncClient,
    source: FixedSource,
    pg_db: Db,
    settings: Settings,
    commit_fails: dict[str, bool],
) -> None:
    who = await _learner(pg_db, settings)
    lost = await _post(client, who, {"kind": "message", "text": "explain it all again"})
    assert _names(parse_sse(lost.text))[-1] == "error"
    [message_seq] = await _message_seqs(pg_db, who)
    assert await _turns(pg_db, who) == []  # the input has no answer

    commit_fails["on"] = False
    retried = await _post(client, who, {"kind": "retry_turn"})

    assert retried.status_code == 200, retried.text
    frames = parse_sse(retried.text)
    assert _names(frames) == ["action_recorded", "status", "token", "turn_committed"]
    assert frames[0][1]["seq"] == message_seq  # the answered input, not a new event
    assert await _message_seqs(pg_db, who) == [message_seq]  # a retry writes no input
    [turn] = await _turns(pg_db, who)
    assert turn["action_seq"] == str(message_seq) and turn["seq"] == frames[-1][1]["seq"]
    assert source.asked[-1].action_seq == message_seq


async def test_a_repeated_retry_does_not_duplicate_the_turn(
    client: httpx.AsyncClient,
    source: FixedSource,
    pg_db: Db,
    settings: Settings,
    commit_fails: dict[str, bool],
) -> None:
    who = await _learner(pg_db, settings)
    await _post(client, who, {"kind": "message", "text": "why?"})
    commit_fails["on"] = False
    key = str(uuid4())
    first = await client.post(
        f"/v1/sessions/{who.session_id}/actions",
        json={"kind": "retry_turn", "idempotency_key": key},
        cookies=who.cookies,
    )
    assert first.status_code == 200
    asked = len(source.asked)

    again = await client.post(  # the same request, as a double click or a retried fetch
        f"/v1/sessions/{who.session_id}/actions",
        json={"kind": "retry_turn", "idempotency_key": key},
        cookies=who.cookies,
    )
    other_key = await _post(client, who, {"kind": "retry_turn"})

    [message_seq] = await _message_seqs(pg_db, who)
    for response in (again, other_key):
        assert response.status_code == 201, response.text
        assert response.json() == {"seq": message_seq}  # answered already: nothing to run
    assert len(await _turns(pg_db, who)) == 1
    assert len(source.asked) == asked  # no second model call


async def test_a_retry_that_races_the_turn_finds_its_commit(
    client: httpx.AsyncClient,
    source: FixedSource,
    pg_db: Db,
    settings: Settings,
    commit_fails: dict[str, bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two retries pass the check before either committed: the second write is refused by
    `commit_turn` (one `turn.committed` per action) and answers with the first turn's seq."""
    who = await _learner(pg_db, settings)
    await _post(client, who, {"kind": "message", "text": "why?"})
    commit_fails["on"] = False
    [message_seq] = await _message_seqs(pg_db, who)
    first = await _post(client, who, {"kind": "retry_turn"})
    [committed] = await _turns(pg_db, who)

    async def stale(*_args: Any, **_kwargs: Any) -> RetryTarget:
        return RetryTarget(message_seq, answered=False)  # the check ran before the first commit

    monkeypatch.setattr("quack.session.router.retry_target", stale)
    second = await _post(client, who, {"kind": "retry_turn"})

    assert first.status_code == second.status_code == 200
    assert await _turns(pg_db, who) == [committed]  # still one
    name, data = parse_sse(second.text)[-1]
    assert name == "turn_committed" and data["seq"] == committed["seq"]


async def test_retry_takes_the_latest_input_without_an_answer(
    client: httpx.AsyncClient,
    source: FixedSource,
    pg_db: Db,
    settings: Settings,
    commit_fails: dict[str, bool],
) -> None:
    who = await _learner(pg_db, settings)
    await _post(client, who, {"kind": "message", "text": "first"})
    await _post(client, who, {"kind": "message", "text": "second"})
    first, second = await _message_seqs(pg_db, who)

    commit_fails["on"] = False
    await _post(client, who, {"kind": "retry_turn"})
    assert [t["action_seq"] for t in await _turns(pg_db, who)] == [str(second)]

    await _post(client, who, {"kind": "retry_turn"})  # the older orphan is next
    assert [t["action_seq"] for t in await _turns(pg_db, who)] == [str(second), str(first)]
    assert (await _post(client, who, {"kind": "retry_turn"})).status_code == 201


async def test_retry_without_any_input_is_not_found(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    who = await _learner(pg_db, settings)

    response = await _post(client, who, {"kind": "retry_turn"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert source.asked == [] and "turn.committed" not in await _types(pg_db, who.learner_id)


async def test_retry_of_another_learners_session_is_not_found(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    who, other = await _learner(pg_db, settings), await _learner(pg_db, settings)
    await _post(client, other, {"kind": "message", "text": "mine"})

    response = await _post(client, who, {"kind": "retry_turn"}, other.session_id)

    assert response.status_code == 404
    assert len(source.asked) == 1  # only `other`'s own turn
