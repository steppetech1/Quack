"""Jobs of the task agents, WP16.6 / WP12.4 / WP12.6 / WP8.2 (ТЗ Ф4): `task.solution_space` and
`agent.solution_space` build the space with the known misconceptions and answer once;
`agent.task_generation` stores the checked variants and answers with their ids;
`check.known_claim` checks the skills of the topic at their next target level, one task per
skill. A repeat of any job writes no second `agent.completed`. D-B-191."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

from quack.agents.delivery import completed_key
from quack.agents.solution_space import answer_request as answer_space
from quack.agents.solution_space import ensure_space
from quack.agents.task_generation import known_check, run_request
from quack.config import Settings
from quack.db import Db
from quack.events import records
from quack.events.event_types import AgentRequested, KnownClaimed
from quack.events.log import NewEvent, StoredEvent, append_events
from quack.events.records import AgentParamsRecord
from quack.events.values import AnswerSpec, MathValue
from quack.jobs.runner import JobContext
from quack.llm.gateway import LLMResult
from quack.practice.role_outputs import (
    KeyStep,
    SolutionSpace,
    TaskCheckOutput,
    TaskCondition,
    TaskGeneratorOutput,
    WrongBranch,
)
from quack.practice.task_store import create_task, get_task
from tests.db.fixtures import create_learner
from tests.support import graph_read as fx

SETTINGS = Settings()
READER = fx.FixedGraphReader()
NUMBER = AnswerSpec(type="number", value=MathValue(latex="1", sympy="1"))


def _space() -> SolutionSpace:
    return SolutionSpace(
        key_steps=[KeyStep(statement=f"step {i}", expression=None) for i in range(3)],
        wrong_branches=[
            WrongBranch(sign="adds denominators", gap_type="confusion", misconception="M1"),
            WrongBranch(sign="drops a term", gap_type="execution", misconception=None),
        ],
        hints=["a", "b", "c"],
    )


def _draft(skill: str, level: int) -> dict[str, Any]:
    return {
        "condition": {"text": f"Compute 2 + 3 - 2, skill {skill}", "latex": None},
        "markup": {
            "tests": [{"skill": skill, "level": level}],
            "methodNamed": False,
            "answer": {"type": "number", "value": {"latex": "3", "sympy": "3"}},
        },
        "solution": "it is 3",
    }


class _Roles:
    """The space role answers a space; the generator a draft for the given skill and level; the
    checker agrees with the generator."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(
        self, role: str, version: int, model: Any, variables: dict[str, Any], *a: Any, **kw: Any
    ) -> LLMResult[Any]:
        self.calls.append({"role": role, "variables": variables, "key": a[3]})
        if role == "solution_space":
            value: Any = _space()
        elif role == "task_generator":
            level = int(variables["target_level"])
            value = TaskGeneratorOutput.model_validate({"tasks": [_draft("S1", level)]})
        else:
            value = TaskCheckOutput.model_validate(
                {
                    "answer_text": "3",
                    "answer_math": {"latex": "3", "sympy": "3"},
                    "methodNamed": False,
                    "is_variant": None,
                    "levels_fit": [True],
                    "note": "",
                }
            )
        return LLMResult(value, False, "h", "m", None)


def _ctx(db: Db, roles: _Roles) -> JobContext:
    return cast(JobContext, SimpleNamespace(db=db, llm=roles, settings=SETTINGS, compute=None))


async def _completed(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(l)s "
        "AND type = 'agent.completed' ORDER BY seq",
        {"l": learner_id},
    )
    return [r["payload"] for r in rows]


async def _request(
    db: Db, learner_id: UUID, kind: Any, params: AgentParamsRecord
) -> tuple[UUID, StoredEvent]:
    request_id = uuid4()
    (stored,) = await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="agent.requested",
                payload=AgentRequested(
                    request_id=request_id, kind=kind, params=params, needed_in_session=False
                ),
                actor="code",
                idempotency_key=f"agent.requested:{request_id}",
                domain_id=fx.DOMAIN,
            )
        ],
    )
    return request_id, stored


async def _bank_task(db: Db, learner_id: UUID) -> UUID:
    task_id = uuid4()
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=uuid4(),
        source="seminar",
        condition=TaskCondition(text="Add 1/2 and 1/3", latex=None),
        markup=records.TaskMarkupRecord(
            tests=[records.TestedLevelRecord(skill_id=fx.SKILL_FAILED, level=3)],
            method_named=False,
            answer=NUMBER,
        ),
        verification="unchecked",
        actor="code",
        idempotency_key=f"task:{task_id}",
    )
    return task_id


