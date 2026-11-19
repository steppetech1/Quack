"""Photo pipeline of a canvas (ТЗ Ф4 WP22.3; Т-§22.6, Т-§16.3).

`photo.uploaded` queues `photo.pipeline` (P1, `lock_key = canvas:{id}`, stream A). The job:

1. aligns the perspective by the sheet contour — compute `/v1/image/align`; no contour, the
   photo goes as is;
2. reads every line of the sheet with `recognizer@1` in the sheet mode, in one call, top to
   bottom (D-C-134);
3. separates new lines from those already written by aligning the two sequences on the
   normalized Levenshtein distance between LaTeX strings: a pair matches below
   `sheet_line_match_threshold`. Only new and changed lines go to the log
   (`canvas.line_committed {source: photo, photo_id, bbox}`), newly crossed-out ones — as
   `canvas.line_erased`;
4. fewer than `new_sheet_match_share` of the lines of the last photo matched — a new sheet of
   the same solution: every line is new.

5. every area the recognizer marked as a drawing — and only those (T-CP3) — goes to `segmenter`
   (`sam-3.1`) by its box in pixels; compute `/v1/image/clean-drawing` removes the paper under
   the mask, the image is stored and laid into the learner layer of the board (D-C-153).

The line source is the whole photo with the approximate box of the line (`Т-§22.6`), so no crop
is stored.

The sheet the photo is compared with is read from the log: a photo line id is
`{sheet}:{photo}:{n}`, where `sheet` is the photo that opened the sheet (D-C-134). A repeated job
gives the same reading through the outbox, and every line of it then matches itself: nothing new
is written (law 5).
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from quack.canvas.board import LearnerDrawing
from quack.canvas.boards import Canvas, add_drawing, get_canvas
from quack.canvas.channel import publish_canvas
from quack.canvas.drawings import drawing_mask, pixel_box, webp_size
from quack.canvas.recognition import (
    RECOGNIZER_ROLE,
    RECOGNIZER_VERSION,
    SHEET_MODE,
    RecognizerSheetOutput,
    SheetLine,
    line_recognized,
)
from quack.compute_client.client import ComputeClient
from quack.compute_client.contracts import CleanDrawingRequest, ImageRequest
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import CanvasLineCommitted, CanvasLineErased, PhotoUploaded
from quack.events.log import NewEvent, append_events
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.llm.gateway import LLMGateway
from quack.llm.provider import ImageInput
from quack.logging import get_logger
from quack.storage.keys import photo_drawing_key
from quack.storage.objects import ObjectStore

log = get_logger(__name__)

# compute `/v1/image/align` answers WebP (WP24), like the photo itself (`Т-§22.6`).
SHEET_MIME = "image/webp"

_PHOTO = (
    "SELECT payload, occurred_at FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'photo.uploaded' AND payload ->> 'photo_id' = %(photo_id)s LIMIT 1"
)
_PHOTO_LINES = (
    "SELECT type, payload FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type IN ('canvas.line_committed', 'canvas.line_erased') "
    "AND payload ->> 'canvas_id' = %(canvas_id)s "
    "AND (type = 'canvas.line_erased' OR payload ->> 'source' = 'photo') ORDER BY seq"
)


def normalized_levenshtein(a: str, b: str) -> float:
    """Edit distance over the longer length: 0 — the same string, 1 — nothing in common."""
    if not a and not b:
        return 0.0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1] / max(len(a), len(b))


def align_lines(
    previous: Sequence[str], current: Sequence[str], threshold: float
) -> dict[int, int]:
    """The order-keeping alignment with the most matched pairs: index in `current` → index in
    `previous`; a pair can match only below `threshold`, ties go to the closer pair."""
    rows, cols = len(previous), len(current)
    # score[i][j] for the suffixes previous[i:], current[j:]: (matches, -total distance).
    score = [[(0, 0.0)] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows - 1, -1, -1):
        for j in range(cols - 1, -1, -1):
            best = max(score[i + 1][j], score[i][j + 1])
            distance = normalized_levenshtein(previous[i], current[j])
            if distance < threshold:
                matches, total = score[i + 1][j + 1]
                best = max(best, (matches + 1, total - distance))
            score[i][j] = best
    pairs: dict[int, int] = {}
    i = j = 0
    while i < rows and j < cols:
        distance = normalized_levenshtein(previous[i], current[j])
        if distance < threshold:
            matches, total = score[i + 1][j + 1]
            if score[i][j] == (matches + 1, total - distance):
                pairs[j] = i
                i, j = i + 1, j + 1
                continue
        if score[i][j] == score[i + 1][j]:
            i += 1
        else:
            j += 1
    return pairs


@dataclass(frozen=True)
class SheetRow:
    """A line of the current sheet as the log has it: the last reading and whether it is
    crossed out."""

    line_id: str
    text: str
    crossed_out: bool
    top: float


@dataclass(frozen=True)
class Sheet:
    sheet_id: str | None
    rows: list[SheetRow]


async def current_sheet(db: Db, *, learner_id: UUID, canvas_id: UUID) -> Sheet:
    """The lines of the last sheet of the canvas, top to bottom (D-C-134)."""
    found = await db.fetch_all(
        _PHOTO_LINES, {"learner_id": learner_id, "canvas_id": str(canvas_id)}
    )
    committed: dict[str, dict[str, object]] = {}
    erased: set[str] = set()
    sheet_id: str | None = None
    for row in found:
        payload = row["payload"]
        if row["type"] == "canvas.line_erased":
            erased.add(payload["line_id"])
            continue
        committed[payload["line_id"]] = payload
        erased.discard(payload["line_id"])  # written again after it was struck out
        sheet_id = payload["line_id"].split(":", 1)[0]
    rows = [
        SheetRow(
            line_id=line_id,
            text=str(payload["text"]),
            crossed_out=bool(payload["crossed_out"]) or line_id in erased,
            top=float(payload["bbox"][1]),  # type: ignore[index]
        )
        for line_id, payload in committed.items()
        if line_id.split(":", 1)[0] == sheet_id
    ]
    rows.sort(key=lambda row: row.top)
    return Sheet(sheet_id=sheet_id, rows=rows)


@dataclass(frozen=True)
class PhotoResult:
    """What the job wrote: the ids of committed and erased lines, whether the photo opened a
    new sheet, how many drawings the sheet has and the ids of those laid on the board now."""

    committed: list[str]
    erased: list[str]
    new_sheet: bool
    drawings: int
    drawings_placed: list[str]


async def run_photo_pipeline(
    db: Db,
    gateway: LLMGateway,
    compute: ComputeClient,
    storage: ObjectStore,
    settings: Settings,
    *,
    learner_id: UUID,
    photo_id: UUID,
    redis: Redis | None = None,
) -> PhotoResult | None:
    """Steps 1–5 of WP22.3 for one photo; `None` — the photo has no canvas (WP22.4). Errors of
    compute and of the model propagate: the job is retried (§4.2)."""
    row = await db.fetch_one(_PHOTO, {"learner_id": learner_id, "photo_id": str(photo_id)})
    if row is None:
        raise LookupError(f"photo.uploaded {photo_id} is not in the log of the learner")
    photo = PhotoUploaded.model_validate(row["payload"])
    if photo.canvas_id is None:
        log.info("photo_without_canvas", photo_id=str(photo_id))
        return None
    canvas = await get_canvas(db, learner_id=learner_id, canvas_id=photo.canvas_id)
    original = await storage.get(photo.object_key)
    aligned = await compute.image_align(ImageRequest(image_b64=base64.b64encode(original).decode()))
    sheet_image = base64.b64decode(aligned.image_webp_b64)
    reading = await gateway.generate_structured(
        RECOGNIZER_ROLE,
        RECOGNIZER_VERSION,
        RecognizerSheetOutput,
        {"mode": SHEET_MODE},
        learner_id,
        canvas.session_id,
        None,
        f"recognizer:photo:{photo_id}",
        images=[ImageInput(SHEET_MIME, sheet_image)],
    )
    lines = [line for line in reading.value.lines if line.kind != "drawing"]
    areas = [line for line in reading.value.lines if line.kind == "drawing"]

    sheet = await current_sheet(db, learner_id=learner_id, canvas_id=photo.canvas_id)
    pairs = align_lines(
        [r.text for r in sheet.rows], [line.text for line in lines],
        settings.sheet_line_match_threshold,
    )  # fmt: skip
    new_sheet = bool(sheet.rows) and len(pairs) < settings.new_sheet_match_share * len(sheet.rows)
    if new_sheet or sheet.sheet_id is None:
        pairs, sheet_id = {}, str(photo_id)
    else:
        sheet_id = sheet.sheet_id

    occurred_at: datetime = row["occurred_at"]
    at_ms = int(occurred_at.timestamp() * 1000)
    events: list[NewEvent] = []
    committed: list[str] = []
    messages: list[dict[str, Any]] = []
    erased: list[str] = []
    for n, line in enumerate(lines):
        before = sheet.rows[pairs[n]] if n in pairs else None
        line_id = before.line_id if before else f"{sheet_id}:{photo_id}:{n}"
        if before is None or before.text != line.text:
            committed.append(line_id)
            event = _committed(photo.canvas_id, photo_id, line_id, line, at_ms, canvas.session_id)
            events.append(event)
            assert isinstance(event.payload, CanvasLineCommitted)
            messages.append({**line_recognized(line_id, event.payload), "source": "photo"})
        if line.crossed_out and (before is None or not before.crossed_out):
            erased.append(line_id)
            events.append(
                NewEvent(
                    type="canvas.line_erased",
                    payload=CanvasLineErased(
                        canvas_id=photo.canvas_id, line_id=line_id, text=line.text
                    ),
                    actor="learner",
                    # A line struck out, written again and struck out again is erased again.
                    idempotency_key=f"canvas.line_erased:{photo.canvas_id}:{line_id}:{photo_id}",
                    session_id=canvas.session_id,
                )
            )
    if events:
        await append_events(db, learner_id, events, redis=redis)
    if redis is not None:
        for message in messages:
            await _tell_canvas(redis, photo.canvas_id, message)
    placed = await _place_drawings(
        db, gateway, compute, storage,
        learner_id=learner_id, canvas=canvas, photo_id=photo_id, image=sheet_image, areas=areas,
    )  # fmt: skip
    return PhotoResult(
        committed=committed,
        erased=erased,
        new_sheet=new_sheet,
        drawings=len(areas),
        drawings_placed=placed,
    )


async def _tell_canvas(redis: Redis, canvas_id: UUID, message: dict[str, Any]) -> None:
    """A new line of the photo to the devices of the canvas: the notebook shows the reading and
    its socket checks the line like one of the pen (WP22.3 "as for the pen"). The log has the
    line already, so a Redis failure is logged, not retried."""
    try:
        await publish_canvas(redis, canvas_id, message)
    except RedisError as exc:
        log.warning("photo_line_publish_failed", canvas_id=str(canvas_id), error=str(exc))


async def _place_drawings(
    db: Db,
    gateway: LLMGateway,
    compute: ComputeClient,
    storage: ObjectStore,
    *,
    learner_id: UUID,
    canvas: Canvas,
    photo_id: UUID,
    image: bytes,
    areas: Sequence[SheetLine],
) -> list[str]:
    """Step 5: one `segmenter` call per drawing area, `clean-drawing` under the mask, the image
    to the store, a record in the learner layer. A drawing already on the board is not touched
    (a repeated job calls nothing). The photo whose size the WebP header does not give has no
    box in pixels: its drawings are skipped and logged (D-C-153). Errors propagate: the job is
    retried (§4.2)."""
    if not areas:
        return []
    size = webp_size(image)
    if size is None:
        log.info("photo_drawings_skipped", photo_id=str(photo_id), drawings=len(areas))
        return []
    known = {d.drawing_id for d in canvas.doc.learner.drawings}
    placed: list[str] = []
    for n, area in enumerate(areas):
        drawing_id = f"{photo_id}:{n}"
        box = pixel_box(area.bbox, size)
        if drawing_id in known:
            continue
        if box.x2 <= box.x1 or box.y2 <= box.y1:
            log.info("photo_drawing_empty_box", photo_id=str(photo_id), drawing=n)
            continue
        found = await gateway.segment(
            ImageInput(SHEET_MIME, image),
            learner_id=learner_id,
            session_id=canvas.session_id,
            box=box,
        )
        objects = found.value.objects
        chosen = next((o for o in objects if o.mask is not None), objects[0] if objects else None)
        cleaned = await compute.image_clean_drawing(
            CleanDrawingRequest(
                image_b64=base64.b64encode(image).decode(),
                mask_b64=drawing_mask(chosen, box, size),
            )
        )
        key = photo_drawing_key(learner_id, photo_id, n)
        await storage.put(key, base64.b64decode(cleaned.image_webp_b64), SHEET_MIME)
        drawing = LearnerDrawing(
            drawing_id=drawing_id,
            photo_id=photo_id,
            object_key=key,
            bbox=(area.bbox.left, area.bbox.top, area.bbox.right, area.bbox.bottom),
        )
        if await add_drawing(
            db, learner_id=learner_id, canvas_id=canvas.canvas_id, drawing=drawing
        ):
            placed.append(drawing_id)
    return placed


def _committed(
    canvas_id: UUID,
    photo_id: UUID,
    line_id: str,
    line: SheetLine,
    at_ms: int,
    session_id: UUID | None,
) -> NewEvent:
    return NewEvent(
        type="canvas.line_committed",
        payload=CanvasLineCommitted(
            canvas_id=canvas_id,
            line_id=line_id,
            text=line.text,
            sympy=line.sympy,
            kind="text" if line.kind == "text" else "latex",
            bbox=(line.bbox.left, line.bbox.top, line.bbox.right, line.bbox.bottom),
            stroke_ids=[],
            started_at_ms=at_ms,
            ended_at_ms=at_ms,
            source="photo",
            photo_id=photo_id,
            recognition_confidence=line.confidence,
            crossed_out=line.crossed_out,
        ),
        actor=f"{RECOGNIZER_ROLE}@{RECOGNIZER_VERSION}",
        # A changed line keeps its id; the photo tells its versions apart.
        idempotency_key=f"canvas.line_committed:{canvas_id}:{line_id}:{photo_id}",
        session_id=session_id,
    )


@register_job_handler("photo.pipeline")
async def _photo_pipeline_handler(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None:
        raise ValueError("photo.pipeline without a learner")
    await run_photo_pipeline(
        ctx.db, ctx.llm, ctx.compute, ctx.storage, ctx.settings,
        learner_id=job.learner_id, photo_id=UUID(job.payload["photo_id"]), redis=ctx.redis,
    )  # fmt: skip
