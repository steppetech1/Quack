"""E2E: idempotency and isolation (ТЗ Фазы 3 §8.7: T-E2, T-E3). T-E7 (a parallel turn, parallel
starts) left with the Phase 3 turn and the single active session (ТЗ Ф4 §8.15, П-4.10)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx

from tests.e2e.endpoints import mutating_routes
from tests.e2e.journey import ok, onboard
from tests.e2e.routes import (
    Ids,
    body_for,
    files_for,
    form_for,
    isolation_prepares,
    no_key_routes,
    path_for,
    walk,
)
from tests.e2e.routes.b import _session_domain
from tests.e2e.system import Learner, System
from tests.session.helpers import parse_sse


async def _events(system: System, learner_id: UUID) -> int:
    """Events of the learner in the log."""
    row = await system.resources.db.fetch_one(
        "SELECT count(*) AS n FROM core.events WHERE learner_id = %(learner_id)s",
        {"learner_id": learner_id},
    )
    return 0 if row is None else int(row["n"])


async def _send(
    system: System, learner: Learner, method: str, url: str, body: dict[str, Any]
) -> httpx.Response:
    return await system.client.request(method, url, json=body, headers=learner.headers)


def _payload(response: httpx.Response) -> Any:
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        return [(e, d) for e, d in parse_sse(response.content)]
    return response.json()


async def test_mutating_endpoints_are_idempotent(system_for_walk: System) -> None:
    """T-E2: repeat with the same key and body → same answer, no new events; same key with
    another body → 409. Endpoints without a key are listed with a reason."""
    system = system_for_walk
    steps = walk()
    keyed = mutating_routes(system.app) - set(no_key_routes())
    assert keyed == {r.key for r in steps}, {
        "not walked": sorted(keyed - {r.key for r in steps}),
        "not mounted": sorted({r.key for r in steps} - keyed),
    }

    learner = await system.register()
    main_domain, main_topic = await onboard(system, learner, "te2:main")
    onboarded = Ids(domain_id=main_domain, topic_id=main_topic)
    ids = onboarded

    for route in steps:
        method, path = route.key
        ids = ids.replace(**{name: getattr(onboarded, name) for name in route.restore})
        if route.prepare is not None:
            ids = await route.prepare(system, learner, ids)
        not_yet = [name for name in route.requires if ids.is_default(name)]
        assert not not_yet, f"{method} {path}: the walk has not created {not_yet} yet"
        key = f"te2:{method}:{path}"
        url = path_for(method, path, ids)
        body = body_for(method, path, ids, key)
        first_response = await _send(system, learner, method, url, body)
        assert first_response.status_code < 300, f"{method} {path}: {first_response.text}"
        # Background jobs of the step (turn, session finish) write events; let them land.
        await system.settle()
        before = await _events(system, learner.learner_id)
        again = await _send(system, learner, method, url, body)
        assert again.status_code == first_response.status_code, f"{method} {path}: {again.text}"
        assert _payload(again) == _payload(first_response), f"{method} {path}"
        assert await _events(system, learner.learner_id) == before, f"{method} {path}"
        if route.no_other_body is None:
            other = await _send(
                system, learner, method, url, body_for(method, path, ids, key, variant=1)
            )
            assert other.status_code == 409, f"{method} {path}: {other.status_code} {other.text}"
            assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        if route.advance is not None:
            ids = await route.advance(system, learner, ids, _payload(first_response))


async def test_cross_learner_isolation(system: System) -> None:
    """T-E3: learner B with ids of learner A gets 404 on every route with an id in the path;
    A's data does not change."""
    a = await system.register()
    domain_id, topic_id = await onboard(system, a, "te3")
    ids = Ids(domain_id=domain_id, topic_id=topic_id)
    # The session of the learner is on the Phase 4 log (D-B-210): its own domain, then a start.
    ids = await _session_domain(system, a, ids)
    started = ok(
        await system.client.post(
            "/v1/sessions",
            json={
                "domain_id": str(ids.session_domain_id),
                "intent": "learn",
                "idempotency_key": str(uuid4()),
            },
            headers=a.headers,
        ),
        201,
    )
    ids = ids.replace(session_id=UUID(started["session_id"]))
    for prepare in isolation_prepares():
        ids = await prepare(system, a, ids)
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
        url = path_for(method, path, ids)
        files = files_for(method, path)
        if method == "GET":
            response = await system.client.get(url, headers=b.headers)
        elif files is not None:
            response = await system.client.request(
                method, url, files=files, data=form_for(method, path), headers=b.headers
            )
        else:
            body = body_for(method, path, ids, f"te3:b:{method}:{path}")
            response = await system.client.request(method, url, json=body, headers=b.headers)
        assert response.status_code == 404, (
            f"{method} {path}: {response.status_code} {response.text}"
        )
        assert response.json()["error"]["code"] == "NOT_FOUND", f"{method} {path}"

    await system.settle()
    assert await _events(system, a.learner_id) == a_events
