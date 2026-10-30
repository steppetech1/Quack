"""Deterministic LLM degradation of a turn (ТЗ §2.3.5, Т-§17.1)."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from tests.session.fixtures import SeededDomain
from tests.session.helpers import parse_sse

pytest_plugins = ["tests.session.fixtures"]


async def _start_session(
    client: httpx.AsyncClient, auth_headers: dict[str, str], seeded_domain: SeededDomain, key: str
) -> UUID:
    resp = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seeded_domain.domain_id),
            "topic_id": str(seeded_domain.topic_id),
            "idempotency_key": key,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return UUID(resp.json()["session_id"])


@pytest.mark.parametrize("cause", ["provider_unavailable", "session_budget"])
async def test_turn_degrades_to_task_bank_when_llm_is_down_or_over_budget(
    cause: str,
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    settings: Settings,
    fake_llm,
) -> None:
    """ТЗ degradation test 1: 200 SSE, a math/choice task from the bank, degraded_mode set."""
    session_id = await _start_session(client, auth_headers, seeded_domain, f"start-{cause}")
    if cause == "provider_unavailable":
        fake_llm.unavailable = True
        expected_mode = "llm_unavailable"
    else:
        async with db() as session, session.begin():
            await session.execute(
                text("UPDATE core.sessions SET tokens_used = :t WHERE id = :s"),
                {"t": settings.session_token_limit, "s": session_id},
            )
        expected_mode = "budget_exceeded"

    resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "Teach me something", "idempotency_key": f"turn-{cause}"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    events = parse_sse(resp.text)
    by_name = {name: data for name, data in events}
    names = [name for name, _ in events]
    assert names[0] == "turn_started"
    assert names[-1] == "done"

    assigned = by_name["task_assigned"]
    assert UUID(assigned["task_id"]) in seeded_domain.task_ids
    assert assigned["kind"] in ("math", "choice")
    assert assigned["prompt_text"] in by_name["token"]["delta"]
    assert by_name["turn_completed"]["degraded_mode"] == expected_mode

    async with db() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT type, payload FROM core.events "
                    "WHERE learner_id = :l AND session_id = :s ORDER BY seq"
                ),
                {"l": seeded_domain.learner_id, "s": session_id},
            )
        ).all()
    types = [r.type for r in rows]
    assert "task.assigned" in types
    teacher = next(r.payload for r in rows if r.type == "turn.teacher_replied")
    assert teacher["degraded_mode"] == expected_mode
    assert assigned["prompt_text"] in teacher["text"]
