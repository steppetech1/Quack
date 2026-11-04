"""ТЗ Ф4 §8.7 T-CH1 (transaction part): the instant verdict is written in the transaction of
`answer.submitted` (Т-§6.1, WP11.2, WP11.4); help comes from the task's events in the
session."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import AnswerSubmitted, HintShown
from quack.events.log import NewEvent, append_events
from quack.events.values import LearnerAnswer as CoreAnswer
from quack.practice.answer_spec import AnswerSpec
from quack.practice.answers import DEFAULT_PART, record_answer
from quack.practice.check import NotYet
from tests.db.fixtures import create_learner

SETTINGS = Settings()
CHOICE = AnswerSpec.model_validate(
    {
        "type": "choice",
        "options": [
            {"option_id": "A", "text": "converges", "correct": True},
            {"option_id": "B", "text": "diverges", "correct": False},
        ],
    }
)


def _payload(task_id: UUID, value: str, kind: str = "choice") -> AnswerSubmitted:
    return AnswerSubmitted(
        task_id=task_id,
        answer=CoreAnswer.model_validate({"kind": kind, "value": value}),
        source="chat",
    )


async def _events(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT seq, type, idempotency_key, causation_seq, payload FROM core.events "
        "WHERE learner_id = %(l)s ORDER BY seq",
        {"l": learner_id},
    )


async def test_instant_verdict_in_answer_transaction(pg_db: Db) -> None:
    learner_id, session_id, task_id = await create_learner(pg_db), uuid4(), uuid4()
    recorded = await record_answer(
        pg_db,
        learner_id=learner_id,
        session_id=session_id,
        domain_id=None,
        idempotency_key=str(uuid4()),
        payload=_payload(task_id, "A"),
        spec=CHOICE,
        settings=SETTINGS,
    )
    assert recorded.verdict is not None and recorded.verdict.outcome == "correct"
    events = await _events(pg_db, learner_id)
    assert [e["type"] for e in events] == ["answer.submitted", "verdict.recorded"]
    answer, verdict = events
    assert verdict["seq"] == answer["seq"] + 1 == recorded.answer_seq + 1
    assert verdict["idempotency_key"] == f"verdict:{answer['seq']}:{DEFAULT_PART}"
    assert verdict["causation_seq"] == answer["seq"]
    assert verdict["payload"]["verification"] == "code"


async def test_answer_and_verdict_roll_back_together(pg_db: Db) -> None:
    """A failure in the caller's transaction after the write leaves neither event."""
    learner_id = await create_learner(pg_db)
    with pytest.raises(RuntimeError):
        async with pg_db.transaction():
            await record_answer(
                pg_db,
                learner_id=learner_id,
                session_id=uuid4(),
                domain_id=None,
                idempotency_key=str(uuid4()),
                payload=_payload(uuid4(), "B"),
                spec=CHOICE,
                settings=SETTINGS,
            )
            raise RuntimeError("turn failed before commit")
    assert await _events(pg_db, learner_id) == []


async def test_help_from_session_events_and_repeat(pg_db: Db) -> None:
    """WP11.2: the highest hint step of the task in the session goes into the verdict; a repeat
    with the same key writes nothing new (law 5)."""
    learner_id, session_id, task_id = await create_learner(pg_db), uuid4(), uuid4()
    hints = [
        NewEvent(
            type="hint.shown",
            payload=HintShown(task_id=task_id, step=step, source="teacher"),
            actor="teacher@2",
            idempotency_key=f"hint:{step}",
            session_id=session_id,
        )
        for step in (1, 2)
    ]
    await append_events(pg_db, learner_id, hints)
    key = str(uuid4())
    kwargs: dict[str, Any] = {
        "learner_id": learner_id,
        "session_id": session_id,
        "domain_id": None,
        "idempotency_key": key,
        "payload": _payload(task_id, "B"),
        "spec": CHOICE,
        "settings": SETTINGS,
    }
    first = await record_answer(pg_db, **kwargs)
    assert first.verdict is not None
    assert (first.verdict.outcome, first.verdict.help_step) == ("incorrect", 2)
    again = await record_answer(pg_db, **kwargs)
    assert again.answer_seq == first.answer_seq
    assert len(await _events(pg_db, learner_id)) == 4  # two hints, answer, verdict


async def test_fast_and_slow_types_write_only_the_answer(pg_db: Db) -> None:
    """Т-§6.1: a number written as an expression waits for compute or insurance; a proof waits
    for `check.model` — only `answer.submitted` is written."""
    learner_id = await create_learner(pg_db)
    number = AnswerSpec.model_validate(
        {"type": "number", "value": {"latex": "\\frac{\\sqrt2}{2}", "sympy": "sqrt(2)/2"}}
    )
    fast = await record_answer(
        pg_db,
        learner_id=learner_id,
        session_id=uuid4(),
        domain_id=None,
        idempotency_key=str(uuid4()),
        payload=_payload(uuid4(), "sqrt(2)/2", "number"),
        spec=number,
        settings=SETTINGS,
    )
    assert fast.verdict is None and isinstance(fast.result, NotYet)
    proof = await record_answer(
        pg_db,
        learner_id=learner_id,
        session_id=uuid4(),
        domain_id=None,
        idempotency_key=str(uuid4()),
        payload=_payload(uuid4(), "Assume ...", "proof"),
        spec=AnswerSpec(type="proof", key_ideas=["induction step"]),
        settings=SETTINGS,
    )
    assert proof.verdict is None and proof.result is None
    assert [e["type"] for e in await _events(pg_db, learner_id)] == [
        "answer.submitted",
        "answer.submitted",
    ]
