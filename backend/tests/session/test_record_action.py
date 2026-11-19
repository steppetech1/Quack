"""ТЗ Ф4 §8.9 T-TT1 (journal part): every action kind of WP13.1 writes its event; only the kinds
of the table start a teacher turn; a repeat with the same `idempotency_key` writes nothing new
(Т-§2.2, Т-§2.3). The consent check of the session start and the SSE of the turn are separate
(WP18.4 via `require_terms`, WP13.2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter

from quack.config import Settings
from quack.db.db import Db
from quack.events import records
from quack.events.log import NewEvent, append_events
from quack.events.values import AnswerSpec, MathValue
from quack.practice.role_outputs import TaskCondition
from quack.practice.task_store import create_task
from quack.session.actions import ACTION_EVENT_TYPES, TURN_ACTION_KINDS, Action
from quack.session.record import record_action
from tests.db.fixtures import create_learner

SETTINGS = Settings()
ACTION = TypeAdapter[Action](Action)
NUMBER = AnswerSpec(type="number", value=MathValue(latex="\\frac{1}{2}", sympy="1/2"))


async def _task(db: Db, learner_id: UUID) -> UUID:
    task_id = uuid4()
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=task_id,
        source="teacher",
        condition=TaskCondition(text="1/4 + 1/4?", latex=None),
        markup=records.TaskMarkupRecord(
            tests=[records.TestedLevelRecord(skill_id=uuid4(), level=2)],
            method_named=False,
            answer=NUMBER,
        ),
        verification="unchecked",
        actor="teacher@2",
        idempotency_key=f"task:{task_id}",
    )
    return task_id


def _actions(task_id: UUID) -> dict[str, dict[str, Any]]:
    return {
        "message": {"text": "why is that?"},
        "intent": {"intent": "practice", "topic_id": str(uuid4())},
        "answer": {"task_id": str(task_id), "answer": {"kind": "number", "value": "0.5"}},
        "element_response": {"turn_seq": 1, "element_id": "e1", "response": "B"},
        "hint_request": {"task_id": str(task_id)},
        "solution_request": {"task_id": str(task_id)},
        "check_skip": {"task_id": str(task_id)},
        "flag_unclear": {"turn_seq": 1, "block_id": "b2", "quote": "the limit"},
        "confidence": {"target_seq": 1, "value": 3},
    }


async def _types(db: Db, learner_id: UUID) -> list[str]:
    rows = await db.fetch_all(
        "SELECT type FROM core.events WHERE learner_id = %(l)s ORDER BY seq", {"l": learner_id}
    )
    return [r["type"] for r in rows]


@pytest.mark.parametrize("kind", sorted(ACTION_EVENT_TYPES))
async def test_each_kind_writes_its_event(pg_db: Db, kind: str) -> None:
    learner_id = await create_learner(pg_db)
    task_id = await _task(pg_db, learner_id)
    before = await _types(pg_db, learner_id)
    action = ACTION.validate_python(
        {"kind": kind, "idempotency_key": str(uuid4()), **_actions(task_id)[kind]}
    )
    recorded = await record_action(
        pg_db, action, learner_id=learner_id, session_id=uuid4(), domain_id=None, settings=SETTINGS
    )
    written = (await _types(pg_db, learner_id))[len(before) :]
    assert written[0] == ACTION_EVENT_TYPES[kind]
    assert recorded.starts_turn is (kind in TURN_ACTION_KINDS)
    assert recorded.seq > 0


async def test_answer_writes_its_instant_verdict(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    task_id = await _task(pg_db, learner_id)
    action = ACTION.validate_python(
        {"kind": "answer", "idempotency_key": str(uuid4()), **_actions(task_id)["answer"]}
    )
    recorded = await record_action(
        pg_db, action, learner_id=learner_id, session_id=uuid4(), domain_id=None, settings=SETTINGS
    )
    assert recorded.verdict is not None and recorded.verdict.outcome == "correct"
    assert (await _types(pg_db, learner_id))[-2:] == ["answer.submitted", "verdict.recorded"]


def _turn(task_id: UUID, action_seq: int) -> dict[str, Any]:
    markup = records.TaskMarkupRecord(
        tests=[records.TestedLevelRecord(skill_id=uuid4(), level=2)],
        method_named=False,
        answer=NUMBER,
    )
    return {
        "action_seq": action_seq,
        "text": "Try this one.",
        "blocks": [{"block_id": "b1", "text": None, "element_id": "e1"}],
        "head": {
            "intent": "practice",
            "target_skill_ids": [],
            "verdict_stance": None,
            "branch_type": None,
            "approach": "none",
        },
        "markup": {
            "tasks": [
                {**markup.model_dump(mode="json"), "element_id": "e1", "task_id": str(task_id)}
            ],
            "elements": [],
            "exposures": [],
            "targets_misconception_id": None,
            "plan_update": {
                "completed_steps": [],
                "current_step": "s",
                "next_steps": [],
                "branches": [],
                "cancel_request_ids": [],
            },
            "agent_requests": [],
            "corrections_acknowledged": [],
            "decision_reason": "r",
            "deadline": None,
        },
        "role": "teacher",
        "model": "m",
        "effort": "low",
        "degraded_mode": None,
        "guard_retries": 0,
        "manifest_id": str(uuid4()),
    }


async def test_task_element_response_also_writes_the_answer(pg_db: Db) -> None:
    """WP13.1: `element.responded` (+ `answer.submitted`, if the element is a task); the task
    is the one the turn's markup put behind `element_id`."""
    learner_id = await create_learner(pg_db)
    task_id = await _task(pg_db, learner_id)
    [turn] = await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="turn.committed",
                payload=_turn(task_id, 1),
                actor="teacher@2",
                idempotency_key=f"turn:{uuid4()}",
            )
        ],
    )

    def action(element_id: str) -> Action:
        return ACTION.validate_python(
            {
                "kind": "element_response",
                "idempotency_key": str(uuid4()),
                "turn_seq": turn.seq,
                "element_id": element_id,
                "response": "0.5",
                "answer": {"kind": "number", "value": "0.5"},
            }
        )

    recorded = await record_action(
        pg_db,
        action("e1"),
        learner_id=learner_id,
        session_id=uuid4(),
        domain_id=None,
        settings=SETTINGS,
    )
    assert recorded.verdict is not None and recorded.verdict.outcome == "correct"
    assert (await _types(pg_db, learner_id))[-3:] == [
        "element.responded",
        "answer.submitted",
        "verdict.recorded",
    ]
    with pytest.raises(LookupError):  # no task behind this element in the turn
        await record_action(
            pg_db,
            action("e9"),
            learner_id=learner_id,
            session_id=uuid4(),
            domain_id=None,
            settings=SETTINGS,
        )


