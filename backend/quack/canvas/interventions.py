"""Marks and interventions of a canvas (ТЗ Ф4 WP21.5; Т-§15.4, Т-§15.5, D-C-159).

The cascade of `quack.canvas.step_check` says what a line deserves; this module says when, and
whether, the learner sees it. The teacher does not react to every line (Т-§15.5):

- a mark of a confirmed error comes not at once but after `error_grace_ms` or after the next
  line; a line the learner erased meanwhile gets no mark — self-correction is a signal, not
  something to interrupt (`line_erased`);
- the learner is stuck after `stuck_idle_s` without strokes over an unfinished solution, or
  when one place is erased `stuck_erase_times` times: a quiet hint of the first rung, once;
- at most `max_unprompted_marks_per_task` unprompted interventions (marks and hints) on the
  task and not more often than one in `min_intervention_gap_s`; one that waits for the gap
  is shown when it passes;
- the quiet mode (`preference.set` `canvas_quiet`) lets no unprompted intervention through;
  a mark that falls due in it is dropped, and "проверь" (`check_request`) shows everything,
  bypassing the limits (`prompted`); evidence is written as usual either way.

`InterventionGate` is plain state over a clock the caller passes (seconds): the socket feeds it
events and asks `due(now)`; tests drive it with a fake clock. A second opinion that comes after
the mark was shown upgrades it to `error` or takes it off quietly without a new intervention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from quack.canvas.board import Hint, Mark
from quack.canvas.boards import put_hint, put_mark, remove_mark
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.practice.role_outputs import SolutionSpace

type MarkKind = Literal["error", "question"]
type Box = tuple[float, float, float, float]

QUIET_KEY = "canvas_quiet"
_QUIET_VALUES = frozenset({True, "true", "True", "1"})
_QUIET = (
    "SELECT payload -> 'value' AS value FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'preference.set' AND payload ->> 'key' = %(key)s ORDER BY seq DESC LIMIT 1"
)
_CLOSED = (
    "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s AND ("
    "(type = 'task.closed' AND payload ->> 'task_id' = %(task_id)s) OR "
    "(type = 'answer.submitted' AND payload ->> 'task_id' = %(task_id)s "
    "AND payload ->> 'source' = 'canvas')) LIMIT 1"
)


async def canvas_quiet(db: Db, *, learner_id: UUID) -> bool:
    """The learner's choice of the quiet mode: the latest `preference.set {key: canvas_quiet}`;
    none — the default, the restrained mode (Т-§15.5)."""
    row = await db.fetch_one(_QUIET, {"learner_id": learner_id, "key": QUIET_KEY})
    return row is not None and row["value"] in _QUIET_VALUES


async def task_finished(db: Db, *, learner_id: UUID, task_id: UUID) -> bool:
    """The task is closed or its final answer came from the canvas: nobody is stuck."""
    found = await db.fetch_one(_CLOSED, {"learner_id": learner_id, "task_id": str(task_id)})
    return found is not None


def mark_id(line_id: str) -> str:
    """One mark per line."""
    return f"mark:{line_id}"


@dataclass(frozen=True)
class Action:
    """What the socket does: `mark` (show or upgrade), `clear` (take off quietly) or `hint`."""

    kind: Literal["mark", "clear", "hint"]
    line_id: str | None = None
    mark: MarkKind | None = None
    # A mark counted as an unprompted intervention; an upgrade or a clear is not a new one.
    counted: bool = False


@dataclass
class _Pending:
    line_id: str
    kind: MarkKind
    due: float


def _meets(a: Box, b: Box) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _union(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


class InterventionGate:
    """The state of one canvas of one task. `shown_before` seeds the count of interventions
    already on the board (a reconnecting socket does not start the limit over)."""

    def __init__(self, settings: Settings, *, shown_before: int = 0) -> None:
        self._grace = settings.error_grace_ms / 1000
        self._gap = settings.min_intervention_gap_s
        self._max = settings.max_unprompted_marks_per_task
        self._idle = settings.stuck_idle_s
        self._erase_times = settings.stuck_erase_times
        self.quiet = False
        self.finished = False
        # The solution space has a hint to give; without it the teacher answers by a turn.
        self.can_hint = True
        self._count = shown_before
        self._last: float | None = None
        self._lines: list[str] = []
        self._pending: dict[str, _Pending] = {}
        self._shown: dict[str, MarkKind] = {}
        self._last_stroke: float | None = None
        self._places: list[tuple[Box, int]] = []
        self._stuck = False
        self._hinted = False

    # --- what happens on the board ---

    def line_written(self, line_id: str, now: float) -> None:
        """A line is finished: marks waiting for it ("or after the next line") fall due."""
        if line_id not in self._lines:
            self._lines.append(line_id)
        for pending in self._pending.values():
            if pending.line_id != line_id:
                pending.due = min(pending.due, now)

    def strokes(self, now: float) -> None:
        """The learner writes: the idle time starts over."""
        self._last_stroke = now

    def erased_place(self, box: Box, now: float) -> None:
        """Strokes of `box` were erased; the same place `stuck_erase_times` times — stuck."""
        for index, (seen, times) in enumerate(self._places):
            if _meets(seen, box):
                self._places[index] = (_union(seen, box), times + 1)
                if times + 1 >= self._erase_times:
                    self._stuck = True
                break
        else:
            self._places.append((box, 1))
            if self._erase_times <= 1:
                self._stuck = True
        self._last_stroke = now

    def propose(self, line_id: str, kind: MarkKind, now: float) -> None:
        """The cascade found a mark for the line. Due after the grace, at once when a later
        line is already written."""
        if line_id not in self._lines:
            return  # erased while the check was running: self-correction
        followed = self._lines[-1] != line_id
        self._pending[line_id] = _Pending(line_id, kind, now if followed else now + self._grace)

    def line_erased(self, line_id: str) -> list[Action]:
        """The learner erased the line: a waiting mark is cancelled (self-correction, no mark),
        a shown one is taken off."""
        self._pending.pop(line_id, None)
        if line_id in self._lines:
            self._lines.remove(line_id)
        if self._shown.pop(line_id, None) is not None:
            return [Action("clear", line_id)]
        return []

    def second_opinion(self, line_id: str, agreed: bool | None) -> list[Action]:
        """The second opinion on an unsure diagnosis: agreed — `error`, not agreed — the
        question goes quietly, no answer — nothing changes."""
        if agreed is None:
            return []
        waiting = self._pending.get(line_id)
        if waiting is not None:
            if agreed:
                waiting.kind = "error"
            else:
                del self._pending[line_id]
            return []
        if line_id in self._shown:
            if agreed:
                self._shown[line_id] = "error"
                return [Action("mark", line_id, "error")]
            del self._shown[line_id]
            return [Action("clear", line_id)]
        return []

    def prompted(self, line_id: str, kind: MarkKind) -> Action | None:
        """A mark asked for with "проверь": shown at once, not counted, bypasses the quiet
        mode and the limits."""
        if self._shown.get(line_id) == kind:
            return None
        self._pending.pop(line_id, None)
        self._shown[line_id] = kind
        return Action("mark", line_id, kind)

    # --- when ---

    def due(self, now: float) -> list[Action]:
        """The interventions that may be shown now, at most one per gap."""
        actions: list[Action] = []
        for pending in sorted(self._pending.values(), key=lambda p: p.due):
            if pending.due > now:
                continue
            if self.quiet or self._count >= self._max:
                del self._pending[pending.line_id]  # not shown; the learner can ask
                continue
            if self._waits(now):
                continue
            del self._pending[pending.line_id]
            self._shown[pending.line_id] = pending.kind
            self._used(now)
            actions.append(Action("mark", pending.line_id, pending.kind, counted=True))
        if self._is_stuck(now):
            if self.quiet or self._count >= self._max:
                self._hinted = True
            elif not self._waits(now):
                self._hinted = True
                self._used(now)
                last = self._lines[-1] if self._lines else None
                actions.append(Action("hint", last, counted=True))
        return actions

    def next_due_in(self, now: float) -> float | None:
        """Seconds until `due` may have something; `None` — nothing is waiting."""
        moments: list[float] = []
        for pending in self._pending.values():
            moments.append(max(pending.due, self._free_at(now)))
        if self._stuck_possible() and (self._stuck or self._last_stroke is not None):
            at = now if self._stuck or self._last_stroke is None else self._last_stroke + self._idle
            moments.append(max(at, self._free_at(now)))
        return max(0.0, min(moments) - now) if moments else None

    def _used(self, now: float) -> None:
        self._count += 1
        self._last = now

    def _free_at(self, now: float) -> float:
        return now if self._last is None else max(now, self._last + self._gap)

    def _waits(self, now: float) -> bool:
        return self._last is not None and now < self._last + self._gap

    def _stuck_possible(self) -> bool:
        return self.can_hint and not self._hinted and not self.finished and bool(self._lines)

    def _is_stuck(self, now: float) -> bool:
        if not self._stuck_possible():
            return False
        if self._stuck:
            return True
        return self._last_stroke is not None and now - self._last_stroke >= self._idle


# --- the board and the log ---------------------------------------------------------------


async def show_mark(
    db: Db, *, learner_id: UUID, canvas_id: UUID, line_id: str, kind: MarkKind
) -> None:
    """The mark goes to the teacher layer of the board; the words are the client's."""
    mark = Mark(mark_id=mark_id(line_id), line_id=line_id, kind=kind, text="")
    await put_mark(db, learner_id=learner_id, canvas_id=canvas_id, mark=mark)


