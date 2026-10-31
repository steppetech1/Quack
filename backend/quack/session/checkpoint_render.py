"""Deterministic session summary for `session.checkpoint` (law 10, Т-§10.3, WP13).

No model call and no previous summary on input: `rolling_summary`, `open_threads` and
`key_observations` are built by templates from the session events only (`turn.extracted`,
`task.evaluated`, turn events). Episode lines are English (Т-§22.2). Events of the
`session.checkpointed` keep their schema; the renderer version is `renderer@1` (D-B-002).

A turn is the pair of replies with one `turn_id`; `turn_index` of the events counts replies.
The conversation of the teacher context shows the turns from `collapsed_turn_count` on in full;
the turns before it are the episode lines of `rolling_summary` (D-B-023).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from quack.config import Settings
from quack.events.models import StoredEvent

RENDERER_VERSION = "renderer@1"

REPLY_TYPES = ("turn.student_replied", "turn.teacher_replied")
# The events the renderer reads; everything else of the session (earlier summaries included,
# `T-C3`) is ignored.
RENDER_EVENT_TYPES = (*REPLY_TYPES, "task.assigned", "task.evaluated", "turn.extracted")


@dataclass(frozen=True)
class RenderedCheckpoint:
    """Fields of the `session.checkpointed` payload built by code."""

    up_to_turn_index: int
    rolling_summary: str
    open_threads: list[str]
    key_observations: list[str]


@dataclass
class _Turn:
    number: int
    turn_id: str
    last_reply_index: int = -1
    move: str = ""
    facts: list[str] | None = None


def collapsed_turn_count(turn_count: int, settings: Settings) -> int:
    """Turns folded into episode lines: none while the verbatim window holds
    `conversation_verbatim_max_turns`; past it, the oldest `conversation_collapse_turns` at once,
    so the start of the visible part changes once per batch (`T-C4`)."""
    limit = settings.conversation_verbatim_max_turns
    batch = settings.conversation_collapse_turns
    if turn_count <= limit or batch <= 0:
        return 0
    batches = -(-(turn_count - limit) // batch)
    return min(turn_count, batches * batch)


def _turns(events: Sequence[StoredEvent]) -> list[_Turn]:
    """Turns of the session in the order of their first reply."""
    by_id: dict[str, _Turn] = {}
    for event in sorted(events, key=lambda e: e.seq):
        if event.type not in REPLY_TYPES:
            continue
        turn_id = str(event.payload["turn_id"])
        turn = by_id.get(turn_id)
        if turn is None:
            turn = by_id[turn_id] = _Turn(number=len(by_id) + 1, turn_id=turn_id, facts=[])
        turn.last_reply_index = max(turn.last_reply_index, int(event.payload["turn_index"]))
        if event.type == "turn.teacher_replied":
            turn.move = str(event.payload.get("pedagogical_move") or "")
    return list(by_id.values())


def _turn_id(event: StoredEvent) -> str | None:
    raw = event.payload.get("turn_id", event.turn_id)
    return None if raw is None else str(raw)


def _words(code: str) -> str:
    return code.replace("_", " ")


def _element_fact(element: dict[str, Any]) -> str | None:
    """One accepted `extractor@2` element as words (rejected elements are skipped)."""
    if element.get("rejected_reason") is not None:
        return None
    if element.get("kind") == "misconception_hypothesis":
        return "possible misconception"
    parts = []
    if element.get("level") is not None:
        parts.append(f"level {element['level']}")
    if element.get("outcome") is not None:
        parts.append(_words(str(element["outcome"])))
    assistance = element.get("assistance")
    if assistance == "hint":
        step = element.get("hint_step")
        parts.append(f"after hint {step}" if step is not None else "after a hint")
    elif assistance == "shown_solution":
        parts.append("after the solution was shown")
    gap = element.get("gap_type")
    if gap not in (None, "none"):
        parts.append(f"gap: {_words(str(gap))}")
    return " ".join(parts) if parts else None


def _quote(text: str) -> str:
    return f' "{text}"' if text else ""


@dataclass
class _Rendered:
    turns: list[_Turn]
    open_threads: list[str]
    key_observations: list[str]


def _render(events: Sequence[StoredEvent], up_to_turn_index: int | None) -> _Rendered:
    turns = [
        t
        for t in _turns(events)
        if up_to_turn_index is None or t.last_reply_index <= up_to_turn_index
    ]
    by_id = {t.turn_id: t for t in turns}
    task_numbers: dict[str, int] = {}
    task_solved: dict[str, bool | None] = {}
    observations: list[str] = []
    for event in sorted(events, key=lambda e: e.seq):
        turn_id = _turn_id(event)
        turn = by_id.get(turn_id) if turn_id is not None else None
        if turn is None or turn.facts is None:
            continue
        data = event.payload
        if event.type == "task.assigned":
            task = str(data["task_id"])
            number = task_numbers.setdefault(task, len(task_numbers) + 1)
            task_solved.setdefault(task, None)
            turn.facts.append(f"task {number} assigned")
        elif event.type == "task.evaluated":
            task = str(data["task_id"])
            number = task_numbers.setdefault(task, len(task_numbers) + 1)
            solved = bool(data.get("is_correct"))
            task_solved[task] = solved or bool(task_solved.get(task))
            turn.facts.append(f"task {number} {'solved' if solved else 'not solved'}")
            if data.get("triggered_misconceptions"):
                observations.append(
                    f"Turn {turn.number}: task {number} triggered a known misconception"
                )
        elif event.type == "turn.extracted":
            if event.schema_version >= 2:
                for element in data.get("elements") or []:
                    fact = _element_fact(element)
                    if fact is None:
                        continue
                    turn.facts.append(fact)
                    if element.get("kind") == "misconception_hypothesis":
                        observations.append(
                            f"Turn {turn.number}: possible misconception"
                            + _quote(str(element.get("quote") or ""))
                        )
                    elif element.get("outcome") in ("incorrect", "partial"):
                        observations.append(
                            f"Turn {turn.number}: {fact}" + _quote(str(element.get("quote") or ""))
                        )
            else:
                for misconception in data.get("misconceptions") or []:
                    name = str(misconception.get("name") or "")
                    turn.facts.append("possible misconception")
                    observations.append(f"Turn {turn.number}: possible misconception: {name}")
    open_threads = [
        f"Task {number} is open" + ("" if task_solved[task] is None else ": last answer not solved")
        for task, number in task_numbers.items()
        if not task_solved.get(task)
    ]
    return _Rendered(turns, open_threads, observations)


def _line(turn: _Turn) -> str:
    head = f"Turn {turn.number}, {_words(turn.move) or 'reply'}"
    facts = turn.facts or []
    return head + (": " + "; ".join(facts) if facts else "")


def episode_lines(events: Sequence[StoredEvent], settings: Settings) -> list[str]:
    """Collapsed lines of the oldest `conversation_collapse_turns` turns once the verbatim
    window exceeds `conversation_verbatim_max_turns`, in whole batches (`T-C4`)."""
    rendered = _render(events, None)
    count = collapsed_turn_count(len(rendered.turns), settings)
    return [_line(turn) for turn in rendered.turns[:count]]


def render_checkpoint(
    events: Sequence[StoredEvent], up_to_turn_index: int, settings: Settings
) -> RenderedCheckpoint:
    """Same events → byte-identical result (`T-C2`); earlier summaries are ignored (`T-C3`)."""
    rendered = _render(events, up_to_turn_index)
    count = collapsed_turn_count(len(rendered.turns), settings)
    return RenderedCheckpoint(
        up_to_turn_index=up_to_turn_index,
        rolling_summary="\n".join(_line(turn) for turn in rendered.turns[:count]),
        open_threads=rendered.open_threads,
        key_observations=rendered.key_observations,
    )


def visible_replies(
    replies: Sequence[StoredEvent], up_to_turn_index: int, settings: Settings
) -> list[StoredEvent]:
    """Replies of the teacher context shown in full: those of the turns after the ones that the
    checkpoint covering `up_to_turn_index` folded into episode lines (D-B-023)."""
    turns = _turns(replies)
    covered = sum(1 for t in turns if t.last_reply_index <= up_to_turn_index)
    folded = {t.turn_id for t in turns[: collapsed_turn_count(covered, settings)]}
    return [
        e
        for e in sorted(replies, key=lambda e: e.seq)
        if e.type in REPLY_TYPES and str(e.payload["turn_id"]) not in folded
    ]
