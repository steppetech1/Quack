"""ТЗ Ф4 §8.7 T-CH3, T-CH6 (job body part): the model check of a slow answer or of `check.failed`
(WP11.6; Т-§6.8, Т-§7.7, Т-§22.7). The job registration (`check.model`, dispatcher of
Phase 4, WP7.1) is stream A's runtime at G2; here — what the body calls, sends and writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import AnswerSubmitted
from quack.events.keys import verdict_key
from quack.events.values import LearnerAnswer as CoreAnswer
from quack.graph.read.models import MisconceptionFact
from quack.llm.gateway import LLMGateway, LLMResult
from quack.practice.answer_spec import AnswerSpec, AnswerType
from quack.practice.check import HelpUsed, SecondOpinion, Verdict
from quack.practice.model_check import (
    KnownMisconception,
    LevelState,
    check_with_model,
    known_misconceptions,
    record_verdict,
    verdict_inputs,
    verdict_role,
)
from quack.practice.role_outputs import ModelVerdictOutput, SolutionSpace, TaskCondition
from tests.db.fixtures import create_learner

SETTINGS = Settings()
SURE = (1 + SETTINGS.verdict_min_confidence) / 2
QUIET = LevelState(proven_by_direct=False, last_t1_failed=False)
PROVEN = LevelState(proven_by_direct=True, last_t1_failed=False)
M_ID = uuid4()

CONDITION = TaskCondition(text="Prove that the sum of two even numbers is even.", latex=None)
PROOF = AnswerSpec.model_validate({"type": "proof", "key_ideas": ["write as 2a + 2b"]})
FREE = AnswerSpec.model_validate(
    {"type": "free_text", "key_ideas": ["names the derivative", "explains the sign"]}
)
SPACE = SolutionSpace.model_validate(
    {
        "key_steps": [
            {"statement": "Let the numbers be 2a and 2b", "expression": None},
            {"statement": "Their sum is 2(a + b)", "expression": None},
            {"statement": "2(a + b) is even", "expression": None},
        ],
        "wrong_branches": [
            {"sign": "uses one example", "gap_type": "misconception", "misconception": None},
            {"sign": "assumes odd", "gap_type": "confusion", "misconception": None},
        ],
        "hints": ["h1", "h2", "h3"],
    }
)


def _output(outcome: str = "incorrect", **extra: Any) -> ModelVerdictOutput:
    fields: dict[str, Any] = {
        "items": [{"item": "R1", "status": "found", "quote": "2a + 2b"}],
        "outcome": outcome,
        "confidence": SURE,
        "misconception": None,
        "misconception_hypothesis": None,
    }
    return ModelVerdictOutput.model_validate({**fields, **extra})


@dataclass
class FakeGateway:
    """`LLMGateway.generate_structured` double: answers by role, records the calls."""

    answers: dict[str, list[ModelVerdictOutput]]
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
        self.calls.append(
            {
                "role": role,
                "prompt_version": prompt_version,
                "variables": variables,
                "idempotency_key": idempotency_key,
                "check": kwargs.get("check"),
            }
        )
        value = self.answers[role].pop(0)
        return LLMResult(value=value, from_cache=False, prompt_hash="h", model="m", usage=None)


def _answer(value: str = "2a + 2b = 2(a + b)", **extra: Any) -> AnswerSubmitted:
    return AnswerSubmitted.model_validate(
        {
            "task_id": uuid4(),
            "answer": CoreAnswer.model_validate({"kind": "proof", "value": value}),
            "source": "chat",
            **extra,
        }
    )


async def _check(
    gateway: FakeGateway,
    spec: AnswerSpec = PROOF,
    *,
    levels: list[LevelState] | None = None,
    misconceptions: list[KnownMisconception] | None = None,
    answer: AnswerSubmitted | None = None,
    failed: bool = False,
) -> Verdict:
    return await check_with_model(
        cast(LLMGateway, gateway),
        SETTINGS,
        learner_id=uuid4(),
        session_id=uuid4(),
        condition=CONDITION,
        spec=spec,
        solution_space=None,
        answer=answer or _answer(),
        answer_seq=12,
        part="main",
        failed=failed,
        misconceptions=misconceptions or [],
        levels=levels or [QUIET],
        help_used=HelpUsed(hint_step=1),
    )


@pytest.mark.parametrize(
    ("answer_type", "failed", "role"),
    [
        ("proof", False, "verdict_proof"),
        ("free_text", False, "verdict_free"),
        ("drawing", False, "verdict_free"),
        ("expression", True, "verdict_free"),  # check.failed → verdict_free (Т-§22.7)
        ("quantity", True, "verdict_free"),
    ],
)
def test_role_by_answer_type(answer_type: AnswerType, failed: bool, role: str) -> None:
    assert verdict_role(answer_type, failed=failed) == role


def test_inputs_rubric_from_solution_space_else_key_ideas() -> None:
    labels_by_space = verdict_inputs(
        CONDITION, PROOF, SPACE, _answer(), [KnownMisconception(M_ID, "sum of evens is odd")]
    )
    assert labels_by_space["rubric"].splitlines() == [
        "R1. Let the numbers be 2a and 2b",
        "R2. Their sum is 2(a + b)",
        "R3. 2(a + b) is even",
    ]
    assert labels_by_space["known_misconceptions"] == "M1. sum of evens is odd"
    assert labels_by_space["problem"] == CONDITION.text
    assert labels_by_space["learner_answer"] == "2a + 2b = 2(a + b)"
    by_ideas = verdict_inputs(CONDITION, FREE, None, _answer(), [])
    assert by_ideas["rubric"].splitlines() == [
        "R1. names the derivative",
        "R2. explains the sign",
    ]
    assert set(by_ideas) == {
        "problem",
        "rubric",
        "answer_spec",
        "known_misconceptions",
        "learner_answer",
    }


async def test_model_verdict_with_label_resolved_and_help() -> None:
    gateway = FakeGateway({"verdict_proof": [_output(misconception="M1")]})
    verdict = await _check(gateway, misconceptions=[KnownMisconception(M_ID, "odd sum")])
    assert verdict.verification == "model" and verdict.method == "model"
    assert verdict.misconception_id == M_ID
    assert verdict.help_step == 1
    [call] = gateway.calls
    assert call["idempotency_key"] == "check.model:12:main"
    assert call["prompt_version"] == int(SETTINGS.llm_roles["verdict_proof"].prompt.split("@")[1])  # type: ignore[union-attr]


async def test_unknown_label_goes_back_to_the_model() -> None:
    """Т-§4.5: the gateway's `check` returns the unknown label to the model once."""
    gateway = FakeGateway({"verdict_proof": [_output()]})
    await _check(gateway, misconceptions=[KnownMisconception(M_ID, "odd sum")])
    label_check = gateway.calls[0]["check"]
    assert label_check(_output(misconception="M1")) is None
    assert "M7" in label_check(_output(misconception="M7"))
    assert "R9" in label_check(_output(items=[{"item": "R9", "status": "missing", "quote": None}]))


