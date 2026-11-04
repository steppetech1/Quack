"""E2E: a deadline from the talk → a hub → «Сегодня» (ТЗ Ф4 §8.14, T-E9; WP6.5, WP16.8, WP17).

The learner says a date in a message; the teacher's markup carries it (`deadline`, with the
learner's own words as the quote); the turn writes the hub with its coverage and the date; the
domain's `today` lists the topic with the reason `deadline` and warns that the pace will not make
it; the planner prepares a plan for the item. The last step, a session that starts from the item
with the plan ready, is a test of its own (red)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.e2e.conftest import StandFactory
from tests.e2e.journey import ok, onboard
from tests.e2e.system import System
from tests.e2e.text_journey import (
    SCHEMA,
    act,
    event_types,
    start_session,
    teacher_turn,
)

QUOTE = "colloquium on Friday with proofs"


def planner(system: System) -> None:
    system.llm.respond(
        "TodayPlannerOutput",
        lambda _r: {
            "items": [
                {
                    "recommendation": "1",
                    "tasks": [],
                    "generate": False,
                    "plan_steps": ["Recall the ratio", "Prove the sum formula"],
                }
            ]
        },
    )


def deadline_turn() -> dict[str, Any]:
    turn = teacher_turn(with_task=False)
    turn["p3_markup"]["deadline"] = {
        "hub_label": None,
        "new_hub_name": "Colloquium",
        "deadline_at": (datetime.now(UTC) + timedelta(days=5)).isoformat(),
        "coverage": ["T1"],
        "quote": QUOTE,
        "control_type": "colloquium_proofs",
    }
    return turn


async def deadline_session(system: System) -> Any:
    """Sign in, a topic, a session, and the message that names the date."""
    planner(system)
    system.llm.respond(SCHEMA, lambda _r: deadline_turn())
    learner = await system.sign_in_by_link()
    domain_id, topic_id = await onboard(system, learner, "te9")
    session_id = await start_session(system, learner, domain_id, topic_id)
    said = await act(
        system, learner, session_id, {"kind": "message", "text": f"I have a {QUOTE}, help"}
    )
    assert said.status_code == 200, said.text
    await system.settle()
    return learner, domain_id, topic_id


async def test_hub_deadline_today(stand: StandFactory) -> None:
    """T-E9."""
    async with stand(settings_update={"pace_min_active_days": 1}) as system:
        learner, domain_id, topic_id = await deadline_session(system)
        types = [t for _, t in await event_types(system, learner)]
        assert {"hub.created", "hub.topics_changed", "hub.deadline_set"} <= set(types), types

        listed = await system.client.get(f"/v1/domains/{domain_id}/hubs", headers=learner.headers)
        assert listed.status_code == 200, listed.text
        [hub] = [h for h in listed.json() if h["name"] == "Colloquium"]
        assert hub["deadline_at_ms"], hub
        assert [t["topic_id"] for t in hub["topics"]] == [str(topic_id)], hub

        today = ok(
            await system.client.get(f"/v1/domains/{domain_id}/today", headers=learner.headers)
        )
        assert today["items"], today
        assert any("deadline" in item["reasons"] for item in today["items"]), today
        assert [w["hub_id"] for w in today["pace_warnings"]] == [hub["hub_id"]], today

        await system.settle()  # `today.shown` → the dispatcher → `today.prepare`
        plans = await system.resources.db.fetch_all(
            "SELECT plan, status FROM content.session_plans WHERE learner_id = %(l)s "
            "AND status = 'prepared'",
            {"l": learner.learner_id},
        )
        assert len(plans) == len(today["items"]), plans
        assert plans[0]["plan"]["current_step"] == "Recall the ratio", plans


@pytest.mark.xfail(
    strict=True,
    reason="B: nothing adopts the prepared plan (`prepared_session_id`) when a session starts "
    "from a «Сегодня» item (WP16.8, WP13.6; D-C-191)",
)
async def test_session_from_today_item_has_its_plan(stand: StandFactory) -> None:
    """T-E9, the last step: the session started on the topic of the item opens with the plan the
    planner prepared."""
    async with stand(settings_update={"pace_min_active_days": 1}) as system:
        learner, domain_id, topic_id = await deadline_session(system)
        ok(await system.client.get(f"/v1/domains/{domain_id}/today", headers=learner.headers))
        await system.settle()
        session_id = await start_session(system, learner, domain_id, topic_id)
        plan = await system.resources.db.fetch_one(
            "SELECT plan FROM content.session_plans WHERE session_id = %(s)s", {"s": session_id}
        )
        assert plan is not None and plan["plan"]["current_step"] == "Recall the ratio", plan