async def test_repeat_with_the_same_key_writes_nothing_new(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    key = str(uuid4())
    action = ACTION.validate_python({"kind": "message", "idempotency_key": key, "text": "hi"})
    session_id = uuid4()
    first = await record_action(
        pg_db,
        action,
        learner_id=learner_id,
        session_id=session_id,
        domain_id=None,
        settings=SETTINGS,
    )
    second = await record_action(
        pg_db,
        action,
        learner_id=learner_id,
        session_id=session_id,
        domain_id=None,
        settings=SETTINGS,
    )
    assert first.seq == second.seq
    assert await _types(pg_db, learner_id) == ["message.sent"]


@pytest.mark.parametrize("kind", ["answer", "hint_request", "solution_request", "check_skip"])
async def test_task_of_another_learner_writes_nothing(pg_db: Db, kind: str) -> None:
    """A kind that names a task: the task must be the learner's (D-B-147, review of #108)."""
    learner_id, other = await create_learner(pg_db), await create_learner(pg_db)
    foreign = await _task(pg_db, other)
    action = ACTION.validate_python(
        {"kind": kind, "idempotency_key": str(uuid4()), **_actions(foreign)[kind]}
    )
    with pytest.raises(LookupError):
        await record_action(
            pg_db,
            action,
            learner_id=learner_id,
            session_id=uuid4(),
            domain_id=None,
            settings=SETTINGS,
        )
    assert await _types(pg_db, learner_id) == []


# --- review of 2026-11-13: parts, element answers ---


async def _parted_task(db: Db, learner_id: UUID) -> UUID:
    """A task with the answer of the whole (1/2) and two parts: a = 1/2, b = 3."""
    task_id = uuid4()
    skill = uuid4()
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=task_id,
        source="teacher",
        condition=TaskCondition(text="a) 1/4 + 1/4? b) 1 + 2?", latex=None),
        markup=records.TaskMarkupRecord(
            tests=[records.TestedLevelRecord(skill_id=skill, level=2)],
            method_named=False,
            answer=NUMBER,
            parts=[
                records.TaskPartRecord(part="a", answer=NUMBER),
                records.TaskPartRecord(
                    part="b",
                    answer=AnswerSpec(type="number", value=MathValue(latex="3", sympy="3")),
                ),
            ],
        ),
        verification="unchecked",
        actor="teacher@2",
        idempotency_key=f"task:{task_id}",
    )
    return task_id


