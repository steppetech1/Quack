"""Shared setup of the step-check tests, WP21.4-21.5: a learner, a task with a solution space, a
canvas opened by the task and lines in its log (ТЗ Ф4 WP21.4, WP21.5)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from quack.canvas.boards import Canvas, open_canvas
from quack.compute_client.math_contracts import TransitionResponse
from quack.db import Db
from quack.events import records
from quack.events.event_types import CanvasLineCommitted
from quack.events.log import NewEvent, append_events
from quack.events.values import AnswerSpec, MathValue
from quack.practice.role_outputs import KeyStep, SolutionSpace, TaskCondition, WrongBranch
from quack.practice.task_store import create_task

TRANSITION = "/v1/math/transition"
HINTS = ["Look at the signs first.", "Collect like terms.", "Divide by the coefficient."]

SPACE = SolutionSpace(
    key_steps=[
        KeyStep(statement="move terms", expression=MathValue(latex="2x=4", sympy="Eq(2*x, 4)")),
        KeyStep(statement="divide", expression=MathValue(latex="x=2", sympy="Eq(x, 2)")),
        KeyStep(statement="check", expression=None),
    ],
    wrong_branches=[
        WrongBranch(sign="sign lost when moving a term", gap_type="slip", misconception=None),
        WrongBranch(sign="divides only one side", gap_type="execution", misconception=None),
    ],
    hints=HINTS,
)


def follows(value: bool | None, status: str = "completed") -> TransitionResponse:
    return TransitionResponse(status=status, follows=value)  # type: ignore[arg-type]


async def make_task(db: Db, learner: UUID, *, space: SolutionSpace | None = SPACE) -> UUID:
    task_id = uuid4()
    await create_task(
        db,
        learner_id=learner,
        task_id=task_id,
        family_id=uuid4(),
        source="seminar",
        condition=TaskCondition(text="Solve for x.", latex="2x+3=7"),
        markup=records.TaskMarkupRecord(
            tests=[records.TestedLevelRecord(skill_id=uuid4(), level=3)],
            method_named=False,
            answer=AnswerSpec(type="number", value=MathValue(latex="2", sympy="2")),
        ),
        verification="checked",
        actor="code",
        idempotency_key=f"task:{task_id}",
        solution_space=space,
        solution_space_version="solution_space@1" if space else None,
    )
    return task_id


async def make_canvas(db: Db, learner: UUID, *, task: bool = True) -> tuple[UUID, Canvas]:
    """A session of the learner and a canvas in it (opened by a task of the learner)."""
    session_id = uuid4()
    started = NewEvent(
        type="session.started",
        payload={"session_id": str(session_id), "domain_id": str(uuid4()), "intent": "learn"},
        actor="learner",
        idempotency_key=str(uuid4()),
    )
    await append_events(db, learner, [started])
    task_id = await make_task(db, learner) if task else None
    canvas = await open_canvas(db, learner_id=learner, session_id=session_id, task_id=task_id)
    return session_id, canvas


def line(canvas_id: UUID, line_id: str, text: str, **fields: Any) -> CanvasLineCommitted:
    base: dict[str, Any] = {
        "canvas_id": canvas_id,
        "line_id": line_id,
        "text": text,
        "sympy": None,
        "kind": "latex",
        "bbox": (0.0, 0.0, 1.0, 1.0),
        "stroke_ids": [f"s-{line_id}"],
        "started_at_ms": 0,
        "ended_at_ms": 1,
        "source": "pen",
        "recognition_confidence": 0.95,
        "crossed_out": False,
    }
    return CanvasLineCommitted.model_validate(base | fields)


async def commit(db: Db, learner: UUID, session_id: UUID | None, row: CanvasLineCommitted) -> None:
    await append_events(
        db,
        learner,
        [
            NewEvent(
                type="canvas.line_committed",
                payload=row,
                actor="recognizer@1",
                idempotency_key=f"canvas.line_committed:{row.canvas_id}:{row.line_id}:{row.text}",
                session_id=session_id,
            )
        ],
    )


async def events(db: Db, learner: UUID, type_: str) -> list[Any]:
    return await db.fetch_all(
        "SELECT seq, payload, actor FROM core.events "
        "WHERE learner_id = %(learner_id)s AND type = %(type)s ORDER BY seq",
        {"learner_id": learner, "type": type_},
    )
