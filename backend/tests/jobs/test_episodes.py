"""ТЗ Ф4 WP7.2 (`Т-§7.5`), T-D2 and T-D3 without a database: where the episodes end and
when an episode has nothing to interpret."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from quack.config import Settings
from quack.events.log import StoredEvent
from quack.jobs.episodes import EpisodeTracker, episode_has_content
from tests.events.samples import sample_payload, uid

LEARNER = UUID(int=7)
SESSION = UUID(int=100)
OTHER = UUID(int=101)
T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
SETTINGS = Settings(episode_max_turns=3, episode_idle_min=10)


def _ev(
    event_type: str,
    seq: int,
    *,
    at: int = 0,
    session: UUID | None = SESSION,
    payload: dict[str, Any] | None = None,
) -> StoredEvent:
    """`at` — minutes after T0."""
    return StoredEvent(
        learner_id=LEARNER,
        seq=seq,
        id=seq,
        type=event_type,
        schema_version=1,
        actor="api",
        domain_id=None,
        session_id=session,
        causation_seq=None,
        correlation_id=None,
        idempotency_key=f"k{seq}",
        payload_hash="h",
        occurred_at=T0 + timedelta(minutes=at),
        payload={**sample_payload(event_type), **(payload or {})},
        source_refs=[],
    )


def _turn(
    seq: int, *, intent: str = "practice", step: str = "practice", at: int = 0
) -> StoredEvent:
    event = _ev("turn.committed", seq, at=at)
    payload = event.payload
    payload["head"] = {**payload["head"], "intent": intent}
    payload["markup"]["plan_update"] = {**payload["markup"]["plan_update"], "current_step": step}
    return event


def _run(tracker: EpisodeTracker, events: list[StoredEvent]) -> list[tuple[int, int, str]]:
    out = []
    for event in events:
        out += [(c.from_seq, c.to_seq, c.reason) for c in tracker.feed(event)]
    return out


def test_task_closed_ends_the_episode_with_itself() -> None:
    tracker = EpisodeTracker(SETTINGS)
    got = _run(
        tracker,
        [_ev("message.sent", 1), _ev("answer.submitted", 2), _ev("task.closed", 3)],
    )
    assert got == [(1, 3, "task_closed")]
    assert tracker.dump() == {}
    # The next dialogue opens a new episode.
    assert _run(tracker, [_ev("message.sent", 4), _ev("task.closed", 5)]) == [(4, 5, "task_closed")]


def test_change_of_intent_ends_the_episode_before_the_turn() -> None:
    tracker = EpisodeTracker(SETTINGS)
    got = _run(
        tracker,
        [
            _ev("message.sent", 1),
            _turn(2, intent="practice"),
            _ev("message.sent", 3),
            _turn(4, intent="review"),
        ],
    )
    assert got == [(1, 3, "boundary")]
    assert next(iter(tracker.dump().values()))["from_seq"] == 4  # the changing turn opens the next


def test_change_of_plan_step_ends_the_episode() -> None:
    tracker = EpisodeTracker(SETTINGS)
    got = _run(
        tracker,
        [_turn(1, step="a"), _ev("message.sent", 2), _turn(3, step="a"), _turn(4, step="b")],
    )
    assert got == [(1, 3, "boundary")]


def test_same_intent_and_step_is_no_boundary() -> None:
    tracker = EpisodeTracker(SETTINGS)
    assert _run(tracker, [_turn(1), _turn(2)]) == []


def test_episode_max_turns_ends_with_the_last_turn() -> None:
    tracker = EpisodeTracker(SETTINGS)  # 3 turns
    got = _run(
        tracker,
        [_turn(1), _ev("message.sent", 2), _turn(3), _turn(4), _turn(5), _ev("message.sent", 6)],
    )
    assert got == [(1, 4, "max_turns")]
    assert next(iter(tracker.dump().values()))["from_seq"] == 5  # turn 5 opened the next one


def test_idle_gap_between_events_closes_the_episode_at_its_last_event() -> None:
    """By the event times, so that a replay closes the same episodes."""
    tracker = EpisodeTracker(SETTINGS)
    got = _run(
        tracker,
        [_ev("message.sent", 1, at=0), _ev("message.sent", 2, at=9), _ev("message.sent", 3, at=30)],
    )
    assert got == [(1, 2, "idle")]


def test_idle_check_by_the_clock() -> None:
    """T-D2: `episode_idle_min` minutes without events (the clock replaced)."""
    tracker = EpisodeTracker(SETTINGS)
    _run(tracker, [_ev("message.sent", 1, at=0), _ev("message.sent", 2, at=3)])
    assert tracker.idle(T0 + timedelta(minutes=12)) == []  # 9 minutes since the last event
    (closed,) = tracker.idle(T0 + timedelta(minutes=13))
    assert (closed.from_seq, closed.to_seq, closed.reason) == (1, 2, "idle")
    assert tracker.idle(T0 + timedelta(days=1)) == []


def test_session_ended_closes_with_itself_and_alone_closes_nothing() -> None:
    tracker = EpisodeTracker(SETTINGS)
    assert _run(tracker, [_ev("message.sent", 1), _ev("session.ended", 2)]) == [
        (1, 2, "session_ended")
    ]
    assert _run(tracker, [_ev("session.ended", 3)]) == []  # nothing is open
    assert _run(tracker, [_ev("task.closed", 4)]) == []


def test_sessions_have_their_own_episodes() -> None:
    tracker = EpisodeTracker(SETTINGS)
    got = _run(
        tracker,
        [
            _ev("message.sent", 1),
            _ev("message.sent", 2, session=OTHER),
            _ev("task.closed", 3),
            _ev("task.closed", 4, session=OTHER),
        ],
    )
    assert got == [(1, 3, "task_closed"), (2, 4, "task_closed")]


def test_outputs_of_the_system_neither_open_nor_extend_an_episode() -> None:
    tracker = EpisodeTracker(SETTINGS)
    # A late verdict, an observation, an agent result: no episode to join, none is opened.
    for seq, kind in enumerate(("verdict.recorded", "observation.extracted", "agent.completed"), 1):
        assert tracker.feed(_ev(kind, seq)) == []
    assert tracker.dump() == {}
    # In an open episode a verdict joins; the observation does not move the idle timer.
    _run(tracker, [_ev("message.sent", 4, at=0), _ev("verdict.recorded", 5, at=5)])
    tracker.feed(_ev("observation.extracted", 6, at=20))
    (closed,) = tracker.idle(T0 + timedelta(minutes=16))
    assert (closed.from_seq, closed.to_seq) == (4, 5)


def test_events_without_a_session_are_not_episode_events() -> None:
    tracker = EpisodeTracker(SETTINGS)
    assert tracker.feed(_ev("message.sent", 1, session=None)) == []
    assert tracker.dump() == {}


def test_state_survives_a_dump_and_load() -> None:
    first = EpisodeTracker(SETTINGS)
    _run(first, [_ev("message.sent", 1), _turn(2), _turn(3)])
    second = EpisodeTracker(SETTINGS, first.dump())
    assert second.dump() == first.dump()
    # The reloaded tracker closes exactly as the original would (3 turns → the third closes).
    assert _run(second, [_turn(4)]) == _run(first, [_turn(4)]) == [(1, 4, "max_turns")]


# --- T-D3: has_content --------------------------------------------------------------------


def _answer(seq: int, kind: str, **extra: Any) -> StoredEvent:
    answer = {"kind": kind, "value": "x"}
    return _ev("answer.submitted", seq, payload={"answer": answer, **extra})


def _verdict(
    seq: int, answer_seq: int, verification: str = "code", part: str = "main"
) -> StoredEvent:
    return _ev(
        "verdict.recorded",
        seq,
        payload={"answer_seq": answer_seq, "part": part, "verification": verification},
    )


def test_closed_answers_with_code_verdicts_have_no_content() -> None:
    events = [
        _turn(1),
        _answer(2, "choice"),
        _verdict(3, 2),
        _answer(4, "number"),
        _verdict(5, 4),
        _ev("hint.shown", 6),
        _ev("task.closed", 7),
    ]
    assert not episode_has_content(events)
    assert not episode_has_content([])  # no learner input at all: nothing to interpret


def test_the_learners_words_or_canvas_lines_are_content() -> None:
    base = [_answer(2, "choice"), _verdict(3, 2)]
    for extra in (
        _ev("message.sent", 4),
        _ev("canvas.line_committed", 4),
        _ev("canvas.line_erased", 4),
        _ev("voice.transcribed", 4),
        _ev("item.flagged_unclear", 4),
        _ev("element.responded", 4, payload={"text": "because"}),
    ):
        assert episode_has_content([*base, extra]), extra.type
    # A response to an element without words is a closed action.
    assert not episode_has_content([*base, _ev("element.responded", 4, payload={"text": None})])


def test_a_reasoning_text_or_an_open_answer_is_content() -> None:
    assert episode_has_content([_answer(2, "choice", text="I guessed"), _verdict(3, 2)])
    for kind in ("proof", "free_text", "drawing"):
        assert episode_has_content([_answer(2, kind), _verdict(3, 2)]), kind


def test_a_closed_answer_without_a_code_verdict_is_content() -> None:
    assert episode_has_content([_answer(2, "choice")])  # the verdict is not (yet) in the episode
    assert episode_has_content([_answer(2, "choice"), _verdict(3, 2, verification="model")])
    # The verdict is of another part or another answer.
    assert episode_has_content([_answer(2, "choice"), _verdict(3, 2, part="b")])
    assert episode_has_content([_answer(2, "choice"), _verdict(3, 1)])


def test_uuid_helpers_are_stable() -> None:
    assert uid("x") == uid("x")
