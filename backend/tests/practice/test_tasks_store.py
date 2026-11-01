"""ТЗ Ф4 §8.8 T-TK1: `content.tasks` and `task.created` in one transaction (Т-§5.1, WP12.1)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from quack.db.db import Db
from quack.events.records import TaskMarkupRecord, TestedLevelRecord
from quack.events.values import AnswerSpec, MathValue
from quack.practice.role_outputs import TaskCondition
from quack.practice.task_store import create_task, get_task
from tests.db.fixtures import create_learner

MARKUP = TaskMarkupRecord(
    tests=[TestedLevelRecord(skill_id=uuid4(), level=3)],
    method_named=False,
    answer=AnswerSpec(type="number", value=MathValue(latex="\\frac{1}{3}", sympy="1/3")),
)
CONDITION = TaskCondition(text="Sum of 1/2^n for n >= 2?", latex="\\sum_{n\\ge 2} 2^{-n}")


async def _create(db: Db, learner_id: UUID, task_id: UUID, key: str) -> int:
    return await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=uuid4() if key != "same" else task_id,
        source="generated",
        condition=CONDITION,
        markup=MARKUP,
        verification="unchecked",
        actor="agent:task_generation",
        idempotency_key=key,
    )


async def _events(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT seq, type, payload FROM core.events WHERE learner_id = %(l)s ORDER BY seq",
        {"l": learner_id},
    )


async def test_task_doc_and_event_atomic(pg_db: Db) -> None:
    """T-TK1: the document and `task.created` together; the document points at the event."""
    learner_id = await create_learner(pg_db)
    task_id = uuid4()
    seq = await _create(pg_db, learner_id, task_id, "gen:1")

    doc = await get_task(pg_db, learner_id=learner_id, task_id=task_id)
    assert doc is not None
    assert doc.created_seq == seq
    assert doc.answer_spec == MARKUP.answer and doc.condition == CONDITION
    events = await _events(pg_db, learner_id)
    assert [(e["seq"], e["type"]) for e in events] == [(seq, "task.created")]
    assert events[0]["payload"]["task_id"] == str(task_id)


async def test_task_failure_writes_neither(pg_db: Db) -> None:
    """T-TK1: a failure inside the transaction leaves neither the document nor the event."""
    learner_id = await create_learner(pg_db)
    task_id = uuid4()
    await _create(pg_db, learner_id, task_id, "gen:1")
    other = await create_learner(pg_db)
    # Same task id for another learner: the document insert fails on the primary key.
    with pytest.raises(psycopg.errors.UniqueViolation):
        await _create(pg_db, other, task_id, "gen:2")
    assert await _events(pg_db, other) == []
    assert await get_task(pg_db, learner_id=other, task_id=task_id) is None
    seq_row = await pg_db.fetch_one(
        "SELECT seq FROM core.learners WHERE learner_id = %(l)s", {"l": other}
    )
    assert seq_row == {"seq": 0}


async def test_task_repeat_is_idempotent(pg_db: Db) -> None:
    """Law 5: the same key and payload — the same seq, no second event or document."""
    learner_id = await create_learner(pg_db)
    task_id = uuid4()
    first = await _create(pg_db, learner_id, task_id, "same")
    again = await _create(pg_db, learner_id, task_id, "same")
    assert again == first
    assert len(await _events(pg_db, learner_id)) == 1


async def test_task_isolated_by_learner(pg_db: Db) -> None:
    """Т-§3.5: another learner does not read the document."""
    learner_id = await create_learner(pg_db)
    task_id = uuid4()
    await _create(pg_db, learner_id, task_id, "gen:1")
    stranger = await create_learner(pg_db)
    assert await get_task(pg_db, learner_id=stranger, task_id=task_id) is None
