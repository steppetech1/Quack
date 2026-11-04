"""The document of a board, `content.canvas_boards.doc` (ТЗ Ф4 WP21.1; Т-§15.1, П-9.4, П-9.5).

Blocks with their layout, the learner layer and the teacher layer as **different structures**:
the teacher never writes into the learner layer, the learner's eraser never reaches the teacher
layer (П-9.4). Strokes are not here — they live in the stroke journal (WP21.2, Т-§15.1);
recognized lines are events (`canvas.line_committed`). The version is the row's `version`.
Draft of D-C-118 for stream B's review (B #103: «схему задаёт C и присылает B на ревью»).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Layout(_Strict):
    """Where a block lies and whether it is collapsed (П-9.4 «двигать, сворачивать»)."""

    x: float = 0
    y: float = 0
    width: float | None = None
    height: float | None = None
    collapsed: bool = False


class Block(_Strict):
    """П-9.4: task (condition and room for the solution), visualization, hint, reference card."""

    block_id: str
    kind: Literal["task", "visualization", "hint", "reference_card"]
    task_id: UUID | None = None
    layout: Layout = Field(default_factory=Layout)


class TypedFormula(_Strict):
    """A formula typed in a typing block (`typed_formula {block_id, latex}`, WP21.2)."""

    block_id: str
    latex: str


class LearnerDrawing(_Strict):
    """A drawing cut from a sheet photo (WP22.3 step 5; Т-§16.3 п.4, D-C-153): the image without
    its paper in the store and the box of the area as shares of the sheet."""

    drawing_id: str  # `{photo_id}:{n}`, n — the number of the drawing among those of the photo
    photo_id: UUID
    object_key: str
    bbox: tuple[float, float, float, float]


class LearnerLayer(_Strict):
    """The learner's strokes are in the journal; here — what the learner typed and the drawings
    cut from the photos of the sheet."""

    typed_formulas: list[TypedFormula] = Field(default_factory=list)
    drawings: list[LearnerDrawing] = Field(default_factory=list)


class Mark(_Strict):
    """A mark on a step (WP21.5): `error` confirmed, `question` when unsure (Т-§12.4)."""

    mark_id: str
    line_id: str
    kind: Literal["error", "question"]
    text: str


class Hint(_Strict):
    """A quiet hint near the place where the learner is stuck, one step at a time (П-9.5)."""

    hint_id: str
    task_id: UUID
    step: int = Field(ge=1, le=3)
    text: str
    line_id: str | None = None


class Visualization(_Strict):
    """A visualization laid next to the solution (П-9.5; artifact `Т-§13.6`)."""

    item_id: str
    block_id: str
    artifact_id: UUID


class Scaffold(_Strict):
    """A scaffold of the solution with gaps the learner fills with the pen (П-9.5)."""

    item_id: str
    block_id: str
    text: str


class TeacherLayer(_Strict):
    marks: list[Mark] = Field(default_factory=list)
    hints: list[Hint] = Field(default_factory=list)
    visualizations: list[Visualization] = Field(default_factory=list)
    scaffolds: list[Scaffold] = Field(default_factory=list)


class BoardDoc(_Strict):
    blocks: list[Block] = Field(default_factory=list)
    learner: LearnerLayer = Field(default_factory=LearnerLayer)
    teacher: TeacherLayer = Field(default_factory=TeacherLayer)

    @classmethod
    def empty(cls, *, task_id: UUID | None) -> BoardDoc:
        """A new board; opened by a task — one task block (П-9.3 «задача переносится блоком»)."""
        blocks = [] if task_id is None else [Block(block_id="b1", kind="task", task_id=task_id)]
        return cls(blocks=blocks)
