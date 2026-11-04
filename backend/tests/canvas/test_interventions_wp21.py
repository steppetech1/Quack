"""WP21.5: marks and interventions, T-CV5 (ТЗ Ф4 WP21.5; Т-§15.4, Т-§15.5; D-C-159). The gate is
plain state over a clock the test passes, so the times of the document (8 s, 45 s, 60 s) run
without waiting."""

from __future__ import annotations

from quack.canvas.board import BoardDoc
from quack.canvas.boards import get_canvas
from quack.canvas.interventions import (
    Action,
    InterventionGate,
    canvas_quiet,
    clear_mark,
    show_first_hint,
    show_mark,
    task_finished,
)
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from tests.canvas.steps_support import SPACE, events, make_canvas
from tests.db.fixtures import create_learner

GRACE = 8.0
GAP = 45.0


def _gate(settings: Settings, **update: int) -> InterventionGate:
    return InterventionGate(settings.model_copy(update=update))


def _marks(actions: list[Action]) -> list[tuple[str | None, str | None]]:
    return [(a.line_id, a.mark) for a in actions if a.kind == "mark"]


def test_intervention_limits(settings: Settings) -> None:
    """T-CV5: an error mark comes after `error_grace_ms` or after the next line; not more than
    `max_unprompted_marks_per_task` on the task and not oftener than `min_intervention_gap_s`;
    a mark that waits for the gap is shown when it passes."""
    gate = _gate(settings)
    assert (settings.error_grace_ms, settings.min_intervention_gap_s) == (8000, 45)
    assert settings.max_unprompted_marks_per_task == 2

    gate.line_written("l1", 0.0)
    gate.propose("l1", "error", 0.0)
    assert gate.next_due_in(0.0) == GRACE
    assert gate.due(GRACE - 0.1) == []
    assert _marks(gate.due(GRACE)) == [("l1", "error")]  # by the grace

    gate.line_written("l2", 10.0)
    gate.propose("l2", "error", 10.0)
    gate.line_written("l3", 11.0)  # the next line: l2 falls due now, but the gap holds it
    assert gate.due(11.0) == []
    assert gate.next_due_in(11.0) == GRACE + GAP - 11.0
    assert gate.due(GRACE + GAP - 0.1) == []
    assert _marks(gate.due(GRACE + GAP)) == [("l2", "error")]  # the second, after the gap

    gate.propose("l3", "error", 60.0)
    assert gate.due(500.0) == []  # the limit of two is reached: dropped, not delayed
    assert gate.next_due_in(500.0) is None


def test_the_next_line_brings_the_mark_forward(settings: Settings) -> None:
    gate = _gate(settings)
    gate.line_written("l1", 0.0)
    gate.propose("l1", "error", 0.0)
    gate.line_written("l2", 3.0)
    assert _marks(gate.due(3.0)) == [("l1", "error")]
    # A diagnosis that arrives when a later line is already there is due at once.
    gate.line_written("l2", 100.0)
    gate.line_written("l3", 100.0)
    gate.propose("l2", "question", 100.0)
    assert gate.next_due_in(100.0) == 0.0
    assert _marks(gate.due(100.0)) == [("l2", "question")]


def test_self_correction_gives_no_mark(settings: Settings) -> None:
    """Т-§15.5: the learner erased the line before the mark came - no mark, nothing to clear;
    a mark already shown is taken off quietly and is still counted."""
    gate = _gate(settings)
    gate.line_written("l1", 0.0)
    gate.propose("l1", "error", 0.0)
    assert gate.line_erased("l1") == []
    assert gate.due(GRACE * 10) == []

    gate.line_written("l2", 100.0)
    gate.propose("l2", "error", 100.0)
    assert _marks(gate.due(100.0 + GRACE)) == [("l2", "error")]
    assert gate.line_erased("l2") == [Action("clear", "l2")]
    gate.line_written("l3", 200.0)
    gate.propose("l3", "error", 200.0)
    assert _marks(gate.due(200.0 + GRACE)) == [("l3", "error")]  # the second of two
    gate.line_written("l4", 300.0)
    gate.propose("l4", "error", 300.0)
    assert gate.due(1000.0) == []  # l2 was counted although the learner erased it


