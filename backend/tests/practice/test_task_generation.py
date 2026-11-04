"""Task generation, WP12.6 (ТЗ Ф4; `Т-§5.4`, `Т-§5.6`; `T-TK6` row «generated»): variants of
one call share a `familyId`; a hard request (proof, level 6) is a hard call; a markup that
breaks `Т-§5.3` goes back to the model and is dropped if still wrong; a variant that the check
throws away is not stored; a retry stores nothing new (D-B-168)."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from quack.config import Settings
from quack.db.db import Db
from quack.events import records
from quack.events.values import AnswerSpec
from quack.llm.gateway import LLMGateway, LLMResult
from quack.llm.labels import LabelMap
from quack.practice.role_outputs import TaskCheckOutput, TaskCondition, TaskGeneratorOutput
from quack.practice.task_generation import (
    GenerationRequest,
    family_id,
    generate_tasks,
    generator_variables,
)
from quack.practice.task_store import create_task, get_task
from quack.prompts.registry import PromptRegistry
from tests.db.fixtures import create_learner

SETTINGS = Settings()
SKILL, MISCONCEPTION = uuid4(), uuid4()


def _draft(answer: str, *, level: int = 3, skill: str = "S1") -> dict[str, Any]:
    return {
        "condition": {"text": f"Compute 2 + {answer} - 2", "latex": None},
        "markup": {
            "tests": [{"skill": skill, "level": level}],
            "methodNamed": False,
            "answer": {"type": "number", "value": {"latex": answer, "sympy": answer}},
        },
        "solution": f"it is {answer}",
    }


def _check(answer: str) -> TaskCheckOutput:
    return TaskCheckOutput.model_validate(
        {
            "answer_text": answer,
            "answer_math": {"latex": answer, "sympy": answer},
            "methodNamed": False,
            "is_variant": None,
            "levels_fit": [True],
            "note": "",
        }
    )


class _Roles:
    """The generator answers with drafts; checker and arbiter answer by the key of the draft."""

    def __init__(self, drafts: list[dict[str, Any]], checks: dict[str, str]) -> None:
        self.drafts = drafts
        self.checks = checks
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(
        self, role: str, version: int, model: Any, variables: dict[str, Any], *a: Any, **kw: Any
    ) -> LLMResult[Any]:
        key = a[3]
        self.calls.append({"role": role, "key": key, "variables": variables, **kw})
        if role == "task_generator":
            return LLMResult(
                TaskGeneratorOutput.model_validate({"tasks": self.drafts}), False, "h", "m", None
            )
        draft = key.rsplit(":", 1)[0]  # `{draft key}:{prompt}`
        return LLMResult(_check(self.checks[f"{role}:{draft}"]), False, "h", "m", None)


def _request(**kw: Any) -> GenerationRequest:
    base: dict[str, Any] = {
        "key": "gen:req1",
        "skill_id": SKILL,
        "skill_name": "Adding numbers",
        "topic_name": "Arithmetic",
        "level": 3,
        "kind": "number",
        "misconceptions": {MISCONCEPTION: "adds the wrong sign"},
    }
    base.update(kw)
    return GenerationRequest(**base)


async def test_generate_check_and_store(pg_db: Db) -> None:
    """Variants checked one by one; the one the arbiter rejects too is thrown away; the kept
    ones share one `familyId`, are `checked`, `generated`; a retry stores nothing new."""
    learner_id = await create_learner(pg_db)
    roles = _Roles(
        [_draft("5"), _draft("7")],
        {
            "task_checker:gen:req1:0": "5",  # agrees
            "task_checker:gen:req1:1": "8",  # disagrees ...
            "task_arbiter:gen:req1:1": "8",  # ... and so does the arbiter
        },
    )
    stored = await generate_tasks(
        pg_db, cast(LLMGateway, roles), SETTINGS, _request(), learner_id=learner_id
    )
    assert len(stored) == 1
    generator = roles.calls[0]
    assert generator["role"] == "task_generator" and generator["hard"] is False
    assert generator["check"] is not None
    doc = await get_task(pg_db, learner_id=learner_id, task_id=stored[0])
    assert doc is not None and doc.source == "generated"
    events = await pg_db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(l)s AND type = 'task.created'",
        {"l": learner_id},
    )
    assert [e["payload"]["verification"] for e in events] == ["checked"]
    assert events[0]["payload"]["family_id"] == str(family_id("gen:req1"))
    assert events[0]["payload"]["markup"]["tests"][0]["skill_id"] == str(SKILL)

    again = await generate_tasks(
        pg_db, cast(LLMGateway, roles), SETTINGS, _request(), learner_id=learner_id
    )
    assert again == stored
    assert (
        len(
            await pg_db.fetch_all(
                "SELECT seq FROM core.events WHERE learner_id = %(l)s AND type = 'task.created'",
                {"l": learner_id},
            )
        )
        == 1
    )


@pytest.mark.parametrize(("kind", "level", "hard"), [("proof", 3, True), ("number", 6, True)])
async def test_hard_requests(pg_db: Db, kind: str, level: int, hard: bool) -> None:
    """`Т-§11.1` «для сложных — Wonder»: a proof or level 6 is a hard call of the generator."""
    learner_id = await create_learner(pg_db)
    roles = _Roles([], {})
    roles.drafts = [_draft("5")]
    roles.checks = {"task_checker:gen:req1:0": "5"}
    await generate_tasks(
        pg_db,
        cast(LLMGateway, roles),
        SETTINGS,
        _request(kind=kind, level=level),
        learner_id=learner_id,
    )
    assert roles.calls[0]["hard"] is hard


async def test_wrong_markup_is_dropped(pg_db: Db) -> None:
    """A draft with an unknown label (`Т-§5.3`) goes back to the model through `check`; still
    wrong after it — dropped without a check call."""
    learner_id = await create_learner(pg_db)
    roles = _Roles([_draft("5", skill="S9")], {})
    stored = await generate_tasks(
        pg_db, cast(LLMGateway, roles), SETTINGS, _request(), learner_id=learner_id
    )
    assert stored == []
    assert "unknown_label" in roles.calls[0]["check"](
        TaskGeneratorOutput.model_validate({"tasks": roles.drafts})
    )
    assert [c["role"] for c in roles.calls] == ["task_generator"]


def test_generator_input() -> None:
    """The nine input blocks of `task_generator@1` with labels, no ids."""
    request = _request()
    labels = LabelMap.build(SETTINGS, skills=[SKILL], misconceptions=[MISCONCEPTION])
    variables = generator_variables(request, labels)
    assert variables["target_skill"] == "S1: Adding numbers"
    assert variables["known_misconceptions"] == "M1: adds the wrong sign"
    assert variables["source_problem"] == "(none)"
    assert str(SKILL) not in "".join(variables.values())
    PromptRegistry().load("task_generator", 1).render(variables)


async def _source_task(db: Db, learner_id: UUID, family: UUID) -> UUID:
    task_id = uuid4()
    answer = AnswerSpec.model_validate({"type": "number", "value": {"latex": "5", "sympy": "5"}})
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=family,
        source="seminar",
        condition=TaskCondition(text="Compute 2 + 5 - 2", latex=None),
        markup=records.TaskMarkupRecord(
            tests=[records.TestedLevelRecord(skill_id=SKILL, level=3)],
            method_named=False,
            answer=answer,
        ),
        verification="checked",
        actor="code",
        idempotency_key=f"task:{task_id}",
    )
    return task_id


@pytest.mark.parametrize(
    ("purpose", "variant_of", "shares"),
    [
        ("retest", None, True),  # Т-§5.7: a retest variant has the family of the source
        ("practice", "K1", True),  # Т-§5.4: a task that says it is a variant of the source
        ("practice", None, False),  # no variant of the source: its own family
    ],
)
async def test_variant_of_the_source_takes_its_family(
    pg_db: Db, purpose: str, variant_of: str | None, shares: bool
) -> None:
    """D-B-186: the family of a retest variant or of a declared variant is the source's; so the
    projector counts it `same` and it never proves level 4 (`Т-§5.4`)."""
    learner_id = await create_learner(pg_db)
    source_family = uuid4()
    source = await get_task(
        pg_db,
        learner_id=learner_id,
        task_id=await _source_task(pg_db, learner_id, source_family),
    )
    assert source is not None
    draft = _draft("5")
    draft["markup"]["variantOf"] = variant_of
    roles = _Roles([draft], {"task_checker:gen:req1:0": "5"})
    stored = await generate_tasks(
        pg_db,
        cast(LLMGateway, roles),
        SETTINGS,
        _request(purpose=purpose, source=source),
        learner_id=learner_id,
    )
    assert len(stored) == 1
    generated = await pg_db.fetch_one(
        "SELECT payload FROM core.events WHERE learner_id = %(l)s AND type = 'task.created' "
        "AND payload->>'task_id' = %(t)s",
        {"l": learner_id, "t": str(stored[0])},
    )
    assert generated is not None
    expected = source_family if shares else family_id("gen:req1")
    assert generated["payload"]["family_id"] == str(expected)
    assert "K1" in roles.calls[0]["variables"]["source_problem"]