async def clear_mark(db: Db, *, learner_id: UUID, canvas_id: UUID, line_id: str) -> None:
    await remove_mark(db, learner_id=learner_id, canvas_id=canvas_id, mark_id=mark_id(line_id))


FIRST_RUNG = 1


async def show_first_hint(
    db: Db,
    *,
    learner_id: UUID,
    session_id: UUID | None,
    canvas_id: UUID,
    task_id: UUID,
    space: SolutionSpace | None,
    line_id: str | None,
) -> str | None:
    """The quiet hint of the first rung from the solution space (Т-§5.5): `hint.shown
    {task_id, step: 1, source: canvas}` and a hint in the teacher layer. No space, no hint —
    the teacher answers with an ordinary turn when the learner asks (Т-§15.5); `None`."""
    if space is None or not space.hints:
        return None
    text = space.hints[FIRST_RUNG - 1]
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="hint.shown",
                payload={"task_id": str(task_id), "step": FIRST_RUNG, "source": "canvas"},
                actor="code",
                idempotency_key=f"hint.shown:canvas:{canvas_id}:{task_id}:{FIRST_RUNG}",
                session_id=session_id,
            )
        ],
    )
    await put_hint(
        db,
        learner_id=learner_id,
        canvas_id=canvas_id,
        hint=Hint(
            hint_id=f"hint:{task_id}:{FIRST_RUNG}",
            task_id=task_id,
            step=FIRST_RUNG,
            text=text,
            line_id=line_id,
        ),
    )
    return text
