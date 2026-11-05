"""The misconception describer, WP16.2 (ТЗ Ф4; Т-§9.3, Т-§9.2; `T-AG2`): not confirmed — exit
without a model call; the input is all manifestations and no earlier wording (law 10); the event
`misconception.described` with a key from the source (law 5). D-B-175."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import BaseModel

from quack.agents.misconception_describe import (
    NO_QUOTE,
    NONE_LINE,
    describe_misconception,
    render_manifestations,
    run_misconception_describe,
)
from quack.agents.schemas import MisconceptionDescription
from quack.config import Settings
from quack.db import Db
from quack.events.records import TaskMarkupRecord, TestedLevelRecord
from quack.events.values import AnswerSpec, MathValue
from quack.graph.read import ManifestationFact, MisconceptionDetail
from quack.jobs.runner import JobContext
from quack.jobs.types import ClaimedJob
from quack.llm.gateway import LLMGateway, LLMResult
from quack.practice.role_outputs import TaskCondition
from quack.practice.task_store import create_task
from quack.prompts.registry import PromptRegistry
from tests.db.fixtures import create_learner
from tests.support import graph_read as fx

SETTINGS = Settings()
DAY_MS = 86_400_000
AT_MS = 1_699_000_000_000  # 2023-11-03 UTC


@dataclass
class _Gateway:
    answer: MisconceptionDescription
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
        return LLMResult(
            value=self.answer, from_cache=False, prompt_hash="h", model="m", usage=None
        )


def _m(quote: str | None, task_id: UUID | None, at_ms: int, seq: int = 1) -> ManifestationFact:
    return ManifestationFact(
        evidence_id=uuid4(), quote=quote, task_id=task_id, source_seq=seq, at_ms=at_ms, tier="code"
    )


def _facts(status: Any, manifestations: list[ManifestationFact]) -> MisconceptionDetail:
    return MisconceptionDetail(
        misconception_id=uuid4(),
        status=status,
        statement="an earlier wording",
        skill_id=uuid4(),
        skill_name="adding fractions",
        manifestations=manifestations,
    )


async def _task(db: Db, learner_id: UUID, text: str) -> UUID:
    task_id = uuid4()
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=uuid4(),
        source="generated",
        condition=TaskCondition(text=text, latex=None),
        markup=TaskMarkupRecord(
            tests=[TestedLevelRecord(skill_id=uuid4(), level=2)],
            method_named=False,
            answer=AnswerSpec(type="number", value=MathValue(latex="5/6", sympy="5/6")),
        ),
        verification="checked",
        actor="task_generator@1",
        idempotency_key=f"task:{task_id}",
    )
    return task_id


async def test_describer_exits_if_not_confirmed(pg_db: Db) -> None:
    """T-AG2: no misconception, `suspected` — no call, no event."""
    learner_id = await create_learner(pg_db)
    gateway = _Gateway(MisconceptionDescription(statement="s", triggers=[]))
    for facts in (
        None,
        _facts("suspected", [_m("q", None, AT_MS)]),
        _facts("unknown", []),
    ):
        found = await describe_misconception(
            pg_db,
            cast(LLMGateway, gateway),
            SETTINGS,
            learner_id=learner_id,
            trigger_seq=7,
            facts=facts,
        )
        assert found is None
    assert gateway.calls == []


async def test_describer_writes_from_all_manifestations(pg_db: Db) -> None:
    """Т-§9.3: every manifestation in time order with its problem and date; the skill by name;
    no earlier statement; one event per trigger, a retry is the same event."""
    learner_id = await create_learner(pg_db)
    task_id = await _task(pg_db, learner_id, "Add 1/2 and 1/3")
    stranger_task = await _task(pg_db, await create_learner(pg_db), "Someone else's problem")
    facts = _facts(
        "watching",
        [
            _m("the bottoms add up too", task_id, AT_MS + DAY_MS),
            _m("1/2 + 1/3 = 2/5", None, AT_MS),
            _m("same for 1/4 + 1/4", stranger_task, AT_MS + 2 * DAY_MS),
        ],
    )
    gateway = _Gateway(
        MisconceptionDescription(
            statement=" Adds denominators when adding fractions. ",
            triggers=["fractions with different denominators", " "],
        )
    )

    async def run(trigger_seq: int) -> Any:
        return await describe_misconception(
            pg_db,
            cast(LLMGateway, gateway),
            SETTINGS,
            learner_id=learner_id,
            trigger_seq=trigger_seq,
            facts=facts,
        )

    first = await run(40)
    variables = gateway.calls[0]["variables"]
    assert variables["skill"] == "adding fractions"
    assert variables["manifestations"].splitlines() == [
        '1. 2023-11-03, in conversation, no problem; the learner: "1/2 + 1/3 = 2/5"',
        '2. 2023-11-04, problem: Add 1/2 and 1/3; the learner: "the bottoms add up too"',
        '3. 2023-11-05, in conversation, no problem; the learner: "same for 1/4 + 1/4"',
    ]
    assert "an earlier wording" not in str(variables)  # law 10
    PromptRegistry().load("misconception_describer", 1).render(variables)
    assert first.type == "misconception.described"
    assert first.actor == "misconception_describer@1"
    assert first.causation_seq == 40
    assert first.idempotency_key == f"misconception.described:{facts.misconception_id}:40"
    assert first.payload == {
        "misconception_id": str(facts.misconception_id),
        "statement": "Adds denominators when adding fractions.",
        "triggers": ["fractions with different denominators"],
    }
    assert (await run(40)).seq == first.seq
    assert (await run(41)).seq != first.seq


def test_no_manifestations_is_named() -> None:
    assert render_manifestations([], {}) == NONE_LINE


def test_same_time_by_source_seq_and_no_quote() -> None:
    """D-B-179: the same moment goes by the source seq; a manifestation without a quote is
    named so, not shown as empty quotes."""
    lines = render_manifestations(
        [_m("second", None, AT_MS, seq=9), _m(None, None, AT_MS, seq=3)], {}
    ).splitlines()
    assert lines == [
        f"1. 2023-11-03, in conversation, no problem; {NO_QUOTE}",
        '2. 2023-11-03, in conversation, no problem; the learner: "second"',
    ]


async def test_job_reads_the_detail_by_payload(pg_db: Db) -> None:
    """The job of the WP7.1 row: `misconception_id` and `trigger_seq` from the payload, the
    facts from `GraphReader.misconception_detail`; an unknown one ends without a call."""
    learner_id = await create_learner(pg_db)
    gateway = _Gateway(MisconceptionDescription(statement="Adds denominators.", triggers=[]))
    ctx = cast(JobContext, SimpleNamespace(db=pg_db, llm=gateway, settings=SETTINGS))

    def job(misconception_id: UUID) -> ClaimedJob:
        payload = {"trigger_seq": 5, "misconception_id": str(misconception_id)}
        return ClaimedJob(
            id=uuid4(),
            kind="misconception.describe",
            learner_id=learner_id,
            lock_key=f"misconception:{misconception_id}",
            coalesce_key=None,
            priority=0,
            payload=payload,
            attempt=1,
            max_attempts=3,
        )

    reader = fx.FixedGraphReader()
    assert await run_misconception_describe(job(uuid4()), ctx, reader) is None
    done = await run_misconception_describe(job(fx.MISCONCEPTION), ctx, reader)
    assert done is not None
    assert done.idempotency_key == f"misconception.described:{fx.MISCONCEPTION}:5"
    assert gateway.calls[0]["variables"]["skill"] == "common denominator"
