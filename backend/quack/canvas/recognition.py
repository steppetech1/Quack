"""Recognition of canvas lines and the `recognizer@1` schema (ТЗ Ф4 WP21.3, S8; Т-§15.3, Т-§22.7).

The API renders the strokes of a finished line into an image and sends it to `recognizer` (Spark
with an image, class P0, called by the WebSocket handler directly, without a queue). The model
reads the line: LaTeX or text (`canvas.line_committed.kind`, Q-A-3), SymPy where the line is
math (`Т-§22.7`), its confidence and whether the line is crossed out. The schema lives in the
package that calls the role (§7.2 S8); `tests/llm/prompt_contracts.py` registers it.
"""

from __future__ import annotations

import base64
import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from quack.canvas.strokes import Stroke, StrokeBatch
from quack.compute_client.client import ComputeClient, ComputeUnavailableError
from quack.compute_client.contracts import RenderStrokesRequest
from quack.compute_client.contracts import Stroke as RenderStroke
from quack.db import Db
from quack.events.errors import IdempotencyConflictError
from quack.events.event_types import CanvasLineCommitted, CanvasLineErased, HandwritingCorrected
from quack.events.log import NewEvent, append_events
from quack.llm.errors import LLMError
from quack.llm.gateway import LLMGateway
from quack.llm.provider import ImageInput
from quack.logging import get_logger

log = get_logger(__name__)


class RecognizerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["latex", "text"]
    text: str
    sympy: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    crossed_out: bool


class SheetBox(BaseModel):
    """The approximate box of a sheet line as shares of the image, top-left origin."""

    model_config = ConfigDict(extra="forbid")

    left: float = Field(ge=0.0, le=1.0)
    top: float = Field(ge=0.0, le=1.0)
    right: float = Field(ge=0.0, le=1.0)
    bottom: float = Field(ge=0.0, le=1.0)


