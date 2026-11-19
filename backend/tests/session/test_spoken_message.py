"""A spoken message is recorded once: `voice.transcribed` holds the words, the `message.sent`
that starts the turn points at it (`voice_seq`) and carries none. The teacher context, the
extractor and the journal read the words from the voice event only."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from quack.agents.extract_episode import event_lines, source_text
from quack.config import Settings
from quack.events.event_types import MessageSent, VoiceTranscribed
from quack.events.log import StoredEvent
from quack.llm.labels import LabelMap
from quack.session.actions import Action, MessageAction
from quack.session.conversation import render_conversation
from quack.session.turn_source import read_journal

SETTINGS = Settings()
LEARNER, SESSION = uuid4(), uuid4()
WORDS = "is the series geometric?"


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
            "occurred_at": "2026-11-03T10:00:00Z",
            "recorded_at": "2026-11-03T10:00:00Z",
            "source_refs": [],
        }
    )


def _spoken() -> list[StoredEvent]:
    voice = VoiceTranscribed(text=WORDS, started_at_ms=0, ended_at_ms=4000)
    return [
        _event(5, "voice.transcribed", voice),
        _event(6, "message.sent", MessageSent(text="", voice_seq=5)),
    ]


def test_the_teacher_reads_a_spoken_message_once() -> None:
    text = render_conversation(_spoken(), LabelMap.build(SETTINGS), SETTINGS)
    assert [line for line in text.splitlines() if "earner" in line] == [f"Learner said: {WORDS}"]
    assert text.count(WORDS) == 1


def test_the_extractor_reads_a_spoken_message_once() -> None:
    labels = LabelMap.build(SETTINGS)
    lines = [line for e in _spoken() for line in event_lines(e, labels, {})]
    assert lines == [f"[5] learner said: {WORDS}"]
    assert [source_text(e) for e in _spoken()] == [WORDS, ""]


def test_spoken_words_are_the_learners_text_of_the_journal() -> None:
    """A deadline quoted from speech is grounded in the voice event."""
    journal = read_journal(_spoken(), 6)
    assert [(t.seq, t.text) for t in journal.learner_texts if t.text] == [(5, WORDS)]


def test_a_typed_message_is_unchanged() -> None:
    typed = _event(7, "message.sent", MessageSent(text="typed words"))
    text = render_conversation([typed], LabelMap.build(SETTINGS), SETTINGS)
    assert text.splitlines()[-1] == "Learner: typed words"


def test_a_spoken_message_carries_no_text() -> None:
    adapter = TypeAdapter[Action](Action)
    key = str(uuid4())
    ok = adapter.validate_python({"kind": "message", "idempotency_key": key, "voice_seq": 5})
    assert isinstance(ok, MessageAction) and ok.text == "" and ok.voice_seq == 5
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"kind": "message", "idempotency_key": key, "voice_seq": 5, "text": WORDS}
        )
