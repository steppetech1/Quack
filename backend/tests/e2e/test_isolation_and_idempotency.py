"""E2E: idempotency, isolation, concurrency (ТЗ Фазы 3 §8.7: T-E2, T-E3, T-E7)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text

from tests.e2e.endpoints import (
    NO_IDEMPOTENCY_KEY,
    Ids,
    body_for,
    mutating_routes,
    path_for,
)
from tests.e2e.journey import first, ok, onboard, run_journey, turn
from tests.e2e.system import Learner, System
from tests.session.helpers import parse_sse

# Order of the T-E2 walk: each step may use ids created by the previous ones.
IDEMPOTENT_WALK: tuple[tuple[str, str], ...] = (
    ("PATCH", "/v1/learner/preferences"),
    ("POST", "/v1/domains"),
    ("POST", "/v1/domains/{domain_id}/structure/propose"),
    ("POST", "/v1/domains/{domain_id}/structure/confirm"),
    ("PATCH", "/v1/domains/{domain_id}/structure"),
    ("PATCH", "/v1/domains/{domain_id}/topics/{topic_id}/status"),
    ("PATCH", "/v1/topics/{topic_id}/status"),
    ("POST", "/v1/domains/{domain_id}/diagnostic/start"),
    ("POST", "/v1/domains/{domain_id}/diagnostic/answer"),
    ("POST", "/v1/domains/{domain_id}/diagnostic/finish"),
    ("POST", "/v1/sessions"),
    ("POST", "/v1/sessions/{session_id}/turns"),
    ("POST", "/v1/sessions/{session_id}/tasks/next"),
    ("POST", "/v1/sessions/{session_id}/tasks/{task_id}/hint"),
    ("POST", "/v1/sessions/{session_id}/tasks/{task_id}/submit"),
    ("POST", "/v1/artifacts/notes"),
    ("PATCH", "/v1/artifacts/notes/{artifact_id}"),
    ("DELETE", "/v1/artifacts/notes/{artifact_id}"),
    ("POST", "/v1/sessions/{session_id}/finish"),
    ("POST", "/v1/domains/{domain_id}/archive"),
)
# A body of these has no field besides the key, so "same key, other body" does not exist.
NO_OTHER_BODY = {
    ("POST", "/v1/domains/{domain_id}/diagnostic/start"),
    ("DELETE", "/v1/artifacts/notes/{artifact_id}"),
    ("POST", "/v1/sessions/{session_id}/finish"),
}


async def _events(system: System, learner_id: UUID) -> int:
    async with system.session_factory() as session:
        return int(
            await session.scalar(
                text("SELECT count(*) FROM core.events WHERE learner_id = :l"), {"l": learner_id}
            )
            or 0
        )


async def _send(
    system: System, learner: Learner, method: str, url: str, body: dict[str, Any]
) -> httpx.Response:
    return await system.client.request(method, url, json=body, headers=learner.headers)


def _payload(response: httpx.Response) -> Any:
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        return [(e, d) for e, d in parse_sse(response.content)]
    return response.json()


def _placeholder_ids(domain_id: UUID, topic_id: UUID) -> Ids:
    zero = UUID(int=0)
    return Ids(domain_id, topic_id, zero, zero, zero, zero, zero, zero, 1)


async def test_mutating_endpoints_are_idempotent(system: System) -> None:
    """T-E2: repeat with the same key and body → same answer, no new events; same key with
    another body → 409. Endpoints without a key are listed with a reason."""
    keyed = mutating_routes(system.app) - set(NO_IDEMPOTENCY_KEY)
    assert keyed == set(IDEMPOTENT_WALK), {
        "not walked": sorted(keyed - set(IDEMPOTENT_WALK)),
        "not mounted": sorted(set(IDEMPOTENT_WALK) - keyed),
    }

    learner = await system.register()
    main_domain, main_topic = await onboard(system, learner, "te2:main")
    ids = _placeholder_ids(main_domain, main_topic)

    for method, path in IDEMPOTENT_WALK:
        if path.startswith("/v1/sessions"):
            ids = replace(ids, domain_id=main_domain, topic_id=main_topic)
        key = f"te2:{method}:{path}"
        url = path_for(path, ids)
        body = body_for(method, path, ids, key)
        first_response = await _send(system, learner, method, url, body)
        assert first_response.status_code < 300, f"{method} {path}: {first_response.text}"
        before = await _events(system, learner.learner_id)
        again = await _send(system, learner, method, url, body)
        assert again.status_code == first_response.status_code, f"{method} {path}: {again.text}"
        assert _payload(again) == _payload(first_response), f"{method} {path}"
        assert await _events(system, learner.learner_id) == before, f"{method} {path}"
        if (method, path) not in NO_OTHER_BODY:
            other = await _send(
                system, learner, method, url, body_for(method, path, ids, key, variant=1)
            )
            assert other.status_code == 409, f"{method} {path}: {other.status_code} {other.text}"
            assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        ids = await _advance(system, ids, method, path, _payload(first_response))


async def _advance(system: System, ids: Ids, method: str, path: str, payload: Any) -> Ids:
    """Take the ids the walk needs next from a response."""
    if (method, path) == ("POST", "/v1/domains"):
        return replace(ids, domain_id=UUID(payload["domain_id"]))
    if path.endswith("/structure/propose"):
        return replace(
            ids,
            topic_id=UUID(payload["topics"][0]["topic_id"]),
            structure_version=payload["structure_version"],
        )
    if path.endswith("/structure/confirm"):
        await system.settle()
    if path.endswith("/diagnostic/start"):
        return replace(
            ids,
            diagnostic_session_id=UUID(payload["session_id"]),
            diagnostic_task_id=UUID(payload["next_question"]["task_id"]),
        )
    if (method, path) == ("POST", "/v1/sessions"):
        return replace(ids, session_id=UUID(payload["session_id"]))
    if path.endswith("/turns"):
        started = first(payload, "turn_started")
        assert started is not None
        return replace(ids, turn_id=UUID(started["turn_id"]))
    if path.endswith("/tasks/next"):
        return replace(ids, task_id=UUID(payload["task_id"]))
    if (method, path) == ("POST", "/v1/artifacts/notes"):
        return replace(ids, artifact_id=UUID(payload["artifact_id"]))
    return ids


NOTE_WORD = "quokkasecret"


async def test_cross_learner_isolation(system: System) -> None:
    """T-E3: learner B with ids of learner A gets 404 on every route with an id in the path;
    A's data does not change; B's search does not see A's note."""
    j = await run_journey(system, "te3")
    a = j.learner
    note = ok(
        await system.client.post(
            "/v1/artifacts/notes",
            json={
                "domain_id": str(j.domain_id),
                "title": "Mine",
                "text": f"Remember the {NOTE_WORD}",
                "idempotency_key": "te3:note",
            },
            headers=a.headers,
        ),
        201,
    )
    turn_started = first(j.turns[-1], "turn_started")
    assert turn_started is not None
    async with system.session_factory() as session:
        diag = (
            await session.execute(
                text(
                    "SELECT payload->>'session_id' AS sid FROM core.events "
                    "WHERE learner_id = :l AND type = 'diagnostic.started' LIMIT 1"
                ),
                {"l": a.learner_id},
            )
        ).one()
    ids = Ids(
        domain_id=j.domain_id,
        topic_id=j.topic_id,
        session_id=j.session_id,
        task_id=j.practice_task_id,
        turn_id=UUID(turn_started["turn_id"]),
        artifact_id=UUID(note["artifact_id"]),
        diagnostic_session_id=UUID(diag.sid),
        diagnostic_task_id=j.assigned_task_id,
        structure_version=1,
    )
    await system.settle()
    a_events = await _events(system, a.learner_id)

    b = await system.register()
    with_ids = sorted(
        (method.upper(), path)
        for path, operations in system.app.openapi()["paths"].items()
        if path.startswith("/v1/") and "{" in path
        for method in operations
    )
    assert with_ids
    for method, path in with_ids:
        url = path_for(path, ids)
        if method == "GET":
            response = await system.client.get(url, headers=b.headers)
        else:
            body = body_for(method, path, ids, f"te3:b:{method}:{path}")
            response = await system.client.request(method, url, json=body, headers=b.headers)
        assert response.status_code == 404, (
            f"{method} {path}: {response.status_code} {response.text}"
        )
        assert response.json()["error"]["code"] == "NOT_FOUND", f"{method} {path}"

    await system.settle()
    assert await _events(system, a.learner_id) == a_events
    found = ok(
        await system.client.get("/v1/artifacts/search", params={"q": NOTE_WORD}, headers=b.headers)
    )
    assert found["artifact_hits"] == [] and found["event_hits"] == [], found