class SheetLine(BaseModel):
    """A line of a sheet photo (Т-§22.6 п.2): what `RecognizerOutput` gives for a pen line, plus
    `drawing` as a kind and the approximate box as shares of the image, top-left origin
    (D-C-134)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["latex", "text", "drawing"]
    text: str
    sympy: str | None
    bbox: SheetBox
    confidence: float = Field(ge=0.0, le=1.0)
    crossed_out: bool


class RecognizerSheetOutput(BaseModel):
    """`recognizer@1` in the sheet mode: every line of the photo in one call, top to bottom
    (ТЗ Ф4 WP22.3 п.2; a prompt with two modes has one schema per mode, D-B-140)."""

    model_config = ConfigDict(extra="forbid")

    lines: list[SheetLine]


# --- recognition of a pen line (WP21.3, D-C-131) ---------------------------------------------

RECOGNIZER_ROLE = "recognizer"
RECOGNIZER_VERSION = 1
# compute `/v1/image/render-strokes` answers WebP (WP24); `ImageInput` takes it (D-B-170).
# Whether Spark reads WebP — [ПРОВЕРИТЬ] on the stand (DoD п.6a), PNG otherwise.
RENDER_MIME = "image/webp"
# The two modes of `recognizer@1` (D-C-134): one pen line, or a whole sheet photo (WP22.3).
LINE_MODE = "line"
SHEET_MODE = "sheet"

_EVENT_BY_KEY = (
    "SELECT payload FROM core.events "
    "WHERE learner_id = %(learner_id)s AND idempotency_key = %(key)s"
)


@dataclass(frozen=True)
class LineStrokes:
    """A finished line from `line_ready` with the strokes of this socket it names."""

    line_id: str
    bbox: tuple[float, float, float, float]
    started_at_ms: int
    ended_at_ms: int
    strokes: tuple[Stroke, ...]


class LineTooLarge(ValueError):
    """The box of the points of a line is not an image compute may draw."""


def render_request(strokes: Sequence[Stroke], max_side_px: int) -> RenderStrokesRequest:
    """The strokes moved to the corner of their own box: the image is the line only. The box
    comes from the points, not from the client `bbox`, whose convention the documents do not
    fix (D-C-131). The coordinates are the client's: a box with a side past `max_side_px` (or
    with a coordinate that is not a number) is `LineTooLarge`, a stroke without points is
    dropped."""
    strokes = [stroke for stroke in strokes if stroke.points]
    points = [p for stroke in strokes for p in stroke.points]
    if not points:
        raise LineTooLarge("the line has no points")
    if not all(math.isfinite(c) for p in points for c in (p.x, p.y)):
        raise LineTooLarge("a coordinate is not a finite number")
    x0, y0 = min(p.x for p in points), min(p.y for p in points)
    x1, y1 = max(p.x for p in points), max(p.y for p in points)
    width, height = math.ceil(x1 - x0) + 1, math.ceil(y1 - y0) + 1
    if max(width, height) > max_side_px:
        raise LineTooLarge(f"the line is {width} x {height}, the limit is {max_side_px}")
    return RenderStrokesRequest(
        strokes=[
            RenderStroke(points=[(p.x - x0, p.y - y0) for p in stroke.points]) for stroke in strokes
        ],
        width=width,
        height=height,
    )


async def recognize_line(
    db: Db,
    gateway: LLMGateway,
    compute: ComputeClient,
    *,
    learner_id: UUID,
    session_id: UUID | None,
    canvas_id: UUID,
    line: LineStrokes,
    max_side_px: int,
    redis: Redis | None = None,
) -> CanvasLineCommitted | None:
    """Render the line, read it with `recognizer@1` and write `canvas.line_committed {source:
    pen}`; `None` — the line has no strokes of this socket, it is too large to draw
    (`max_side_px`), or compute or the model is down (nothing is written, the client may send
    `line_ready` again). A repeated `line_ready` writes nothing new: the event key is the line
    (law 5), and a reading that differs from the written one (another model answer, another
    set of strokes) is answered with the written line, not with a conflict."""
    key = f"canvas.line_committed:{canvas_id}:{line.line_id}"
    try:
        request = render_request(line.strokes, max_side_px)
    except LineTooLarge as exc:
        log.warning("line_not_drawn", canvas_id=str(canvas_id), reason=str(exc))
        return None
    try:
        image = await compute.image_render_strokes(request)
        result = await gateway.generate_structured(
            RECOGNIZER_ROLE,
            RECOGNIZER_VERSION,
            RecognizerOutput,
            {"mode": LINE_MODE},
            learner_id,
            session_id,
            None,
            f"recognizer:{key}",
            images=[ImageInput(RENDER_MIME, base64.b64decode(image.image_webp_b64))],
        )
    except (ComputeUnavailableError, LLMError) as exc:
        log.warning("line_recognition_failed", canvas_id=str(canvas_id), error=repr(exc))
        return None
    read = result.value
    payload = CanvasLineCommitted(
        canvas_id=canvas_id,
        line_id=line.line_id,
        text=read.text,
        sympy=read.sympy,
        kind=read.kind,
        bbox=line.bbox,
        stroke_ids=[stroke.stroke_id for stroke in line.strokes],
        started_at_ms=line.started_at_ms,
        ended_at_ms=line.ended_at_ms,
        source="pen",
        recognition_confidence=read.confidence,
        crossed_out=read.crossed_out,
    )
    try:
        await append_events(
            db,
            learner_id,
            [
                NewEvent(
                    type="canvas.line_committed",
                    payload=payload,
                    actor=f"{RECOGNIZER_ROLE}@{RECOGNIZER_VERSION}",
                    idempotency_key=key,
                    session_id=session_id,
                )
            ],
            redis=redis,
        )
    except IdempotencyConflictError:
        written = await db.fetch_one(_EVENT_BY_KEY, {"learner_id": learner_id, "key": key})
        if written is None:  # pragma: no cover - the conflict is the proof it exists
            raise
        return CanvasLineCommitted.model_validate(written["payload"])
    return payload


def line_recognized(line_id: str, line: CanvasLineCommitted | None) -> dict[str, Any]:
    """The server message `line_recognized` (WP21.2): how the line was read (П-9.7 «касание
    строки показывает, как она прочитана»), or `recognized: false` (D-C-131)."""
    if line is None:
        return {"type": "line_recognized", "line_id": line_id, "recognized": False}
    return {
        "type": "line_recognized",
        "line_id": line_id,
        "recognized": True,
        "kind": line.kind,
        "text": line.text,
        "sympy": line.sympy,
        "confidence": line.recognition_confidence,
        "crossed_out": line.crossed_out,
    }


async def commit_typed(
    db: Db,
    *,
    learner_id: UUID,
    session_id: UUID | None,
    canvas_id: UUID,
    block_id: str,
    latex: str,
    at_ms: int,
    redis: Redis | None = None,
) -> CanvasLineCommitted:
    """`typed_formula`: LaTeX straight into the log, without recognition (`source: typed`,
    Т-§15.3). The line is the typing block; each new text of the block is a new line event
    (the key holds the text), the same text again writes nothing (D-C-131)."""
    payload = CanvasLineCommitted(
        canvas_id=canvas_id,
        line_id=block_id,
        text=latex,
        kind="latex",
        bbox=(0.0, 0.0, 0.0, 0.0),
        stroke_ids=[],
        started_at_ms=at_ms,
        ended_at_ms=at_ms,
        source="typed",
        recognition_confidence=1.0,
        crossed_out=False,
    )
    digest = hashlib.sha256(latex.encode()).hexdigest()[:16]
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="canvas.line_committed",
                payload=payload,
                actor="learner",
                idempotency_key=f"canvas.line_committed:{canvas_id}:{block_id}:{digest}",
                session_id=session_id,
            )
        ],
        redis=redis,
    )
    return payload


async def erase_line(
    db: Db,
    *,
    learner_id: UUID,
    session_id: UUID | None,
    canvas_id: UUID,
    line_id: str,
    text: str,
    redis: Redis | None = None,
) -> None:
    """`canvas.line_erased {canvas_id, line_id, text}`: every stroke of a recognized line is
    erased (Т-§15.1 — the erasure stays in the stroke log, the line leaves the solution)."""
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="canvas.line_erased",
                payload=CanvasLineErased(canvas_id=canvas_id, line_id=line_id, text=text),
                actor="learner",
                idempotency_key=f"canvas.line_erased:{canvas_id}:{line_id}",
                session_id=session_id,
            )
        ],
        redis=redis,
    )


# --- correction of a reading (ТЗ Ф4 WP21.3, Т-§2.8, D-C-137) ---------------------------------

_LINE_OF_CANVAS = (
    "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'canvas.line_committed' AND payload ->> 'canvas_id' = %(canvas_id)s "
    "AND payload ->> 'line_id' = %(line_id)s LIMIT 1"
)


class LineNotFound(Exception):
    """The canvas has no recognized line with this id."""


async def correct_line(
    db: Db,
    *,
    learner_id: UUID,
    session_id: UUID | None,
    canvas_id: UUID,
    line_id: str,
    text: str,
    sympy: str | None,
    idempotency_key: str,
    redis: Redis | None = None,
) -> HandwritingCorrected:
    """`handwriting.corrected {canvas_id, line_id, text, sympy}`: the learner corrects how a line
    was read (П-9.7); the dispatcher queues `canvas.rediagnose` (stream A). Only a line the log
    has for this canvas can be corrected; the key is the client's (law 5): the same key with
    another text is `IdempotencyConflictError` (409)."""
    found = await db.fetch_one(
        _LINE_OF_CANVAS,
        {"learner_id": learner_id, "canvas_id": str(canvas_id), "line_id": line_id},
    )
    if found is None:
        raise LineNotFound(f"line {line_id} not found on canvas {canvas_id}")
    payload = HandwritingCorrected(canvas_id=canvas_id, line_id=line_id, text=text, sympy=sympy)
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="handwriting.corrected",
                payload=payload,
                actor="learner",
                idempotency_key=f"handwriting.corrected:{idempotency_key}",
                session_id=session_id,
            )
        ],
        redis=redis,
    )
    return payload


class LineBook:
    """The strokes this socket received and the lines recognized from them: which strokes a
    `line_ready` names, and which recognized lines an erasure removes entirely."""

    def __init__(self) -> None:
        self._strokes: dict[str, Stroke] = {}
        self._lines: dict[str, tuple[frozenset[str], str]] = {}
        self._erased: set[str] = set()
        # The chunks of the earlier sockets of the canvas are read once (`adopt`).
        self.history_loaded = False

    def add(self, batch: StrokeBatch) -> list[tuple[str, str]]:
        """Take the batch in; `(line_id, text)` of recognized lines it erased entirely."""
        for stroke in batch.strokes:
            self._strokes[stroke.stroke_id] = stroke
        for erasure in batch.erasures:
            self._erased.update(erasure.stroke_ids)
        gone = [
            (line_id, text)
            for line_id, (ids, text) in self._lines.items()
            if ids and ids <= self._erased
        ]
        for line_id, _ in gone:
            del self._lines[line_id]
        return gone

    def erasure_boxes(self, batch: StrokeBatch) -> list[tuple[float, float, float, float]]:
        """The box of the strokes each erasure of the batch removed (strokes this socket knows);
        where the learner erases the same place again and again, they are stuck (WP21.5)."""
        boxes: list[tuple[float, float, float, float]] = []
        for erasure in batch.erasures:
            points = [
                p
                for stroke_id in erasure.stroke_ids
                if (stroke := self._strokes.get(stroke_id)) is not None
                for p in stroke.points
            ]
            if points:
                xs, ys = [p.x for p in points], [p.y for p in points]
                boxes.append((min(xs), min(ys), max(xs), max(ys)))
        return boxes

    def line(
        self,
        line_id: str,
        stroke_ids: Sequence[str],
        bbox: tuple[float, float, float, float],
        started_at_ms: int,
        ended_at_ms: int,
    ) -> LineStrokes:
        strokes = tuple(
            self._strokes[i]
            for i in stroke_ids
            if i in self._strokes and i not in self._erased and self._strokes[i].points
        )
        return LineStrokes(line_id, bbox, started_at_ms, ended_at_ms, strokes)

    def missing(self, stroke_ids: Sequence[str]) -> bool:
        """Does a line name a stroke this socket has not seen (it was sent before a reconnect)?"""
        return any(i not in self._strokes for i in stroke_ids)

    def adopt(self, strokes: dict[str, Stroke], erased: set[str]) -> None:
        """The strokes and erasures of the earlier sockets of the canvas (`load_journal`);
        what this socket received itself stays as it is."""
        for stroke_id, stroke in strokes.items():
            self._strokes.setdefault(stroke_id, stroke)
        self._erased.update(erased)
        self.history_loaded = True

    def recognized(self, line: CanvasLineCommitted) -> bool:
        """Take a recognized line in; `False` - every stroke it was read from was erased while
        the model read it, the line is gone and stays out (the caller writes the erasure)."""
        ids = frozenset(line.stroke_ids)
        if ids and ids <= self._erased:
            return False
        self._lines[line.line_id] = (ids, line.text)
        return True
