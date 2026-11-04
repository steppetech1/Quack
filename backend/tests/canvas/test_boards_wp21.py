"""WP21.1: the board of a canvas and its routes (ТЗ Ф4 WP21.1, §6.4; Т-§15.1, П-2.6, П-9.4).
`POST /v1/sessions/{id}/canvases`, `GET /v1/canvases/{id}`, `GET /v1/domains/{id}/canvases`
on Phase 4 sessions. Integration tests on the Phase 4 database (`pg_db`)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from quack.api.app import create_app
from quack.canvas.board import BoardDoc
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from tests.db.fixtures import create_learner
from tests.support.auth import login
from tests.support.resources import build_app_resources

type ClientFor = Callable[[UUID | None], Awaitable[httpx.AsyncClient]]


@pytest.fixture
async def client_for(pg_db: Db, settings: Settings) -> AsyncIterator[ClientFor]:
    async with AsyncExitStack() as stack:

        async def make(learner_id: UUID | None) -> httpx.AsyncClient:
            resources = build_app_resources(settings, None, None, db=pg_db)
            app = create_app(settings, resources=resources)
            client = await stack.enter_async_context(
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
            )
            if learner_id is not None:
                await login(client, pg_db, learner_id, settings)
            return client

        yield make


async def _event(db: Db, learner: UUID, type_: str, payload: dict[str, Any]) -> None:
    await append_events(
        db,
        learner,
        [NewEvent(type=type_, payload=payload, actor="learner", idempotency_key=str(uuid4()))],
    )


async def _domain(db: Db, learner: UUID) -> UUID:
    domain_id = uuid4()
    await _event(db, learner, "domain.created", {"domain_id": str(domain_id), "name": "Calculus"})
    return domain_id


async def _session(db: Db, learner: UUID, domain: UUID) -> UUID:
    session_id = uuid4()
    await _event(
        db,
        learner,
        "session.started",
        {"session_id": str(session_id), "domain_id": str(domain), "intent": "learn"},
    )
    return session_id


async def _task(db: Db, learner: UUID, *, source: str, solution_space: bool) -> UUID:
    task_id = uuid4()
    await db.execute(
        "INSERT INTO content.tasks (task_id, learner_id, source, condition, answer_spec, "
        "solution_space, created_seq) VALUES (%(task_id)s, %(learner_id)s, %(source)s, "
        "%(condition)s::jsonb, '{}'::jsonb, %(space)s::jsonb, 1)",
        {
            "task_id": task_id,
            "learner_id": learner,
            "source": source,
            "condition": json.dumps({"text": "Find the limit", "latex": None}),
            "space": json.dumps({"milestones": []}) if solution_space else None,
        },
    )
    return task_id


async def _agent_requests(db: Db, learner: UUID) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'agent.requested'",
        {"learner_id": learner},
    )
    return [r["payload"] for r in rows]


def test_board_layers_are_separate_structures() -> None:
    """П-9.4, Т-§15.1: the learner layer and the teacher layer are different structures of the
    document; a teacher item cannot be put into the learner layer and back."""
    doc = BoardDoc.empty(task_id=None)
    assert doc.learner.model_dump() == {"typed_formulas": [], "drawings": []}
    assert set(doc.teacher.model_dump()) == {"marks", "hints", "visualizations", "scaffolds"}
    mark = {"mark_id": "m1", "line_id": "l1", "kind": "question", "text": "Check the sign?"}
    with pytest.raises(ValidationError):
        BoardDoc.model_validate(
            {**doc.model_dump(mode="json"), "learner": {"typed_formulas": [], "marks": [mark]}}
        )


async def test_open_canvas_by_task_and_read_it(pg_db: Db, client_for: ClientFor) -> None:
    """WP21.1: the canvas belongs to the session; by task — a task block on the board; an
    on-the-fly task without a solution space writes `agent.requested {kind: solution_space}`;
    the canvas is in the list of its domain."""
    learner = await create_learner(pg_db)
    domain = await _domain(pg_db, learner)
    session_id = await _session(pg_db, learner, domain)
    task_id = await _task(pg_db, learner, source="teacher", solution_space=False)
    client = await client_for(learner)

    opened = await client.post(
        f"/v1/sessions/{session_id}/canvases", json={"task_id": str(task_id)}
    )
    assert opened.status_code == 201, opened.text
    canvas = opened.json()
    canvas_id = canvas["canvas_id"]
    assert canvas["session_id"] == str(session_id) and canvas["task_id"] == str(task_id)
    [block] = canvas["doc"]["blocks"]
    assert block["kind"] == "task" and block["task_id"] == str(task_id)
    [request] = await _agent_requests(pg_db, learner)
    assert request["kind"] == "solution_space" and request["params"]["task_id"] == str(task_id)

    read = await client.get(f"/v1/canvases/{canvas_id}")
    assert read.status_code == 200 and read.json() == canvas
    listed = await client.get(f"/v1/domains/{domain}/canvases")
    assert [c["canvas_id"] for c in listed.json()["canvases"]] == [canvas_id]


async def test_one_request_per_task(pg_db: Db, client_for: ClientFor) -> None:
    """Law 5 (`Т-§1.1`, key from the task id), WP12.4: a second canvas on the same on-the-fly
    task — another session or a reopening — writes no second `agent.requested`. The request is
    written by code (`actor = code`) with the domain and the session of its canvas (WP10.4)."""
    learner = await create_learner(pg_db)
    domain = await _domain(pg_db, learner)
    first, second = await _session(pg_db, learner, domain), await _session(pg_db, learner, domain)
    task_id = await _task(pg_db, learner, source="teacher", solution_space=False)
    client = await client_for(learner)
    for session_id in (first, second, first):
        opened = await client.post(
            f"/v1/sessions/{session_id}/canvases", json={"task_id": str(task_id)}
        )
        assert opened.status_code == 201, opened.text
    rows = await pg_db.fetch_all(
        "SELECT actor, domain_id, session_id, idempotency_key FROM core.events "
        "WHERE learner_id = %(learner_id)s AND type = 'agent.requested'",
        {"learner_id": learner},
    )
    assert rows == [
        {
            "actor": "code",
            "domain_id": domain,
            "session_id": first,
            "idempotency_key": f"solution_space:{task_id}",
        }
    ]


async def test_no_request_when_the_space_exists_or_task_is_from_the_bank(
    pg_db: Db, client_for: ClientFor
) -> None:
    learner = await create_learner(pg_db)
    session_id = await _session(pg_db, learner, uuid4())
    client = await client_for(learner)
    with_space = await _task(pg_db, learner, source="teacher", solution_space=True)
    from_bank = await _task(pg_db, learner, source="seminar", solution_space=False)
    for task_id in (with_space, from_bank, None):
        body = {} if task_id is None else {"task_id": str(task_id)}
        response = await client.post(f"/v1/sessions/{session_id}/canvases", json=body)
        assert response.status_code == 201, response.text
    assert await _agent_requests(pg_db, learner) == []
    empty = (await client.post(f"/v1/sessions/{session_id}/canvases", json={})).json()
    assert empty["task_id"] is None and empty["doc"]["blocks"] == []


async def test_isolation_and_no_session(pg_db: Db, client_for: ClientFor) -> None:
    """WP18.3, T-E3: a session, task, canvas or domain of another learner — 404; without a
    session — 401 `UNAUTHENTICATED`."""
    owner, other = await create_learner(pg_db), await create_learner(pg_db)
    domain = await _domain(pg_db, owner)
    session_id = await _session(pg_db, owner, domain)
    task_id = await _task(pg_db, owner, source="teacher", solution_space=False)
    canvas_id = (
        await (await client_for(owner)).post(f"/v1/sessions/{session_id}/canvases", json={})
    ).json()["canvas_id"]
    own_session = await _session(pg_db, other, uuid4())

    stranger = await client_for(other)
    for response in (
        await stranger.post(f"/v1/sessions/{session_id}/canvases", json={}),
        await stranger.post(f"/v1/sessions/{own_session}/canvases", json={"task_id": str(task_id)}),
        await stranger.get(f"/v1/canvases/{canvas_id}"),
        await stranger.get(f"/v1/domains/{domain}/canvases"),
    ):
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "NOT_FOUND"

    anonymous = await client_for(None)
    for response in (
        await anonymous.post(f"/v1/sessions/{session_id}/canvases", json={}),
        await anonymous.get(f"/v1/canvases/{canvas_id}"),
        await anonymous.get(f"/v1/domains/{domain}/canvases"),
    ):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"
