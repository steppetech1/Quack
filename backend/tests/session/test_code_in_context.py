"""`teacher@4`, Т-§6.10: the teacher reads the learner's code and the check by its tests; a
hidden test shows its status only; a ready code task appears with its label."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from quack.config import Settings
from quack.events.event_types import AnswerSubmitted
from quack.events.log import StoredEvent
from quack.events.values import CodeTestOutcome, LearnerAnswer, Verdict
from quack.llm.labels import LabelMap
from quack.practice.code_check import CodeRunErrorView, CodeTestView, verdict_tests
from quack.session.conversation import render_conversation
from quack.session.turn_source import _code_task_note

SETTINGS = Settings()
LEARNER, SESSION, TASK = uuid4(), uuid4(), uuid4()
SECRET = "777123"


def _event(seq: int, type_: str, payload: Any) -> StoredEvent:
    return StoredEvent.model_validate(
        {
            "id": seq,
            "seq": seq,
            "learner_id": LEARNER,
            "domain_id": None,
            "causation_seq": None,
            "correlation_id": None,
            "type": type_,
            "payload": payload.model_dump(mode="json"),
            "actor": "learner",
            "idempotency_key": f"k:{seq}",
            "schema_version": 1,
            "payload_hash": "h",
            "session_id": SESSION,
            "occurred_at": "2026-11-25T10:00:00Z",
            "recorded_at": "2026-11-25T10:00:00Z",
            "source_refs": [],
        }
    )


def test_verdict_tests_keep_open_details_and_hide_hidden_ones() -> None:
    views = [
        CodeTestView(number=1, hidden=False, status="passed", args=[2], expected=4, actual=4),
        CodeTestView(number=2, hidden=False, status="wrong", args=[3], expected=9, actual=6),
        CodeTestView(
            number=3,
            hidden=True,
            status="error",
            args=[int(SECRET)],
            error=CodeRunErrorView(type="ValueError", message=SECRET),
        ),
    ]
    tests = verdict_tests(views, SETTINGS.code_test_detail_chars)
    assert tests[0].detail is None
    assert tests[1].detail == "args [3], expected 9, got 6"
    assert tests[2] == CodeTestOutcome(number=3, hidden=True, status="error", detail=None)
    assert SECRET not in str([t.model_dump() for t in tests])


def test_conversation_shows_code_and_test_results() -> None:
    answer = AnswerSubmitted(
        task_id=TASK,
        answer=LearnerAnswer(kind="code", value="def f(x):\n    return x"),
        source="editor",
    )
    verdict = Verdict(
        answer_seq=10,
        part="main",
        outcome="partial",
        method="tests",
        verification="code",
        tests=[
            CodeTestOutcome(
                number=1, hidden=False, status="wrong", detail="args [3], expected 9, got 3"
            ),
            CodeTestOutcome(number=3, hidden=True, status="passed"),
        ],
    )
    labels = LabelMap.build(SETTINGS, tasks=[TASK])
    text = render_conversation(
        [_event(10, "answer.submitted", answer), _event(11, "verdict.recorded", verdict)],
        labels,
        SETTINGS,
    )
    assert "def f(x):\n    return x" in text
    assert "example 1: wrong — args [3], expected 9, got 3" in text
    assert "hidden test 3: passed" in text


def test_long_code_is_cut() -> None:
    settings = SETTINGS.model_copy(update={"code_in_context_chars": 20})
    answer = AnswerSubmitted(
        task_id=TASK, answer=LearnerAnswer(kind="code", value="x = 1\n" * 100), source="editor"
    )
    text = render_conversation(
        [_event(10, "answer.submitted", answer)], LabelMap.build(settings, tasks=[TASK]), settings
    )
    assert "… (cut)" in text


def test_ready_code_tasks_are_named_with_labels() -> None:
    other = uuid4()
    labels = LabelMap.build(SETTINGS, tasks=[TASK, other])
    note = _code_task_note([TASK, other], {TASK: "python"}, labels)
    label = labels.label("task", TASK)
    assert note == f": code tasks {label} (python)"
    assert _code_task_note([other], {}, labels) == ""
