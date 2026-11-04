"""Check of a teacher's task after the turn, WP12.5 (ТЗ Ф4; `Т-§5.6`; `T-TK6`): the checker
solves blind, code compares its answer with the key by `check()`; a wrong key →
`task.key_disputed`, a wrong markup → `task.markup_disputed` with the correction, else
`task.verified`; one check per task (D-B-167)."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from quack.config import Settings
from quack.db.db import Db
from quack.events import records
from quack.events.values import AnswerOption, AnswerSpec
from quack.llm.gateway import LLMGateway, LLMResult
from quack.practice.role_outputs import TaskCheckOutput, TaskCondition
from quack.practice.task_check import NONE_LINE, check_key, check_teacher_task, verify_generated
from quack.practice.task_store import create_task
from quack.prompts.registry import PromptRegistry
from tests.db.fixtures import create_learner

SETTINGS = Settings()
SKILL, OTHER = uuid4(), uuid4()
NAMES = {SKILL: "D'Alembert test", OTHER: "Limits"}
CONDITION = TaskCondition(text="Which test decides the series of n!/n^n?", latex=None)
CHOICE = AnswerSpec(
    type="choice",
    options=[
        AnswerOption(option_id="A", text="the ratio test", correct=True),
        AnswerOption(option_id="B", text="the integral test", correct=False),
    ],
)


def _output(answer: str = "A", **kw: Any) -> TaskCheckOutput:
    base: dict[str, Any] = {
        "answer_text": answer,
        "answer_math": None,
        "methodNamed": False,
        "is_variant": None,
        "levels_fit": [True, True],
        "note": "",
    }
    base.update(kw)
    return TaskCheckOutput.model_validate(base)


class _Gateway:
    def __init__(self, answer: TaskCheckOutput) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(
        self,
        role: str,
        version: int,
        model: type[TaskCheckOutput],
        variables: dict[str, Any],
        learner_id: UUID,
        session_id: UUID | None,
        turn_id: UUID | None,
        key: str,
        **kw: Any,
    ) -> LLMResult[TaskCheckOutput]:
        self.calls.append({"role": role, "version": version, "variables": variables, "key": key})
        return LLMResult(self.answer, False, "h", "m", None)


async def _task(db: Db, learner_id: UUID, *, variant_of: UUID | None = None) -> UUID:
    task_id = uuid4()
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=uuid4(),
        source="teacher",
        condition=CONDITION,
        markup=records.TaskMarkupRecord(
            tests=[
                records.TestedLevelRecord(skill_id=SKILL, level=4),
                records.TestedLevelRecord(skill_id=OTHER, level=2),
            ],
            method_named=False,
            variant_of=variant_of,
            answer=CHOICE,
        ),
        verification="unchecked",
        actor="teacher@2",
        idempotency_key=f"task:{task_id}",
    )
    return task_id


async def _check(db: Db, learner_id: UUID, task_id: UUID, output: TaskCheckOutput) -> Any:
    gateway = _Gateway(output)
    stored = await check_teacher_task(
        db,
        cast(LLMGateway, gateway),
        SETTINGS,
        learner_id=learner_id,
        task_id=task_id,
        skill_names=NAMES,
        causation_seq=7,
    )
    return stored, gateway


async def test_task_verification_table(pg_db: Db) -> None:
    """`T-TK6`, row «teacher on the fly»: the input is blind (no key), the answer is compared
    by `check()`; the three outcomes."""
    learner_id = await create_learner(pg_db)

    task_id = await _task(pg_db, learner_id)
    verified, gateway = await _check(pg_db, learner_id, task_id, _output())
    assert verified.type == "task.verified"
    assert verified.idempotency_key == check_key(task_id) and verified.causation_seq == 7
    assert verified.actor == "task_checker@1"
    (call,) = gateway.calls
    assert (call["role"], call["version"]) == ("task_checker", 1)
    variables = call["variables"]
    assert variables["options"] == "A. the ratio test\nB. the integral test"
    assert variables["claimed_markup"] == (
        "method named: no\ntested skills and levels:\n"
        "1. D'Alembert test — level 4\n2. Limits — level 2"
    )
    assert variables["source_problem"] == NONE_LINE
    assert "correct" not in variables["options"]  # the key is not given
    PromptRegistry().load("task_checker", 1).render(variables)

    key = await _task(pg_db, learner_id)
    disputed, _ = await _check(pg_db, learner_id, key, _output("B"))
    assert disputed.type == "task.key_disputed"
    assert disputed.payload["corrected_markup"] is None

    level = await _task(pg_db, learner_id)
    markup, _ = await _check(pg_db, learner_id, level, _output(levels_fit=[False, True]))
    assert markup.type == "task.markup_disputed"
    corrected = records.TaskMarkupRecord.model_validate(markup.payload["corrected_markup"])
    assert [(t.skill_id, t.level) for t in corrected.tests] == [(OTHER, 2)]

    both = await _task(pg_db, learner_id)
    named, _ = await _check(pg_db, learner_id, both, _output("B", methodNamed=True))
    assert named.type == "task.key_disputed"  # the key goes first, with the correction
    fixed = records.TaskMarkupRecord.model_validate(named.payload["corrected_markup"])
    assert fixed.method_named is True and len(fixed.tests) == 2


async def test_variant_and_retry(pg_db: Db) -> None:
    """A task that is no variant of its source loses `variant_of`; the source problem is shown
    to the checker; a retry of the job is the same event (law 5); another learner's task —
    nothing."""
    learner_id = await create_learner(pg_db)
    source = await _task(pg_db, learner_id)
    task_id = await _task(pg_db, learner_id, variant_of=source)
    first, gateway = await _check(pg_db, learner_id, task_id, _output(is_variant=False))
    assert gateway.calls[0]["variables"]["source_problem"] == CONDITION.text
    assert first.type == "task.markup_disputed"
    corrected = records.TaskMarkupRecord.model_validate(first.payload["corrected_markup"])
    assert corrected.variant_of is None
    again, _ = await _check(pg_db, learner_id, task_id, _output(is_variant=False))
    assert again.seq == first.seq

    other = await create_learner(pg_db)
    assert (await _check(pg_db, other, task_id, _output()))[0] is None


@pytest.mark.parametrize("fits", [[False, False]])
async def test_no_level_fits(pg_db: Db, fits: list[bool]) -> None:
    """No tested level fits: disputed, the markup keeps its tests (nothing to keep instead)."""
    learner_id = await create_learner(pg_db)
    task_id = await _task(pg_db, learner_id)
    stored, _ = await _check(pg_db, learner_id, task_id, _output(levels_fit=fits))
    assert stored.type == "task.markup_disputed"
    corrected = records.TaskMarkupRecord.model_validate(stored.payload["corrected_markup"])
    assert len(corrected.tests) == 2


class _Draft:
    condition = CONDITION
    answer_spec = CHOICE


MARKUP = records.TaskMarkupRecord(
    tests=[records.TestedLevelRecord(skill_id=SKILL, level=4)],
    method_named=False,
    answer=CHOICE,
)


class _Roles:
    """Answers by role; records the calls."""

    def __init__(self, **answers: TaskCheckOutput) -> None:
        self.answers = answers
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(
        self, role: str, version: int, model: Any, variables: dict[str, Any], *a: Any, **kw: Any
    ) -> LLMResult[TaskCheckOutput]:
        self.calls.append({"role": role, "variables": variables, "key": a[3]})
        return LLMResult(self.answers[role], False, "h", "m", None)


async def _verify(gateway: _Roles) -> bool:
    return await verify_generated(
        cast(LLMGateway, gateway),
        SETTINGS,
        _Draft(),
        MARKUP,
        learner_id=uuid4(),
        key="gen:1:0",
        skill_names=NAMES,
        generator_solution="ratio -> 1/e < 1",
    )


async def test_generated_row_with_arbiter() -> None:
    """`T-TK6`, row «generated»: the checker agrees → kept, no arbiter; disagrees → the arbiter
    (Wonder) gets both solutions and the disagreement; it agrees → kept; disagrees → thrown."""
    ok = _Roles(task_checker=_output(levels_fit=[True]))
    assert await _verify(ok) is True
    assert [c["role"] for c in ok.calls] == ["task_checker"]
    assert ok.calls[0]["key"] == "gen:1:0:task_checker@1"

    saved = _Roles(task_checker=_output("B", levels_fit=[True]), task_arbiter=_output("A"))
    assert await _verify(saved) is True
    arbiter = saved.calls[1]
    assert (arbiter["role"], arbiter["key"]) == ("task_arbiter", "gen:1:0:task_arbiter@1")
    variables = arbiter["variables"]
    assert variables["generator_solution"] == "ratio -> 1/e < 1"
    assert variables["checker_solution"] == "B"
    assert variables["disagreement"] == "the independent answer differs from the key."
    PromptRegistry().load("task_arbiter", 1).render(variables)

    lost = _Roles(
        task_checker=_output(levels_fit=[False], note="level 4 needs the method unnamed"),
        task_arbiter=_output(levels_fit=[False]),
    )
    assert await _verify(lost) is False
    assert lost.calls[1]["variables"]["disagreement"] == (
        "levels 1 do not fit. Checker's note: level 4 needs the method unnamed"
    )
    wrong_key = _Roles(task_checker=_output("B", levels_fit=[True]), task_arbiter=_output("B"))
    assert await _verify(wrong_key) is False