async def test_concurrency_invariants(system: System) -> None:
    """T-E7: a parallel turn of the same session is 409 TURN_LOCKED; two parallel session
    starts leave exactly one active session and no 5xx."""
    learner = await system.register()
    domain_id, topic_id = await onboard(system, learner, "te7")
    session = ok(
        await system.client.post(
            "/v1/sessions",
            json={
                "domain_id": str(domain_id),
                "topic_id": str(topic_id),
                "idempotency_key": "te7:s",
            },
            headers=learner.headers,
        ),
        201,
    )
    session_id = UUID(session["session_id"])

    gate = system.llm.hold("TeacherOutput")
    first_turn = asyncio.create_task(turn(system, learner, session_id, "one", "te7:t1"))
    await asyncio.wait_for(gate.arrived.wait(), timeout=10)
    second = await system.client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "two", "idempotency_key": "te7:t2"},
        headers=learner.headers,
    )
    gate.release.set()
    events = await asyncio.wait_for(first_turn, timeout=30)
    assert first(events, "turn_completed") is not None
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "TURN_LOCKED"

    starts = await asyncio.gather(
        *(
            system.client.post(
                "/v1/sessions",
                json={"domain_id": str(domain_id), "idempotency_key": f"te7:par:{uuid4().hex}"},
                headers=learner.headers,
            )
            for _ in range(2)
        )
    )
    assert all(r.status_code in (200, 201) for r in starts), [r.text for r in starts]
    started_ids = {r.json()["session_id"] for r in starts}
    async with system.session_factory() as s:
        rows = (
            await s.execute(
                text("SELECT id, status FROM core.sessions WHERE learner_id = :l"),
                {"l": learner.learner_id},
            )
        ).all()
        abandoned_reason = await s.scalar(
            text(
                "SELECT payload->>'reason' FROM core.events WHERE learner_id = :l "
                "AND type = 'session.abandoned' AND payload->>'session_id' = ANY(:ids)"
            ),
            {"l": learner.learner_id, "ids": list(started_ids)},
        )
    statuses = {str(r.id): r.status for r in rows if str(r.id) in started_ids}
    assert sorted(statuses.values()) == ["abandoned", "active"], statuses
    assert sum(1 for r in rows if r.status == "active") == 1
    assert abandoned_reason == "superseded_by_new_session"
