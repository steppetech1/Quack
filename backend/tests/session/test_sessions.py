"""Tests for `SessionView.unanswered_seq` and `SessionDetail.unanswered_seq`."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx

from quack.config import Settings
from quack.db import Db
from quack.events import records
from quack.events.event_types import ConfidenceReported, MessageSent
from quack.events.log import NewEvent, append_events
from quack.events.values import AnswerSpec, MathValue
from quack.practice.role_outputs import TaskCondition
from quack.practice.task_store import create_task
from quack.session.actions import AnswerAction, ElementResponseAction, LearnerAnswer, MessageAction
from quack.session.record import record_action
from quack.session.sessions import (
    SessionIntent,
    StartSession,
    active_session,
    get_session,
    list_sessions,
    start_session,
)
from tests.events.samples import sample_payload
from tests.session.helpers import Learner

pytest_plugins = ["tests.session.fixtures"]

SETTINGS = Settings()


async def _start(db: Db, learner: Learner, intent: SessionIntent = "free") -> UUID:
    started = await start_session(
        db,
        learner_id=learner.learner_id,
        body=StartSession(domain_id=learner.domain_id, intent=intent, idempotency_key=uuid4()),
    )
    return started.session_id


def _turn_payload(action_seq: int) -> dict[str, Any]:
    payload = sample_payload("turn.committed")
    payload["action_seq"] = action_seq
    return payload


async def test_unanswered_seq_is_none_for_fresh_free_and_learn_sessions(
    client: httpx.AsyncClient, pg_db: Db, learner: Learner
) -> None:
    free_id = await _start(pg_db, learner, intent="free")
    learn_id = await _start(pg_db, learner, intent="learn")

    for sid in (free_id, learn_id):
        view = await get_session(pg_db, learner_id=learner.learner_id, session_id=sid)
        assert view.unanswered_seq is None
        detail = (await client.get(f"/v1/sessions/{sid}", cookies=learner.cookies)).json()
        assert detail["unanswered_seq"] is None

    listed = await list_sessions(pg_db, learner_id=learner.learner_id)
    assert all(s.unanswered_seq is None for s in listed)
    active = await active_session(pg_db, learner_id=learner.learner_id)
    assert active is not None and active.unanswered_seq is None


async def test_unanswered_seq_tracks_message_and_clears_after_turn_committed(
    client: httpx.AsyncClient, pg_db: Db, learner: Learner
) -> None:
    session_id = await _start(pg_db, learner, intent="free")
    recorded = await record_action(
        pg_db,
        MessageAction(kind="message", idempotency_key=uuid4(), text="Explain integrals"),
        learner_id=learner.learner_id,
        session_id=session_id,
        domain_id=learner.domain_id,
        settings=SETTINGS,
    )

    view = await get_session(pg_db, learner_id=learner.learner_id, session_id=session_id)
    assert view.unanswered_seq == recorded.seq
    detail = (await client.get(f"/v1/sessions/{session_id}", cookies=learner.cookies)).json()
    assert detail["unanswered_seq"] == recorded.seq

    # Commit a turn answering that message -> `unanswered_seq` clears to `None`.
    await append_events(
        pg_db,
        learner.learner_id,
        [
            NewEvent(
                type="turn.committed",
                payload=_turn_payload(recorded.seq),
                actor="teacher@3",
                idempotency_key=f"turn:{recorded.seq}",
                session_id=session_id,
                domain_id=learner.domain_id,
                causation_seq=recorded.seq,
            )
        ],
    )
    after = await get_session(pg_db, learner_id=learner.learner_id, session_id=session_id)
    assert after.unanswered_seq is None

    # A non-turn event (`confidence.reported`) does not count as unanswered input.
    await append_events(
        pg_db,
        learner.learner_id,
        [
            NewEvent(
                type="confidence.reported",
                payload=ConfidenceReported(target_seq=recorded.seq, value=3),
                actor="learner",
                idempotency_key=f"conf:{uuid4()}",
                session_id=session_id,
                domain_id=learner.domain_id,
            )
        ],
    )
    after_conf = await get_session(pg_db, learner_id=learner.learner_id, session_id=session_id)
    assert after_conf.unanswered_seq is None

    # A second unanswered message sets `unanswered_seq` to the new message's seq.
    [second] = await append_events(
        pg_db,
        learner.learner_id,
        [
            NewEvent(
                type="message.sent",
                payload=MessageSent(text="Another question"),
                actor="learner",
                idempotency_key=f"msg:{uuid4()}",
                session_id=session_id,
                domain_id=learner.domain_id,
            )
        ],
    )
    again = await get_session(pg_db, learner_id=learner.learner_id, session_id=session_id)
    assert again.unanswered_seq == second.seq


async def test_unanswered_seq_tracks_element_responded_over_child_answer(
    client: httpx.AsyncClient, pg_db: Db, learner: Learner
) -> None:
    """When `element_response` on a task writes `element.responded` plus child `answer.submitted`
    and `verdict.recorded`, `unanswered_seq` points to `element.responded` until
    `turn.committed`."""
    session_id = await _start(pg_db, learner, intent="practice")
    task_id = uuid4()
    spec = AnswerSpec(type="number", value=MathValue(latex="4", sympy="4"))
    markup = records.TaskMarkupRecord(
        tests=[records.TestedLevelRecord(skill_id=uuid4(), level=2)],
        method_named=False,
        answer=spec,
    )
    await create_task(
        pg_db,
        learner_id=learner.learner_id,
        task_id=task_id,
        family_id=task_id,
        source="teacher",
        condition=TaskCondition(text="2+2?", latex=None),
        markup=markup,
        verification="unchecked",
        actor="teacher@3",
        idempotency_key=f"task:{task_id}",
    )
    turn_payload = _turn_payload(1)
    turn_payload["markup"]["tasks"] = [
        {**markup.model_dump(mode="json"), "element_id": "e1", "task_id": str(task_id)}
    ]
    [turn] = await append_events(
        pg_db,
        learner.learner_id,
        [
            NewEvent(
                type="turn.committed",
                payload=turn_payload,
                actor="teacher@3",
                idempotency_key=f"turn:{uuid4()}",
                session_id=session_id,
                domain_id=learner.domain_id,
            )
        ],
    )

    resp = await record_action(
        pg_db,
        ElementResponseAction(
            kind="element_response",
            idempotency_key=uuid4(),
            turn_seq=turn.seq,
            element_id="e1",
            response="4",
        ),
        learner_id=learner.learner_id,
        session_id=session_id,
        domain_id=learner.domain_id,
        settings=SETTINGS,
    )

    detail = (await client.get(f"/v1/sessions/{session_id}", cookies=learner.cookies)).json()
    assert detail["unanswered_seq"] == resp.seq

    # Standalone `answer.submitted` (not caused by `element.responded`) is also tracked.
    ans = await record_action(
        pg_db,
        AnswerAction(
            kind="answer",
            idempotency_key=uuid4(),
            task_id=task_id,
            answer=LearnerAnswer(kind="number", value="4"),
        ),
        learner_id=learner.learner_id,
        session_id=session_id,
        domain_id=learner.domain_id,
        settings=SETTINGS,
    )
    after_ans = await get_session(pg_db, learner_id=learner.learner_id, session_id=session_id)
    assert after_ans.unanswered_seq == ans.seq

    await append_events(
        pg_db,
        learner.learner_id,
        [
            NewEvent(
                type="turn.committed",
                payload=_turn_payload(ans.seq),
                actor="teacher@3",
                idempotency_key=f"turn:{ans.seq}",
                session_id=session_id,
                domain_id=learner.domain_id,
                causation_seq=ans.seq,
            )
        ],
    )
    settled = await get_session(pg_db, learner_id=learner.learner_id, session_id=session_id)
    assert settled.unanswered_seq is None