def test_quiet_mode_lets_no_unprompted_mark_through(settings: Settings) -> None:
    """Т-§15.5: in the quiet mode nothing is shown on its own; what fell due is dropped, the
    learner asks with "проверь" (`prompted`), which is not limited and not counted."""
    gate = _gate(settings)
    gate.quiet = True
    gate.line_written("l1", 0.0)
    gate.propose("l1", "error", 0.0)
    assert gate.due(GRACE) == []
    gate.quiet = False
    assert gate.due(GRACE * 2) == []  # it is gone, not postponed

    gate.quiet = True
    for number in range(5):
        action = gate.prompted(f"p{number}", "error")
        assert action == Action("mark", f"p{number}", "error", counted=False)
    assert gate.prompted("p0", "error") is None  # already on the board
    gate.quiet = False
    gate.line_written("l9", 1000.0)
    gate.propose("l9", "error", 1000.0)
    assert _marks(gate.due(1000.0 + GRACE)) == [("l9", "error")]  # the prompted did not count


def test_second_opinion_decides_the_unsure_mark(settings: Settings) -> None:
    """Т-§15.4 п.4: a question waits for the second opinion; agreed - `error` (before or after
    it was shown), not agreed - no mark, or taken off quietly; no answer - a question."""
    gate = _gate(settings)
    gate.line_written("a", 0.0)
    gate.propose("a", "question", 0.0)
    assert gate.second_opinion("a", True) == []
    assert _marks(gate.due(GRACE)) == [("a", "error")]  # shown as the agreed error

    gate.line_written("b", 100.0)
    gate.propose("b", "question", 100.0)
    assert gate.second_opinion("b", False) == []  # before the mark: it never appears
    assert gate.due(100.0 + GRACE) == []

    gate.line_written("c", 200.0)
    gate.propose("c", "question", 200.0)
    assert gate.second_opinion("c", None) == []
    assert _marks(gate.due(200.0 + GRACE)) == [("c", "question")]
    assert gate.second_opinion("c", True) == [Action("mark", "c", "error")]
    assert gate.second_opinion("c", False) == [Action("clear", "c")]  # taken off quietly
    assert gate.second_opinion("zzz", True) == []


def test_stuck_gives_a_quiet_hint_once(settings: Settings) -> None:
    """Т-§15.5: `stuck_idle_s` without strokes over an unfinished solution, or one place erased
    `stuck_erase_times` times: a hint of the first rung beside the last line, once; not for a
    finished task, not without a hint in the solution space, not in the quiet mode."""
    idle = settings.stuck_idle_s
    gate = _gate(settings)
    assert gate.due(1000.0) == []  # nothing written yet: not stuck, just not started
    gate.line_written("l1", 0.0)
    gate.strokes(0.0)
    assert gate.next_due_in(0.0) == idle
    assert gate.due(idle - 1) == []
    assert gate.due(idle) == [Action("hint", "l1", counted=True)]
    assert gate.due(idle * 10) == [] and gate.next_due_in(idle * 10) is None

    place = _gate(settings)
    place.line_written("l1", 0.0)
    place.strokes(0.0)
    for at in (1.0, 2.0):
        place.erased_place((0.0, 0.0, 10.0, 10.0), at)
        assert place.due(at) == []
    place.erased_place((5.0, 5.0, 12.0, 12.0), 3.0)  # the same place, the third time
    assert place.due(3.0) == [Action("hint", "l1", counted=True)]

    elsewhere = _gate(settings)
    elsewhere.line_written("l1", 0.0)
    for box in ((0, 0, 1, 1), (50, 50, 51, 51), (100, 100, 101, 101)):
        elsewhere.erased_place(box, 1.0)
    assert elsewhere.due(2.0) == []

    for blocked in ("finished", "no_hint", "quiet"):
        gate = _gate(settings)
        gate.line_written("l1", 0.0)
        gate.strokes(0.0)
        gate.finished = blocked == "finished"
        gate.can_hint = blocked != "no_hint"
        gate.quiet = blocked == "quiet"
        assert gate.due(idle * 2) == []


