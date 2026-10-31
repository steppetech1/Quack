"""The Locust scenario reads the real turn stream (ТЗ Фазы 3 WP10.3): frame names and order
of the API are the ones `loadtest.scenario.read_turn_stream` measures."""

from __future__ import annotations

import time
from uuid import UUID

from loadtest.scenario import read_turn_stream, session_start_body, turn_body
from tests.e2e.journey import ok, onboard
from tests.e2e.system import System


async def test_scenario_reads_a_real_turn(system: System) -> None:
    learner = await system.register()
    domain_id, topic_id = await onboard(system, learner, "lt")
    started = ok(
        await system.client.post(
            "/v1/sessions",
            json=session_start_body(domain_id, topic_id, "lt:s"),
            headers=learner.headers,
        ),
        201,
    )
    session_id = UUID(started["session_id"])
    t0 = time.monotonic()
    response = await system.client.post(
        f"/v1/sessions/{session_id}/turns",
        json=turn_body("What is a derivative?", "lt:t1"),
        headers=learner.headers,
    )
    assert response.status_code == 200, response.text
    result = read_turn_stream(iter(response.content.splitlines()), started_at=t0)
    assert result.complete, result.events
    assert result.turn_id is not None
    assert result.status_ms is not None and result.token_ms is not None