def _part_answer(task_id: UUID, part: str, value: str) -> Action:
    return ACTION.validate_python(
        {
            "kind": "answer",
            "idempotency_key": str(uuid4()),
            "task_id": str(task_id),
            "part": part,
            "answer": {"kind": "number", "value": value},
        }
    )


async def test_an_answer_to_a_part_is_checked_by_the_spec_of_that_part(pg_db: Db) -> None:
    """Т-§6.2: a correct answer to part b was `incorrect` — it was compared to the key of the
    whole task. Each part has its own verdict key; a part the task does not have is not a task
    of the learner."""
    learner_id = await create_learner(pg_db)
    task_id = await _parted_task(pg_db, learner_id)

    async def verdict(part: str, value: str) -> Any:
        recorded = await record_action(
            pg_db,
            _part_answer(task_id, part, value),
            learner_id=learner_id,
            session_id=uuid4(),
            domain_id=None,
            settings=SETTINGS,
        )
        assert recorded.verdict is not None
        return recorded.verdict

    b_correct = await verdict("b", "3")
    assert (b_correct.part, b_correct.outcome) == ("b", "correct")
    assert (await verdict("a", "3")).outcome == "incorrect"
    assert (await verdict("a", "0.5")).outcome == "correct"
    assert (await verdict("main", "0.5")).outcome == "correct"  # the whole task: its own key
    with pytest.raises(LookupError):
        await verdict("zzz", "3")


async def test_the_turn_finds_the_answer_of_a_task_element(pg_db: Db) -> None:
    """For `element_response` the action is the `element.responded`; the answer it made
    (`causation_seq` = the response) and its verdict belong to the turn: the verdict reaches the
    guard (`stance code`) and the task is `solved`."""
    from quack.practice.repo import session_events_of_action
    from quack.session.turn_source import read_journal

    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    task_id = await _task(pg_db, learner_id)
    [turn] = await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="turn.committed",
                payload=_turn(task_id, 1),
                actor="teacher@2",
                idempotency_key=f"turn:{uuid4()}",
                session_id=session_id,
            )
        ],
    )
    action = ACTION.validate_python(
        {
            "kind": "element_response",
            "idempotency_key": str(uuid4()),
            "turn_seq": turn.seq,
            "element_id": "e1",
            "response": "0.5",
            "answer": {"kind": "number", "value": "0.5"},
        }
    )
    recorded = await record_action(
        pg_db,
        action,
        learner_id=learner_id,
        session_id=session_id,
        domain_id=None,
        settings=SETTINGS,
    )

    events = await session_events_of_action(
        pg_db, learner_id=learner_id, session_id=session_id, action_seq=recorded.seq
    )
    assert [e.type for e in events][-3:] == [
        "element.responded",
        "answer.submitted",
        "verdict.recorded",
    ]
    journal = read_journal(events, recorded.seq)
    assert journal.answered is not None and journal.answered.kind == "answer"
    assert journal.answered.task_id == task_id and journal.answered.solved
    assert journal.answered_verdict is not None and journal.answered_verdict.outcome == "correct"
    assert journal.answer_seq == recorded.seq + 1