def test_a_hint_counts_as_an_intervention(settings: Settings) -> None:
    gate = _gate(settings, max_unprompted_marks_per_task=1)
    gate.line_written("l1", 0.0)
    gate.strokes(0.0)
    assert gate.due(settings.stuck_idle_s) == [Action("hint", "l1", counted=True)]
    gate.propose("l1", "error", 100.0)
    assert gate.due(1000.0) == []  # one intervention was the hint

    seeded = InterventionGate(settings, shown_before=2)  # a reconnecting socket
    seeded.line_written("l1", 0.0)
    seeded.propose("l1", "error", 0.0)
    assert seeded.due(1000.0) == []


async def test_marks_and_hints_go_to_the_teacher_layer(pg_db: Db) -> None:
    """The teacher layer of the board holds the marks (replaced when unsure becomes sure,
    taken off quietly) and the hint; the learner layer is never touched (П-9.4). The hint is
    `hint.shown {task_id, step: 1, source: canvas}` once; without a space there is none."""
    learner = await create_learner(pg_db)
    session_id, canvas = await make_canvas(pg_db, learner)
    assert canvas.task_id is not None
    where = {"learner_id": learner, "canvas_id": canvas.canvas_id}

    await show_mark(pg_db, line_id="l1", kind="question", **where)
    await show_mark(pg_db, line_id="l1", kind="error", **where)
    await show_mark(pg_db, line_id="l2", kind="question", **where)
    await clear_mark(pg_db, line_id="l2", **where)
    await clear_mark(pg_db, line_id="never", **where)
    doc: BoardDoc = (await get_canvas(pg_db, learner_id=learner, canvas_id=canvas.canvas_id)).doc
    assert [(m.line_id, m.kind) for m in doc.teacher.marks] == [("l1", "error")]
    assert doc.learner == canvas.doc.learner

    task_id = canvas.task_id
    assert (
        await show_first_hint(
            pg_db, space=None, session_id=session_id, task_id=task_id, line_id="l1", **where
        )
        is None
    )
    for _ in range(2):
        text = await show_first_hint(
            pg_db, space=SPACE, session_id=session_id, task_id=task_id, line_id="l1", **where
        )
        assert text == SPACE.hints[0]
    [shown] = await events(pg_db, learner, "hint.shown")
    assert shown["payload"] == {"task_id": str(canvas.task_id), "step": 1, "source": "canvas"}
    doc = (await get_canvas(pg_db, learner_id=learner, canvas_id=canvas.canvas_id)).doc
    assert [(h.step, h.line_id, h.text) for h in doc.teacher.hints] == [(1, "l1", SPACE.hints[0])]


async def test_quiet_preference_and_finished_task(pg_db: Db) -> None:
    """The quiet mode is the latest `preference.set {key: canvas_quiet}`, default off; a task is
    finished by `task.closed` or by a final answer from the canvas."""
    learner = await create_learner(pg_db)
    assert await canvas_quiet(pg_db, learner_id=learner) is False

    async def prefer(value: bool, key: str) -> None:
        await append_events(
            pg_db,
            learner,
            [
                NewEvent(
                    type="preference.set",
                    payload={"key": "canvas_quiet", "value": value},
                    actor="learner",
                    idempotency_key=key,
                )
            ],
        )

    await prefer(True, "q1")
    assert await canvas_quiet(pg_db, learner_id=learner) is True
    await prefer(False, "q2")
    assert await canvas_quiet(pg_db, learner_id=learner) is False

    _, canvas = await make_canvas(pg_db, learner)
    assert canvas.task_id is not None
    assert await task_finished(pg_db, learner_id=learner, task_id=canvas.task_id) is False
    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="task.closed",
                payload={"task_id": str(canvas.task_id), "how": "solved"},
                actor="code",
                idempotency_key="closed",
            )
        ],
    )
    assert await task_finished(pg_db, learner_id=learner, task_id=canvas.task_id) is True
