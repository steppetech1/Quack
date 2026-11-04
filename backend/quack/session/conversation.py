"""Conversation of the teacher context — slot 5 (Т-§10.3; ТЗ Ф4 WP14.3).

Built by code from the events of the session only: past sessions never come in raw (П-2.1).
The last turns go in full — learner actions, the teacher text, elements, answers and verdicts,
canvas lines with their numbers (Т-§15.6), "unclear" flags. Past
`conversation_verbatim_max_turns` the oldest `conversation_collapse_turns` turns fold at once
into episode lines (`collapsed_turn_count` of Phase 3 WP13): the start of the visible part
changes once per batch, so the prefix cache of Meta holds (Т-§10.1). Episode lines are written
by code from events and markup, never by a model (law 10). English (Т-§22.2). D-B-158.

A turn is a `turn.committed` with the events before it since the previous turn and the events
of its own append (`causation_seq` = its `action_seq`, WP2.2). Events after the last turn are
the learner's current action: they go last.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from quack.config import Settings
from quack.events.event_types import (
    AnswerSubmitted,
    CanvasLineCommitted,
    CanvasLineErased,
    CanvasRef,
    CheckSkipped,
    ElementResponded,
    HandwritingCorrected,
    HintRequested,
    HintShown,
    ItemFlaggedUnclear,
    MessageSent,
    SolutionRequested,
    TaskClosed,
    TurnCommitted,
    VoiceTranscribed,
)
from quack.events.log import StoredEvent
from quack.events.values import CheckFailed, Verdict
from quack.llm.labels import LabelKind, LabelMap
from quack.session.checkpoint_render import collapsed_turn_count

_INTENT_BUTTONS = {"learn": "Learn", "practice": "Practice", "review": "Review"}

_OUTCOME_WORDS = {
    "correct": "right",
    "incorrect": "wrong",
    "partial": "partly right",
    "unclear": "unclear",
}

_CLOSED_WORDS = {"solved": "solved", "skipped": "skipped", "solution_shown": "solution shown"}

_EXPOSURE_WORDS = {
    "explained": "explained",
    "example": "worked example",
    "method_named": "method named",
    "solution_shown": "solution shown",
}


@dataclass
class Turn:
    """One turn of the session: its events in order and its number from the session start."""

    number: int
    events: list[StoredEvent] = field(default_factory=list)


@dataclass(frozen=True)
class SessionTurns:
    turns: list[Turn]
    current: list[StoredEvent]


def split_turns(events: Sequence[StoredEvent]) -> SessionTurns:
    """The turns of the session and the events of the current action (after the last turn)."""
    ordered = sorted(events, key=lambda e: e.seq)
    by_action: dict[int, Turn] = {}
    for event in ordered:
        if event.type == "turn.committed":
            action_seq = int(event.payload["action_seq"])
            by_action.setdefault(action_seq, Turn(number=len(by_action) + 1))
    turns: list[Turn] = []
    closed: set[int] = set()
    pending: list[StoredEvent] = []
    for event in ordered:
        if event.type == "turn.committed":
            action_seq = int(event.payload["action_seq"])
            turn = by_action[action_seq]
            turn.events.extend(pending)
            turn.events.append(event)
            pending = []
            turns.append(turn)
            closed.add(action_seq)
        elif event.causation_seq is not None and event.causation_seq in closed:
            by_action[event.causation_seq].events.append(event)
        else:
            pending.append(event)
    return SessionTurns(turns=turns, current=pending)


class _Lines:
    """Canvas lines numbered in the order of the session (Т-§15.6); the numbers stay put."""

    def __init__(self) -> None:
        self._numbers: dict[tuple[UUID, str], int] = {}

    def number(self, canvas_id: UUID, line_id: str) -> int:
        return self._numbers.setdefault((canvas_id, line_id), len(self._numbers) + 1)

    def known(self, canvas_id: UUID, line_id: str) -> int | None:
        return self._numbers.get((canvas_id, line_id))


def _ref(labels: LabelMap, kind: LabelKind, object_id: UUID | None, fallback: str) -> str:
    label = labels.label(kind, object_id) if object_id is not None else None
    return label or fallback


def _task(labels: LabelMap, task_id: UUID) -> str:
    return "task " + _ref(labels, "task", task_id, "(earlier task)")


def _answer_seqs(turn_events: Sequence[StoredEvent]) -> dict[int, UUID]:
    return {
        e.seq: AnswerSubmitted.model_validate(e.payload).task_id
        for e in turn_events
        if e.type == "answer.submitted"
    }


def _lasso(refs: Sequence[CanvasRef], lines: _Lines) -> list[str]:
    """The lasso «ask about this» (Т-§15.6): the lines by their numbers in the session, a block
    of the board by its id."""
    out: list[str] = []
    for ref in refs:
        if ref.line_id is not None:
            number = lines.known(ref.canvas_id, ref.line_id)
            out.append(f"canvas line L{number}" if number else "a canvas line")
        if ref.block_id is not None:
            out.append(f"block {ref.block_id}")
    return out


def _verbatim(
    event: StoredEvent, labels: LabelMap, lines: _Lines, answers: dict[int, UUID]
) -> list[str]:
    data = event.payload
    match event.type:
        case "message.sent":
            message = MessageSent.model_validate(data)
            out: list[str] = []
            if message.intent is not None:
                target = (
                    _ref(labels, "topic", message.topic_id, "a topic")
                    if message.topic_id
                    else _ref(labels, "hub", message.hub_id, "the domain")
                )
                out.append(f'Learner pressed "{_INTENT_BUTTONS[message.intent]}" on {target}')
            if message.text:
                out.append(f"Learner: {message.text}")
            if message.canvas_refs:
                out.append("Learner points at: " + ", ".join(_lasso(message.canvas_refs, lines)))
            return out
        case "answer.submitted":
            answer = AnswerSubmitted.model_validate(data)
            part = f" part {answer.part}" if answer.part else ""
            value = answer.answer.latex or answer.text or answer.answer.value
            return [f"Learner answer to {_task(labels, answer.task_id)}{part}: {value}"]
        case "verdict.recorded":
            verdict = Verdict.model_validate(data)
            task_id = answers.get(verdict.answer_seq)
            target = _task(labels, task_id) if task_id else "the answer"
            by = "by code" if verdict.verification == "code" else "by a model"
            words = _OUTCOME_WORDS[verdict.outcome]
            return [f"Check of {target} (answer {verdict.answer_seq}): {words}, {by}"]
        case "check.failed":
            failed = CheckFailed.model_validate(data)
            return [f"Check of answer {failed.answer_seq}: code could not check it"]
        case "element.responded":
            response = ElementResponded.model_validate(data)
            return [f"Learner response to element {response.element_id}: {response.response}"]
        case "hint.requested":
            task_id = HintRequested.model_validate(data).task_id
            return [f"Learner asked for a hint on {_task(labels, task_id)}"]
        case "solution.requested":
            task_id = SolutionRequested.model_validate(data).task_id
            return [f"Learner asked for the solution of {_task(labels, task_id)}"]
        case "check.skipped":
            task_id = CheckSkipped.model_validate(data).task_id
            return [f"Learner skipped the check of {_task(labels, task_id)}"]
        case "item.flagged_unclear":
            flag = ItemFlaggedUnclear.model_validate(data)
            return [f'Learner marked as unclear (block {flag.block_id}): "{flag.text}"']
        case "canvas.line_committed":
            line = CanvasLineCommitted.model_validate(data)
            number = lines.number(line.canvas_id, line.line_id)
            return [f"Canvas line L{number}: {line.text}"]
        case "canvas.line_erased":
            erased = CanvasLineErased.model_validate(data)
            return [f"Canvas line L{lines.number(erased.canvas_id, erased.line_id)} erased"]
        case "handwriting.corrected":
            fixed = HandwritingCorrected.model_validate(data)
            number = lines.number(fixed.canvas_id, fixed.line_id)
            return [f"Canvas line L{number} corrected by the learner: {fixed.text}"]
        case "voice.transcribed":
            return [f"Learner said: {VoiceTranscribed.model_validate(data).text}"]
        case "turn.committed":
            turn = TurnCommitted.model_validate(data)
            out = [f"Teacher: {turn.text}"]
            tasks = {task.element_id: task.task_id for task in turn.markup.tasks}
            for element in turn.markup.elements:
                text = f"Element {element.element_id}: {element.kind.replace('_', ' ')}"
                if element.element_id in tasks:
                    text += f", {_task(labels, tasks[element.element_id])}"
                out.append(text)
            return out
        case "hint.shown":
            hint = HintShown.model_validate(data)
            return [f"Hint {hint.step} shown on {_task(labels, hint.task_id)}"]
        case "task.closed":
            closed = TaskClosed.model_validate(data)
            how = _CLOSED_WORDS[closed.how]
            return [f"Task {_ref(labels, 'task', closed.task_id, '(earlier task)')} closed: {how}"]
        case _:
            return []


def _episode_line(turn: Turn, labels: LabelMap, answers: dict[int, UUID]) -> str:
    """One line of a folded turn: what the teacher did and what happened with the tasks."""
    head = "reply"
    facts: list[str] = []
    canvas = 0
    for event in turn.events:
        data = event.payload
        match event.type:
            case "turn.committed":
                committed = TurnCommitted.model_validate(data)
                head = committed.head.intent.replace("_", " ")
                for exposure in committed.markup.exposures:
                    skill = _ref(labels, "skill", exposure.skill_id, "a skill")
                    facts.append(f"{_EXPOSURE_WORDS[exposure.kind]} {skill}")
                for task in committed.markup.tasks:
                    levels = ", ".join(str(t.level) for t in task.tests)
                    facts.append(f"{_task(labels, task.task_id)} given (level {levels})")
            case "verdict.recorded":
                verdict = Verdict.model_validate(data)
                task_id = answers.get(verdict.answer_seq)
                target = _task(labels, task_id) if task_id else "an answer"
                facts.append(f"answer to {target}: {_OUTCOME_WORDS[verdict.outcome]}")
            case "hint.shown":
                hint = HintShown.model_validate(data)
                facts.append(f"hint {hint.step} on {_task(labels, hint.task_id)}")
            case "task.closed":
                closed = TaskClosed.model_validate(data)
                facts.append(f"{_task(labels, closed.task_id)} {_CLOSED_WORDS[closed.how]}")
            case "item.flagged_unclear":
                facts.append("learner marked a block as unclear")
            case "canvas.line_committed":
                canvas += 1
            case _:
                pass
    if canvas:
        facts.append(f"{canvas} canvas line" + ("" if canvas == 1 else "s"))
    return f"Turn {turn.number}, {head}" + (": " + "; ".join(facts) if facts else "")


def render_conversation(events: Sequence[StoredEvent], labels: LabelMap, settings: Settings) -> str:
    """Slot 5: episode lines of the folded turns, the later turns in full, the current action
    last."""
    session = split_turns(events)
    answers = _answer_seqs(sorted(events, key=lambda e: e.seq))
    lines = _Lines()
    folded = collapsed_turn_count(len(session.turns), settings)
    out: list[str] = []
    for turn in session.turns[:folded]:
        for event in turn.events:  # canvas numbers count from the session start
            if event.type in ("canvas.line_committed", "canvas.line_erased"):
                _verbatim(event, labels, lines, answers)
        out.append(_episode_line(turn, labels, answers))
    for turn in session.turns[folded:]:
        out.append(f"Turn {turn.number}")
        for event in turn.events:
            out.extend(_verbatim(event, labels, lines, answers))
    if session.current:
        out.append("Current action")
        for event in session.current:
            out.extend(_verbatim(event, labels, lines, answers))
    return "\n".join(out)
