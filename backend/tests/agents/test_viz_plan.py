"""Demos prepared ahead, the job `viz.plan` (owner decision 2026-11-20): the planner decides for the
current step and the next ones, a step gets an ordinary `agent.requested {kind: viz}` or a written
refusal, a step decided on is never asked about again, the per-session cap holds, and the prompts
of the two roles render."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from quack.agents.schemas import VizPlanItem, VizPlannerOutput
from quack.agents.viz_plan import (
    declined_key,
    plan_demos,
    request_id_of,
    request_key,
    steps_in_view,
)
from quack.config import Settings
from quack.db import Db
from quack.events.records import PlanStepRecord
from quack.llm.gateway import LLMGateway, LLMResult
from quack.prompts.registry import PromptRegistry
from tests.db.fixtures import create_learner

SETTINGS = Settings()


@dataclass
class _Gateway:
    """Answers each call from a table: step id -> (demo, title, brief)."""

    decisions: dict[str, tuple[bool, str | None, str | None]]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_structured(
        self,
        role: str,
        prompt_version: int,
        response_model: type[BaseModel],
        variables: dict[str, Any],
        learner_id: UUID,
        session_id: UUID | None,
        turn_id: UUID | None,
        idempotency_key: str,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> LLMResult[Any]:
        self.calls.append({"role": role, "variables": variables, "key": idempotency_key})
        asked = [line.split(":", 1)[0] for line in variables["steps"].splitlines()]
        items = [
            VizPlanItem(step_id=i, demo=d, title=t, brief=b)
            for i in asked
            for d, t, b in [self.decisions.get(i, (False, None, None))]
        ]
        return LLMResult(
            value=VizPlannerOutput(steps=items),
            from_cache=False,
            prompt_hash="h",
            model="m",
            usage=None,
        )


def _step(step_id: str, text: str = "") -> dict[str, Any]:
    return {"id": step_id, "text": text or step_id.replace("-", " "), "landmark": None}


async def _session(pg_db: Db, steps: list[str], current: str | None = None) -> tuple[UUID, UUID]:
    learner_id = await create_learner(pg_db)
    session_id, domain_id = uuid4(), uuid4()
    await pg_db.execute(
        "INSERT INTO content.sessions (session_id, learner_id, domain_id, intent, status) "
        "VALUES (%(s)s, %(l)s, %(d)s, 'learn', 'active')",
        {"s": session_id, "l": learner_id, "d": domain_id},
    )
    plan = {
        "v": 2,
        "goal": "Understand projectile motion",
        "current_step": _step(current) if current else None,
        "next_steps": [_step(s) for s in steps],
        "landmark_edits": {"added": [], "removed": []},
        "branches": [],
    }
    await pg_db.execute(
        "INSERT INTO content.session_plans (session_id, learner_id, plan, status) "
        "VALUES (%(s)s, %(l)s, %(p)s, 'active')",
        {"s": session_id, "l": learner_id, "p": Jsonb(plan)},
    )
    return learner_id, session_id


async def _events(pg_db: Db, learner_id: UUID, event_type: str) -> list[dict[str, Any]]:
    rows = await pg_db.fetch_all(
        "SELECT payload, idempotency_key, session_id FROM core.events "
        "WHERE learner_id = %(l)s AND type = %(t)s ORDER BY seq",
        {"l": learner_id, "t": event_type},
    )
    return [dict(r) for r in rows]


async def _run(pg_db: Db, gateway: _Gateway, learner_id: UUID, session_id: UUID, **kw: Any) -> Any:
    settings = kw.pop("settings", SETTINGS)
    return await plan_demos(
        pg_db,
        cast(LLMGateway, gateway),
        settings,
        learner_id=learner_id,
        session_id=session_id,
        **kw,
    )


def test_steps_in_view_takes_current_first_once_and_cuts_to_the_horizon() -> None:
    def s(i: str) -> PlanStepRecord:
        return PlanStepRecord(id=i, text=i, landmark=None)

    assert [x.id for x in steps_in_view(s("a"), [s("b"), s("a"), s("c"), s("d")], 3)] == [
        "a",
        "b",
        "c",
    ]
    assert [x.id for x in steps_in_view(None, [s("b"), s("c")], 5)] == ["b", "c"]
    assert steps_in_view(None, [], 3) == []


async def test_a_demo_is_ordered_for_a_step_that_wants_one_and_the_others_are_declined(
    pg_db: Db,
) -> None:
    learner_id, session_id = await _session(pg_db, ["projectile", "definition"], current="start")
    gateway = _Gateway(
        {
            "projectile": (
                True,
                "Flight of a ball",
                "Show a projectile with a launch angle slider.",
            ),
        }
    )

    ordered = await _run(pg_db, gateway, learner_id, session_id, trigger_seq=7)

    assert ordered == [request_id_of(session_id, "projectile")]
    (call,) = gateway.calls
    assert call["key"].startswith(f"viz.plan:{session_id}:start")
    assert call["variables"]["goal"] == "Understand projectile motion"
    assert call["variables"]["steps"].splitlines()[0] == "start: start"
    assert call["variables"]["existing"] == "(none)"
    PromptRegistry().load("viz_planner", 1).render(call["variables"])

    (requested,) = await _events(pg_db, learner_id, "agent.requested")
    assert requested["idempotency_key"] == request_key(session_id, "projectile")
    assert requested["session_id"] == session_id
    payload = requested["payload"]
    assert payload["kind"] == "viz" and payload["needed_in_session"] is False
    assert payload["request_id"] == str(request_id_of(session_id, "projectile"))
    assert payload["params"]["step_id"] == "projectile"
    assert payload["params"]["title"] == "Flight of a ball"
    assert payload["params"]["description"] == "Show a projectile with a launch angle slider."
    declined = await _events(pg_db, learner_id, "agent.completed")
    assert sorted(d["idempotency_key"] for d in declined) == sorted(
        [declined_key(session_id, "start"), declined_key(session_id, "definition")]
    )
    assert all(d["payload"]["kind"] == "viz_plan" and d["payload"]["ok"] for d in declined)


async def test_a_step_decided_on_is_not_asked_about_again(pg_db: Db) -> None:
    learner_id, session_id = await _session(pg_db, ["projectile", "definition"])
    gateway = _Gateway({"projectile": (True, "Flight", "Show it.")})
    await _run(pg_db, gateway, learner_id, session_id)
    assert len(gateway.calls) == 1

    again = await _run(pg_db, gateway, learner_id, session_id)

    assert again == [] and len(gateway.calls) == 1  # no model call at all
    assert len(await _events(pg_db, learner_id, "agent.requested")) == 1


async def test_a_later_step_in_view_is_asked_with_the_demos_so_far(pg_db: Db) -> None:
    learner_id, session_id = await _session(pg_db, ["projectile"])
    gateway = _Gateway(
        {"projectile": (True, "Flight", "Show it."), "orbit": (True, "Orbit", "Orbit.")}
    )
    await _run(pg_db, gateway, learner_id, session_id)
    await pg_db.execute(
        "UPDATE content.session_plans SET plan = jsonb_set(plan, '{next_steps}', "
        "plan -> 'next_steps' || %(more)s) WHERE session_id = %(s)s",
        {"more": Jsonb([_step("orbit")]), "s": session_id},
    )

    ordered = await _run(pg_db, gateway, learner_id, session_id)

    assert ordered == [request_id_of(session_id, "orbit")]
    assert gateway.calls[1]["variables"]["steps"] == "orbit: orbit"
    assert gateway.calls[1]["variables"]["existing"] == "- Flight"


async def test_the_cap_of_a_session_stops_further_demos(pg_db: Db) -> None:
    learner_id, session_id = await _session(pg_db, ["a", "b", "c"])
    wants: dict[str, tuple[bool, str | None, str | None]] = {
        k: (True, k, f"demo {k}") for k in "abc"
    }
    gateway = _Gateway(wants)
    capped = SETTINGS.model_copy(update={"viz_prefetch_max_per_session": 2})

    ordered = await _run(pg_db, gateway, learner_id, session_id, settings=capped)

    assert len(ordered) == 2
    assert len(await _events(pg_db, learner_id, "agent.requested")) == 2
    # the third was turned down for lack of room, and that is written too: not asked again
    assert len(await _events(pg_db, learner_id, "agent.completed")) == 1
    assert await _run(pg_db, gateway, learner_id, session_id, settings=capped) == []
    assert len(gateway.calls) == 1


async def test_the_horizon_limits_the_steps_asked_about(pg_db: Db) -> None:
    learner_id, session_id = await _session(pg_db, ["a", "b", "c", "d"], current="now")
    gateway = _Gateway({})
    await _run(pg_db, gateway, learner_id, session_id)
    asked = [line.split(":")[0] for line in gateway.calls[0]["variables"]["steps"].splitlines()]
    assert asked == ["now", "a", "b"]  # viz_prefetch_horizon = 3: the current and two more


async def test_a_demo_without_a_brief_is_a_refusal_and_a_switched_off_planner_does_nothing(
    pg_db: Db,
) -> None:
    learner_id, session_id = await _session(pg_db, ["x"])
    gateway = _Gateway({"x": (True, "Title", "   ")})
    assert await _run(pg_db, gateway, learner_id, session_id) == []
    assert await _events(pg_db, learner_id, "agent.requested") == []

    off = SETTINGS.model_copy(update={"viz_prefetch_enabled": False})
    learner2, session2 = await _session(pg_db, ["y"])
    quiet = _Gateway({})
    assert await _run(pg_db, quiet, learner2, session2, settings=off) == []
    assert quiet.calls == []


async def test_a_finished_session_gets_no_demos(pg_db: Db) -> None:
    """A replay of the log must not order demos for a session that is over."""
    learner_id, session_id = await _session(pg_db, ["projectile"])
    await pg_db.execute(
        "UPDATE content.sessions SET status = 'ended' WHERE session_id = %(s)s", {"s": session_id}
    )
    gateway = _Gateway({"projectile": (True, "Flight", "Show it.")})
    assert await _run(pg_db, gateway, learner_id, session_id) == []
    assert gateway.calls == []


async def test_no_session_or_no_steps_calls_nobody(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    gateway = _Gateway({})
    assert await _run(pg_db, gateway, learner_id, uuid4()) == []
    empty_learner, empty_session = await _session(pg_db, [])
    assert await _run(pg_db, gateway, empty_learner, empty_session) == []
    assert gateway.calls == []


def test_the_prompt_of_the_builder_v2_renders_and_names_the_kit() -> None:
    prompt = PromptRegistry().load("viz_builder", 2)
    text = prompt.render({"description": "d", "libraries": "(none)", "previous_errors": "(none)"})
    assert "window.QUACK_VIZ_PARAMS" in text and "QV.mount" in text and "QV.slider" in text
