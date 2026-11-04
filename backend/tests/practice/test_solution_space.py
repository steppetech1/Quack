"""The solution space of a task, WP12.4 (ТЗ Ф4; `Т-§5.5`; `T-TK5`): its shape, Wonder for a
`proof` answer or level 6, misconception labels → ids in the stored space, one build per task,
another learner's task untouched (D-B-166)."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from quack.config import Settings
from quack.db.db import Db
from quack.events import records
from quack.events.values import AnswerSpec, MathValue
from quack.llm.gateway import LLMGateway, LLMResult
from quack.practice.role_outputs import KeyStep, SolutionSpace, TaskCondition, WrongBranch
from quack.practice.solution_space import (
    NONE_LINE,
    SkillMisconception,
    build_solution_space,
)
from quack.practice.task_store import create_task, get_task
from quack.prompts.registry import PromptRegistry
from tests.db.fixtures import create_learner

SETTINGS = Settings()
SKILL = uuid4()
MISCONCEPTION = uuid4()
CONDITION = TaskCondition(text="Does the series of n!/n^n converge?", latex="\\sum n!/n^n")
NUMBER = AnswerSpec(type="number", value=MathValue(latex="1", sympy="1"))
PROOF = AnswerSpec(type="proof")


def _space(misconception: str | None = "M1") -> SolutionSpace:
    return SolutionSpace(
        key_steps=[
            KeyStep(statement="take the ratio", expression=None),
            KeyStep(statement="the ratio tends to 1/e", expression=None),
            KeyStep(statement="1/e < 1, so it converges", expression=None),
        ],
        wrong_branches=[
            WrongBranch(
                sign="uses the root test", gap_type="confusion", misconception=misconception
            ),
            WrongBranch(sign="drops the factorial", gap_type="execution", misconception=None),
        ],
        hints=["compare neighbors", "a_{n+1}/a_n", "(n/(n+1))^n -> 1/e"],
    )


class _Gateway:
    def __init__(self, answer: SolutionSpace) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(
        self,
        role: str,
        version: int,
        model: type[SolutionSpace],
        variables: dict[str, Any],
        learner_id: UUID,
        session_id: UUID | None,
        turn_id: UUID | None,
        key: str,
        *,
        check: Any = None,
        hard: bool = False,
    ) -> LLMResult[SolutionSpace]:
        self.calls.append(
            {
                "role": role,
                "version": version,
                "model": model,
                "variables": variables,
                "key": key,
                "hard": hard,
                "check": check,
            }
        )
        return LLMResult(self.answer, False, "h", "m", None)


async def _task(db: Db, learner_id: UUID, *, answer: AnswerSpec, level: int = 3) -> UUID:
    task_id = uuid4()
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=uuid4(),
        source="seminar",
        condition=CONDITION,
        markup=records.TaskMarkupRecord(
            tests=[records.TestedLevelRecord(skill_id=SKILL, level=level)],
            method_named=False,
            answer=answer,
        ),
        verification="unchecked",
        actor="code",
        idempotency_key=f"task:{task_id}",
    )
    return task_id


def test_solution_space_shape() -> None:
    """`T-TK5`: 3–8 key steps, 2–4 wrong branches, exactly 3 hints (Т-§5.5)."""
    good = _space()
    for field, value in (
        ("key_steps", good.key_steps[:2]),
        ("key_steps", good.key_steps * 3),
        ("wrong_branches", good.wrong_branches[:1]),
        ("wrong_branches", good.wrong_branches * 3),
        ("hints", good.hints[:2]),
        ("hints", [*good.hints, "one more"]),
    ):
        with pytest.raises(ValidationError):
            SolutionSpace.model_validate({**good.model_dump(), field: value})


@pytest.mark.parametrize(
    ("answer", "level", "hard"),
    [(NUMBER, 3, False), (PROOF, 3, True), (NUMBER, 6, True)],
)
async def test_build_and_store_the_space(
    pg_db: Db, answer: AnswerSpec, level: int, hard: bool
) -> None:
    """`T-TK5`, WP12.4: a hard call (Wonder, `hard_model` of the role) for a `proof` answer
    or level 6; the input blocks; the stored space keeps the misconception id,
    not the label; the version is the prompt; a second call builds nothing."""
    wonder = SETTINGS.model_for_role("teacher")
    assert SETTINGS.model_for_role("solution_space", hard=True) == wonder
    assert SETTINGS.model_for_role("solution_space") != wonder
    learner_id = await create_learner(pg_db)
    task_id = await _task(pg_db, learner_id, answer=answer, level=level)
    gateway = _Gateway(_space())
    known = [SkillMisconception(MISCONCEPTION, "uses the root test for factorials")]

    async def build() -> SolutionSpace | None:
        return await build_solution_space(
            pg_db,
            cast(LLMGateway, gateway),
            SETTINGS,
            learner_id=learner_id,
            task_id=task_id,
            misconceptions=known,
        )

    space = await build()
    (call,) = gateway.calls
    assert (call["role"], call["version"], call["hard"]) == ("solution_space", 1, hard)
    assert call["key"] == f"solution_space:{task_id}:solution_space@1"
    variables = call["variables"]
    assert variables["problem"] == f"{CONDITION.text}\n{CONDITION.latex}"
    assert variables["known_misconceptions"] == "M1: uses the root test for factorials"
    assert variables["reference_solution"] == NONE_LINE
    assert str(MISCONCEPTION) not in "".join(variables.values())
    PromptRegistry().load("solution_space", 1).render(variables)  # exactly these variables
    assert call["check"](_space("M7")) == "unknown labels: M7; use only the given labels"
    assert call["check"](_space()) is None

    assert space is not None
    assert space.wrong_branches[0].misconception == str(MISCONCEPTION)
    doc = await get_task(pg_db, learner_id=learner_id, task_id=task_id)
    assert doc is not None and doc.solution_space == space
    assert doc.solution_space_version == "solution_space@1"

    assert await build() == space
    assert len(gateway.calls) == 1  # one build per task


async def test_unknown_label_and_another_learner(pg_db: Db) -> None:
    """A label left unknown after the repair is stored as null; another learner's task is not
    built (Т-§3.5)."""
    owner, other = await create_learner(pg_db), await create_learner(pg_db)
    task_id = await _task(pg_db, owner, answer=NUMBER)
    gateway = _Gateway(_space("M9"))
    assert (
        await build_solution_space(
            pg_db, cast(LLMGateway, gateway), SETTINGS, learner_id=other, task_id=task_id
        )
        is None
    )
    assert gateway.calls == []
    space = await build_solution_space(
        pg_db, cast(LLMGateway, gateway), SETTINGS, learner_id=owner, task_id=task_id
    )
    assert space is not None and space.wrong_branches[0].misconception is None


async def test_two_builds_at_once_keep_the_first(pg_db: Db) -> None:
    """Two jobs of one task (bank rule and canvas request): the space stored first stays, the
    later build does not overwrite it."""
    learner_id = await create_learner(pg_db)
    task_id = await _task(pg_db, learner_id, answer=NUMBER)
    first = _space(None).model_copy(update={"hints": ["one", "two", "three"]})

    class _Racing(_Gateway):
        async def generate_structured(self, *args: Any, **kwargs: Any) -> LLMResult[SolutionSpace]:
            # the other job stores its space while this call is in flight
            await pg_db.execute(
                "UPDATE content.tasks SET solution_space = %(s)s, "
                "solution_space_version = 'solution_space@1' WHERE task_id = %(t)s",
                {"s": Jsonb(first.model_dump(mode="json")), "t": task_id},
            )
            return await super().generate_structured(*args, **kwargs)

    gateway = _Racing(_space("M1"))
    space = await build_solution_space(
        pg_db, cast(LLMGateway, gateway), SETTINGS, learner_id=learner_id, task_id=task_id
    )
    assert space == first
