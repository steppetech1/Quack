"""`ops metrics` (ТЗ Ф4 WP16.7; Т-§18.2, Т-§18.3; D-B-195): the metrics the journal and
`ops.llm_usage` give without labelling; an empty input is «not measured», never a zero."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.agents.metrics import metrics
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import (
    AnswerSubmitted,
    DisputeFiled,
    MessageSent,
    ObservationSecondOpinion,
    TaskCreated,
)
from quack.events.log import NewEvent, append_events
from quack.events.records import TaskMarkupRecord, TestedLevelRecord
from quack.events.values import AnswerSpec, CheckFailed, LearnerAnswer, MathValue, Verdict
from quack.graph.read import GraphReader, StateAt
from tests.db.fixtures import create_learner
from tests.support import graph_read as fx

SETTINGS = Settings()


class _RecallReader(fx.FixedGraphReader):
    """The fixed memory with a known probability to recall (the Brier rule needs `R`)."""

    async def state_at(self, learner_id: UUID, seq_n: int, skill_ids: Any) -> StateAt:
        state = await super().state_at(learner_id, seq_n, skill_ids)
        skills = [s.model_copy(update={"recall_probability": 0.5}) for s in state.skills]
        return state.model_copy(update={"skills": skills})


def _event(type_: str, payload: Any, key: str, session_id: UUID) -> NewEvent:
    return NewEvent(
        type=type_,
        payload=payload,
        actor="learner" if type_ in {"message.sent", "answer.submitted"} else "system",
        idempotency_key=key,
        session_id=session_id,
        domain_id=fx.DOMAIN,
    )


def _verdict(answer_seq: int, part: str, outcome: str, *, model: bool = False) -> Verdict:
    return Verdict(
        answer_seq=answer_seq,
        part=part,
        outcome=outcome,  # type: ignore[arg-type]  # a literal of the model
        method="model" if model else "exact",
        verification="model" if model else "code",
        numeric_only=model,
    )


async def test_metrics_of_the_journal_and_the_usage(pg_db: Db) -> None:
    """One learner, one session: shares of verdicts, disputes per thousand, second opinions,
    cost per learner-hour; the Brier score needs a reader (without it — not measured); metrics
    with no events are not measured."""
    learner_id = await create_learner(pg_db)
    session_id, task_id = uuid4(), uuid4()
    markup = TaskMarkupRecord(
        tests=[TestedLevelRecord(skill_id=fx.SKILL_PROVEN, level=3)],
        method_named=False,
        answer=AnswerSpec(type="number", value=MathValue(latex="1", sympy="1")),
    )
    first = await append_events(
        pg_db,
        learner_id,
        [
            _event("message.sent", MessageSent(text="hi"), "m", session_id),
            _event(
                "task.created",
                TaskCreated(
                    task_id=task_id,
                    family_id=uuid4(),
                    source="teacher",
                    markup=markup,
                    verification="checked",
                ),
                "t",
                session_id,
            ),
            _event(
                "answer.submitted",
                AnswerSubmitted(
                    task_id=task_id,
                    answer=LearnerAnswer(kind="number", value="1"),
                    source="chat",
                ),
                "a",
                session_id,
            ),
        ],
    )
    answer_seq = first[-1].seq
    await append_events(
        pg_db,
        learner_id,
        [
            _event("verdict.recorded", _verdict(answer_seq, "a", "correct"), "v1", session_id),
            _event(
                "verdict.recorded",
                _verdict(answer_seq, "b", "unclear", model=True),
                "v2",
                session_id,
            ),
            _event(
                "dispute.filed",
                DisputeFiled(target_kind="verdict", target_id="v2"),
                "d",
                session_id,
            ),
            _event(
                "check.failed",
                CheckFailed(answer_seq=answer_seq, part="c", reason="timeout"),
                "f",
                session_id,
            ),
            _event(
                "observation.second_opinion",
                ObservationSecondOpinion(target_seq=answer_seq, agreed=False),
                "o",
                session_id,
            ),
        ],
    )
    await pg_db.execute(
        "INSERT INTO ops.llm_usage (learner_id, role, model, status, cost_usd) "
        "VALUES (%(l)s, 'teacher', 'm', 'ok', 0.25)",
        {"l": learner_id},
    )
    since = datetime.now(UTC) - timedelta(hours=1)

    plain = (await metrics(pg_db, since, settings=SETTINGS))["metrics"]
    assert plain["unclear_share"]["value"] == pytest.approx(0.5)
    assert plain["numeric_only_share"]["value"] == pytest.approx(0.5)
    assert plain["disputes_per_1000_verdicts"]["value"] == pytest.approx(500)
    assert plain["check_failed_share"]["value"] == pytest.approx(1 / 3)
    assert plain["canvas_marks_removed_share"]["value"] == pytest.approx(1.0)
    assert plain["cost_per_learner_hour_usd"]["value"] == pytest.approx(0.25)
    for absent in ("corrective_branch_share", "independent_success_later", "next_attempt_brier"):
        assert plain[absent]["status"] == "not_measured" and plain[absent]["value"] is None

    def reader(_learner: UUID) -> GraphReader:
        return _RecallReader()

    with_reader = (await metrics(pg_db, since, settings=SETTINGS, reader=reader))["metrics"]
    # p_proven × R = 0.9 × 0.5 against a correct attempt.
    assert with_reader["next_attempt_brier"]["value"] == pytest.approx((1 - 0.45) ** 2)
