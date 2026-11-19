"""«Сегодня» preparation, WP16.8 (ТЗ Ф4; Т-§14.4, `T-RV2`): `today.shown` → one planner call
→ a `prepared` plan per item under the session id of the item; `generate` → a task generation
request; a repeat writes nothing new. D-B-184."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from quack.agents import today
from quack.agents.schemas import TodayItemPlan, TodayPlannerOutput
from quack.agents.today import prepare_today, prepared_session_id
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import TodayItem, TodayShown
from quack.events.log import NewEvent, append_events
from quack.events.records import TaskMarkupRecord, TestedLevelRecord
from quack.events.values import AnswerSpec, MathValue
from quack.jobs.runner import JobContext
from quack.llm.gateway import LLMResult
from quack.practice.role_outputs import TaskCondition
from quack.practice.select import SelectedTask
from quack.practice.task_store import Verification, create_task
from tests.db.fixtures import create_learner
from tests.support import graph_read as fx

SETTINGS = Settings()


@dataclass
class _Gateway:
    answer: TodayPlannerOutput
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_structured(
        self,
        role: str,
        prompt_version: int,
        response_model: type[BaseModel],
        variables: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> LLMResult[Any]:
        self.calls.append({"role": role, "variables": variables})
        return LLMResult(
            value=self.answer, from_cache=False, prompt_hash="h", model="m", usage=None
        )


async def test_today_prepares_plans_and_requests(
    pg_db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    learner_id = await create_learner(pg_db)
    task_id = uuid4()
    await create_task(
        pg_db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=uuid4(),
        source="generated",
        condition=TaskCondition(text="Reduce 4/6", latex=None),
        markup=TaskMarkupRecord(
            tests=[TestedLevelRecord(skill_id=fx.SKILL_FAILED, level=2)],
            method_named=False,
            answer=AnswerSpec(type="number", value=MathValue(latex="2/3", sympy="2/3")),
        ),
        verification="checked",
        actor="task_generator@1",
        idempotency_key=f"task:{task_id}",
    )

    async def fake_select(_driver: Any, _settings: Any, **kw: Any) -> list[SelectedTask]:
        if kw["skill_id"] == fx.SKILL_FAILED:
            return [SelectedTask(task_id=task_id, traps_target=False, checked=True)]
        return []

    monkeypatch.setattr(today, "select_tasks", fake_select)
    (shown,) = await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="today.shown",
                payload=TodayShown(
                    domain_id=fx.DOMAIN,
                    items=[
                        TodayItem(
                            skill_id=fx.SKILL_FAILED,
                            topic_id=fx.TOPIC_OPEN,
                            reasons=["due"],
                            score=1.0,
                        ),
                        TodayItem(skill_id=fx.SKILL_QUIET, reasons=["deadline"], score=0.5),
                    ],
                ),
                actor="code",
                idempotency_key=f"today:{uuid4()}",
                domain_id=fx.DOMAIN,
            )
        ],
    )
    gateway = _Gateway(
        TodayPlannerOutput(
            items=[
                TodayItemPlan(
                    recommendation="1",
                    tasks=["K1", "K9"],
                    generate=False,
                    plan_steps=["recall the rule"],
                ),
                TodayItemPlan(
                    recommendation="2", tasks=[], generate=True, plan_steps=["worked example"]
                ),
            ]
        )
    )
    ctx = cast(JobContext, SimpleNamespace(db=pg_db, llm=gateway, settings=SETTINGS, neo4j=None))

    async def run() -> int:
        return await prepare_today(
            ctx, fx.FixedGraphReader(), learner_id=learner_id, trigger_seq=shown.seq
        )

    assert await run() == 2
    variables = gateway.calls[0]["variables"]
    assert "K1 (for 1): Reduce 4/6" in variables["candidate_tasks"]
    first = prepared_session_id(learner_id, shown.seq, fx.SKILL_FAILED)
    row = await pg_db.fetch_one(
        "SELECT plan, status FROM content.session_plans WHERE session_id = %(s)s", {"s": first}
    )
    assert row is not None and row["status"] == "prepared"
    assert row["plan"]["current_step"] == "recall the rule"
    assert row["plan"]["next_steps"] == ["Task: Reduce 4/6"]  # K9 is not a candidate
    requests = await pg_db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(l)s AND type = 'agent.requested'",
        {"l": learner_id},
    )
    assert [r["payload"]["params"]["skill_id"] for r in requests] == [str(fx.SKILL_QUIET)]

    await run()  # a repeat writes nothing new
    again = await pg_db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(l)s AND type = 'agent.requested'",
        {"l": learner_id},
    )
    assert len(again) == 1
    assert isinstance(first, UUID)


async def _two_items(db: Db, learner_id: UUID) -> int:
    (shown,) = await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="today.shown",
                payload=TodayShown(
                    domain_id=fx.DOMAIN,
                    items=[
                        TodayItem(skill_id=fx.SKILL_FAILED, reasons=["due"], score=1.0),
                        TodayItem(skill_id=fx.SKILL_QUIET, reasons=["deadline"], score=0.5),
                    ],
                ),
                actor="code",
                idempotency_key=f"today:{uuid4()}",
                domain_id=fx.DOMAIN,
            )
        ],
    )
    return shown.seq


async def _bank_task(db: Db, learner_id: UUID, text: str, verification: Verification) -> UUID:
    task_id = uuid4()
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=uuid4(),
        source="generated",
        condition=TaskCondition(text=text, latex=None),
        markup=TaskMarkupRecord(
            tests=[TestedLevelRecord(skill_id=fx.SKILL_FAILED, level=2)],
            method_named=False,
            answer=AnswerSpec(type="number", value=MathValue(latex="2/3", sympy="2/3")),
        ),
        verification=verification,
        actor="task_generator@1",
        idempotency_key=f"task:{task_id}",
    )
    return task_id


async def test_only_checked_bank_tasks_are_candidates(
    pg_db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP13.5: a bank task is shown only when `verification = checked`; the plan offers no
    other (an unchecked first candidate does not reach the planner)."""
    learner_id = await create_learner(pg_db)
    unchecked = await _bank_task(pg_db, learner_id, "Unchecked one", "disputed")
    checked = await _bank_task(pg_db, learner_id, "Checked one", "checked")

    async def fake_select(_driver: Any, _settings: Any, **kw: Any) -> list[SelectedTask]:
        return [
            SelectedTask(task_id=unchecked, traps_target=True, checked=False),
            SelectedTask(task_id=checked, traps_target=False, checked=True),
        ]

    monkeypatch.setattr(today, "select_tasks", fake_select)
    seq = await _two_items(pg_db, learner_id)
    gateway = _Gateway(
        TodayPlannerOutput(
            items=[
                TodayItemPlan(recommendation="1", tasks=[], generate=False, plan_steps=["a"]),
                TodayItemPlan(recommendation="2", tasks=[], generate=False, plan_steps=["b"]),
            ]
        )
    )
    ctx = cast(JobContext, SimpleNamespace(db=pg_db, llm=gateway, settings=SETTINGS, neo4j=None))

    await prepare_today(ctx, fx.FixedGraphReader(), learner_id=learner_id, trigger_seq=seq)

    block = gateway.calls[0]["variables"]["candidate_tasks"]
    assert "Checked one" in block and "Unchecked one" not in block