async def test_task_solution_space_job(pg_db: Db) -> None:
    """`task.solution_space`: the space is built once, with the misconception of the tested
    skill from memory."""
    learner_id = await create_learner(pg_db)
    task_id = await _bank_task(pg_db, learner_id)
    roles = _Roles()
    for _ in range(2):
        ctx = _ctx(pg_db, roles)
        space = await ensure_space(ctx, READER, learner_id=learner_id, task_id=task_id)
        assert space is not None
    (call,) = roles.calls
    assert fx.MISCONCEPTION_STATEMENT in call["variables"]["known_misconceptions"]
    doc = await get_task(pg_db, learner_id=learner_id, task_id=task_id)
    assert doc is not None and doc.solution_space is not None


async def test_agent_solution_space_job(pg_db: Db) -> None:
    """`agent.solution_space`: built and answered; a repeat does not answer twice; a request
    for a task that is not the learner's is answered `ok: false`."""
    learner_id = await create_learner(pg_db)
    task_id = await _bank_task(pg_db, learner_id)
    roles = _Roles()
    request_id, _ = await _request(
        pg_db, learner_id, "solution_space", AgentParamsRecord(task_id=task_id)
    )
    for _ in range(2):
        await answer_space(_ctx(pg_db, roles), READER, learner_id=learner_id, request_id=request_id)
    (done,) = await _completed(pg_db, learner_id)
    assert done["ok"] is True and done["task_ids"] == [str(task_id)]
    assert done["kind"] == "solution_space" and done["request_id"] == str(request_id)

    ghost, _ = await _request(
        pg_db, learner_id, "solution_space", AgentParamsRecord(task_id=uuid4())
    )
    await answer_space(_ctx(pg_db, roles), READER, learner_id=learner_id, request_id=ghost)
    failed = (await _completed(pg_db, learner_id))[-1]
    assert failed["ok"] is False and failed["error"] == "task_not_found"


async def test_agent_task_generation_job(pg_db: Db) -> None:
    """`agent.task_generation`: the checked variant is stored as `generated` and its id goes
    to `agent.completed`; a repeat stores and answers nothing new."""
    learner_id = await create_learner(pg_db)
    roles = _Roles()
    request_id, _ = await _request(
        pg_db,
        learner_id,
        "task_generation",
        AgentParamsRecord(skill_id=fx.SKILL_FAILED, level=3),
    )
    for _ in range(2):
        await run_request(_ctx(pg_db, roles), READER, learner_id=learner_id, request_id=request_id)
    (done,) = await _completed(pg_db, learner_id)
    assert done["ok"] is True and done["kind"] == "task_generation"
    (task_id,) = done["task_ids"]
    doc = await get_task(pg_db, learner_id=learner_id, task_id=UUID(task_id))
    assert doc is not None and doc.source == "generated"
    generator = next(c for c in roles.calls if c["role"] == "task_generator")
    assert generator["variables"]["kind"] == "number"
    created = await pg_db.fetch_all(
        "SELECT seq FROM core.events WHERE learner_id = %(l)s AND type = 'task.created'",
        {"l": learner_id},
    )
    assert len(created) == 1


async def test_check_known_claim_job(pg_db: Db) -> None:
    """`check.known_claim`: one task per skill of the topic at the next target level, then
    `agent.completed {kind: known_check, task_ids}` by the key of the job; a repeat is quiet."""
    learner_id = await create_learner(pg_db)
    roles = _Roles()
    (trigger,) = await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="known.claimed",
                payload=KnownClaimed(topic_id=fx.TOPIC_OPEN),
                actor="learner",
                idempotency_key=f"known:{uuid4()}",
                domain_id=fx.DOMAIN,
            )
        ],
    )
    for _ in range(2):
        await known_check(
            _ctx(pg_db, roles),
            READER,
            learner_id=learner_id,
            topic_id=fx.TOPIC_OPEN,
            trigger_seq=trigger.seq,
        )
    (done,) = await _completed(pg_db, learner_id)
    job_key = f"check.known_claim:{learner_id}:{trigger.seq}"
    assert done["ok"] is True and done["kind"] == "known_check" and done["job_key"] == job_key
    assert len(done["task_ids"]) == 2  # the two skills of the topic
    levels = {c["variables"]["target_level"] for c in roles.calls if c["role"] == "task_generator"}
    assert levels == {"2"}  # the hub requires 2 and 3, nothing is proven: the lowest first
    assert await pg_db.fetch_one(
        "SELECT 1 FROM core.events WHERE learner_id = %(l)s AND idempotency_key = %(k)s",
        {"l": learner_id, "k": completed_key(job_key)},
    )