async def test_second_opinion_on_a_proven_level() -> None:
    gateway = FakeGateway(
        {"verdict_proof": [_output("incorrect")], "second_opinion": [_output("correct")]}
    )
    verdict = await _check(gateway, levels=[PROVEN])
    assert verdict.second_opinion == SecondOpinion(requested=True, agreed=False)
    assert verdict.outcome == "incorrect"
    assert [c["role"] for c in gateway.calls] == ["verdict_proof", "second_opinion"]
    assert gateway.calls[1]["idempotency_key"] == "second_opinion:12:main"


async def test_no_second_opinion_without_doubt() -> None:
    gateway = FakeGateway({"verdict_proof": [_output("incorrect")]})
    verdict = await _check(gateway, levels=[QUIET])
    assert verdict.second_opinion is None
    assert len(gateway.calls) == 1


async def test_recognized_answer_lowers_confidence() -> None:
    """WP11.7: the same path for a handwritten answer (D-B-127)."""
    unsure = SETTINGS.recognition_min_confidence / 2
    gateway = FakeGateway({"verdict_proof": [_output("incorrect")]})
    verdict = await _check(gateway, answer=_answer(recognition_confidence=unsure))
    assert verdict.needs_reading_confirmation
    assert verdict.confidence == pytest.approx(SURE * unsure)


async def test_record_verdict_once_by_key(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    verdict = Verdict(
        answer_seq=12, part="main", outcome="partial", method="model", verification="model"
    )
    for _ in range(2):
        await record_verdict(pg_db, verdict, learner_id=learner_id, session_id=None, domain_id=None)
    rows = await pg_db.fetch_all(
        "SELECT type, idempotency_key, causation_seq FROM core.events "
        "WHERE learner_id = %(l)s ORDER BY seq",
        {"l": learner_id},
    )
    assert rows == [
        {
            "type": "verdict.recorded",
            "idempotency_key": verdict_key(12, "main"),
            "causation_seq": 12,
        }
    ]


def test_known_misconceptions_from_the_slice() -> None:
    """D-B-145 (A: `MisconceptionFact.statement`, #117): the misconceptions of the tested
    skills, with their statements, in the order of the slice; others are not shown."""
    tested, other = uuid4(), uuid4()
    facts = [
        MisconceptionFact(
            misconception_id=M_ID, skill_id=tested, status="confirmed", statement="odd sum"
        ),
        MisconceptionFact(
            misconception_id=uuid4(), skill_id=other, status="suspected", statement="elsewhere"
        ),
    ]

    assert known_misconceptions(facts, skill_ids=[tested]) == [KnownMisconception(M_ID, "odd sum")]