async def test_a_plan_with_a_missing_item_is_not_written(
    pg_db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The planner skipped a recommendation: by position the plans would go to the wrong
    skills, so none is written and no generation is requested."""
    learner_id = await create_learner(pg_db)

    async def fake_select(_driver: Any, _settings: Any, **kw: Any) -> list[SelectedTask]:
        return []

    monkeypatch.setattr(today, "select_tasks", fake_select)
    seq = await _two_items(pg_db, learner_id)
    gateway = _Gateway(
        TodayPlannerOutput(
            items=[TodayItemPlan(recommendation="2", tasks=[], generate=True, plan_steps=["b"])]
        )
    )
    ctx = cast(JobContext, SimpleNamespace(db=pg_db, llm=gateway, settings=SETTINGS, neo4j=None))

    assert (
        await prepare_today(ctx, fx.FixedGraphReader(), learner_id=learner_id, trigger_seq=seq) == 0
    )
    rows = await pg_db.fetch_all(
        "SELECT 1 FROM content.session_plans WHERE learner_id = %(l)s", {"l": learner_id}
    )
    requested = await pg_db.fetch_all(
        "SELECT 1 FROM core.events WHERE learner_id = %(l)s AND type = 'agent.requested'",
        {"l": learner_id},
    )
    assert rows == [] and requested == []
