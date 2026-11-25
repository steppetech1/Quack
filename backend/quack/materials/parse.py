"""Parse of a material — the job `material.parse` (ТЗ Ф4 WP20.2; Т-§4.3, Т-§3.4.5, Т-§16.5,
Т-§13.4; D-C-200..D-C-203).

One job kind, two steps; the second is queued by the first. A third job, `material.seminar_tasks`,
is queued by the second (below).

1. **parse** (no lock — the heavy part, Т-§4.3). The file comes from the object store and goes to
   compute in the body of the request (`Д-§3.2`): a PDF to `/v1/pdf/pages`, a photo of notes to
   `/v1/image/align` and `recognizer` in the sheet mode. The text by pages goes to
   `content.material_pages`, the page images to the store. Code cuts the pages into chunks of
   about `material_chunk_chars`; `material_parser` marks them in parallel batches (mode `parse`:
   section, notation, explicit `builds_on` claims with quotes, deadlines, seminar tasks) and then
   builds a table of contents (mode `toc`, batched by `material_toc_max_chunks` with a final merge
   call when needed). The TOC candidates, chunk/task mappings, and chunk marks ride in the
   payload of the second step; no event is written yet.
2. **structure** (`lock_key = domain:{id}`, D-C-200). TOC candidates go to the structure writer
   (`resolve_material_structure`, stream B, behind the `StructureResolver` seam) to obtain
   `(topic_id, core_skill_id, skill_ids)` per candidate. Then, in one transaction:
   `chunk.indexed` for every chunk (linking `teaches` to the mapped candidate's core skill so
   material search keeps working, `uses = []`), syllabus hubs and deadlines if present,
   `material.parsed`, and the status of the row. The problems the parser found (`seminar_tasks`)
   do not wait here: once their candidates are resolved, the step queues `material.seminar_tasks`
   in the same transaction with `skill_id = core_skill_id`, `topic_id`, and `topic_skill_ids`
   (D-C-217). Model calls of the tasks must not hold the lock of the domain, nor fail the step
   and the structure it has built (decision of 2026-11-25).
3. **seminar tasks** (`lock_key = material:{id}:seminar_tasks`, no lock of the domain). The
   writer of seminar tasks (stream B, behind the `SeminarWriter` seam) makes a task per problem;
   the tasks already made are skipped, so a retry goes on from where the last attempt stopped.
   A failure of this job never touches the status of the material.

The text of a material is data (Т-§19.8): the prompt says so, and nothing in the output of the
parser is executed or trusted as a rule.
"""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from datetime import date as calendar_date
from typing import Any, Literal, Protocol, cast
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from quack.compute_client.client import ComputeRejected
from quack.compute_client.contracts import ImageRequest, PdfPagesRequest, PptxSlidesRequest
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import (
    ChunkIndexed,
    HubCreated,
    HubDeadlineSet,
    HubTopic,
    HubTopicsChanged,
    MaterialParsed,
)
from quack.events.log import NewEvent, append_events
from quack.graph.ids import QUACK_UUID_NS
from quack.jobs import queue_p4
from quack.jobs.rules_p4 import JobPlan
from quack.jobs.runner import JobContext
from quack.jobs.types import ClaimedJob, JobFailedPermanently
from quack.llm.provider import ImageInput
from quack.logging import get_logger
from quack.storage.keys import material_page_key

log = get_logger(__name__)

PARSER_ROLE = "material_parser"
RECOGNIZER_ROLE = "recognizer"
RECOGNIZER_VERSION = 2
SHEET_MODE = "sheet"
PAGE_MIME = "image/webp"
PDF_MIME = "application/pdf"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
STATUS_PARSED = "parsed"
# The text of the pages is stored and can be read by the teacher; the structure (topics, skills,
# chunks of the graph) is still being made, or could not be made (the graph work needs embeddings).
STATUS_READ = "read"
STATUS_FAILED = "failed"
STEP_STRUCTURE = "structure"
SEMINAR_TASKS_KIND = "material.seminar_tasks"
TOC_PREVIEW_CHARS = 200
ACTOR = "material_parser@2"

_MATERIAL = (
    "SELECT material_id, domain_id, session_id, object_key, mime, status, created_at "
    "FROM content.materials WHERE learner_id = %(learner_id)s AND material_id = %(material_id)s"
)
_PAGES = (
    "SELECT page, text FROM content.material_pages "
    "WHERE learner_id = %(learner_id)s AND material_id = %(material_id)s ORDER BY page"
)
_INSERT_PAGE = (
    "INSERT INTO content.material_pages (material_id, page, learner_id, text, image_key) "
    "VALUES (%(material_id)s, %(page)s, %(learner_id)s, %(text)s, %(image_key)s) "
    "ON CONFLICT (material_id, page) DO NOTHING"
)
_SET_STATUS = (
    "UPDATE content.materials SET status = %(status)s, "
    "pages_count = COALESCE(%(pages)s, pages_count) "
    "WHERE learner_id = %(learner_id)s AND material_id = %(material_id)s"
)
_PARAGRAPHS = re.compile(r"\n\s*\n")


# --- schemas of the roles that stream C calls (§7.2 S8) ---------------------------------------


class ParsedChunk(BaseModel):
    """Legacy `material_parser@1` chunk mark schema (kept for prompt_contracts / meta_smoke
    compatibility until `material_parser@2` supersedes it)."""

    model_config = ConfigDict(extra="forbid")

    n: int
    topic: str | None
    notation: list[str]
    teaches: list[str]
    uses: list[str]


class ParsedBuildsOn(BaseModel):
    """Explicit prerequisite concept stated in the material with a short verbatim quote."""

    model_config = ConfigDict(extra="forbid")

    concept: str
    quote: str


class ParsedChunkV2(BaseModel):
    """`material_parser@2` mode `parse` chunk mark: free-text section (or null if non-teaching),
    notation, and explicit `builds_on` claims."""

    model_config = ConfigDict(extra="forbid")

    n: int
    section: str | None
    notation: list[str]
    builds_on: list[ParsedBuildsOn]


ControlType = Literal["tasks", "colloquium_proofs", "oral_theory"]


class ParsedDeadline(BaseModel):
    """A control of a course the material names with its date (a syllabus). `date` is ISO 8601
    with the year, `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM`; a date the text gives without a year is
    not a deadline. `covers` are topics in the words of the material (D-C-213)."""

    model_config = ConfigDict(extra="forbid")

    label: str
    date: str
    control_type: ControlType
    covers: list[str]


class ParsedSeminarTask(BaseModel):
    """A problem of the material for the learner to solve (WP12.5, #271). Only what the text
    gives: the statement, the answer when the material has one, and the place: `chunk` is `P`
    and the number of the chunk in the input (`P3`), `page` its page. The markup of the task
    (skills, level, form of the check) is written by stream B, not here."""

    model_config = ConfigDict(extra="forbid")

    statement: str
    answer: str | None
    chunk: str
    page: int | None


class MaterialParserOutput(BaseModel):
    """Legacy `material_parser@1` output schema."""

    model_config = ConfigDict(extra="forbid")

    title: str
    chunks: list[ParsedChunk]
    deadlines: list[ParsedDeadline]
    seminar_tasks: list[ParsedSeminarTask]


class MaterialParserV2Output(BaseModel):
    """`material_parser@2` mode `parse` output: title, chunk marks (without teaches/uses),
    dated controls, and seminar problems."""

    model_config = ConfigDict(extra="forbid")

    title: str
    chunks: list[ParsedChunkV2]
    deadlines: list[ParsedDeadline]
    seminar_tasks: list[ParsedSeminarTask]


class TocCandidate(BaseModel):
    """One topic candidate produced by `material_parser@2` mode `toc`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    core_skill_name: str
    builds_on: list[ParsedBuildsOn]


class ChunkCandidateMap(BaseModel):
    """Mapping from chunk number `n` to 0-based index into `candidates` (or null)."""

    model_config = ConfigDict(extra="forbid")

    n: int
    candidate_index: int | None


class TaskCandidateMap(BaseModel):
    """Mapping from 0-based `task_index` to 0-based index into `candidates` (or null)."""

    model_config = ConfigDict(extra="forbid")

    task_index: int
    candidate_index: int | None


class MaterialTocOutput(BaseModel):
    """`material_parser@2` mode `toc` output: topic candidates and chunk/task mappings."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[TocCandidate]
    chunk_candidates: list[ChunkCandidateMap]
    task_candidates: list[TaskCandidateMap]


class SheetBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: float = Field(ge=0.0, le=1.0)
    top: float = Field(ge=0.0, le=1.0)
    right: float = Field(ge=0.0, le=1.0)
    bottom: float = Field(ge=0.0, le=1.0)


class SheetLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["latex", "text", "drawing"]
    text: str
    sympy: str | None
    bbox: SheetBox
    confidence: float = Field(ge=0.0, le=1.0)
    crossed_out: bool


class SheetReading(BaseModel):
    """`recognizer@2` in the sheet mode as the parse of a photo of notes reads it. A copy of
    `quack.canvas.recognition.RecognizerSheetOutput`: product packages do not import each other
    (§4.1); a test keeps the two equal (D-C-203)."""

    model_config = ConfigDict(extra="forbid")

    lines: list[SheetLine]


# --- chunks -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    chunk_id: UUID
    page: int
    ordinal: int
    text: str


def _cut(text: str, size: int) -> list[str]:
    """Pieces of at most `size` characters, broken at a sentence end, a line end or a space."""
    pieces: list[str] = []
    while len(text) > size:
        window = text[:size]
        at = max(window.rfind(". "), window.rfind("\n"))
        if at < size // 2:
            at = window.rfind(" ")
        if at < size // 2:
            at = size - 1  # no place to break: a hard cut
        pieces.append(text[: at + 1].strip())
        text = text[at + 1 :].lstrip()
    if text:
        pieces.append(text)
    return [p for p in pieces if p]


def split_chunks(
    settings: Settings, material_id: UUID, pages: Sequence[tuple[int, str]]
) -> list[Chunk]:
    """Chunks of about `material_chunk_chars` on one page each, never above
    `material_chunk_max_chars` (Т-§3.4.5). Paragraphs are kept whole while they fit; a small
    chunk takes the next paragraph up to the maximum. The id depends on the material, the page
    and the place on it, so the same pages give the same chunks (law 5)."""
    target, ceiling = settings.material_chunk_chars, settings.material_chunk_max_chars
    chunks: list[Chunk] = []
    for page, text in pages:
        units = [
            piece
            for paragraph in _PARAGRAPHS.split(text.strip())
            for piece in _cut(paragraph.strip(), target)
        ]
        current = ""
        texts: list[str] = []
        for unit in units:
            joined = f"{current}\n\n{unit}" if current else unit
            if (
                not current
                or len(joined) <= target
                or (len(current) < target // 2 and len(joined) <= ceiling)
            ):
                current = joined
            else:
                texts.append(current)
                current = unit
        if current:
            texts.append(current)
        for ordinal, chunk_text in enumerate(texts):
            chunk_id = uuid5(QUACK_UUID_NS, f"chunk:{material_id}:{page}:{ordinal}")
            chunks.append(Chunk(chunk_id, page, ordinal, chunk_text))
    return chunks


# --- the row ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Material:
    material_id: UUID
    domain_id: UUID
    session_id: UUID | None
    object_key: str
    mime: str
    status: str
    created_at: datetime | None = None


def _learner(job: ClaimedJob) -> UUID:
    if job.learner_id is None:
        raise JobFailedPermanently("material.parse without a learner")
    return job.learner_id


async def _load(db: Db, learner_id: UUID, material_id: UUID) -> _Material:
    row = await db.fetch_one(_MATERIAL, {"learner_id": learner_id, "material_id": material_id})
    if row is None:
        raise JobFailedPermanently(f"material {material_id} not found")
    if row["domain_id"] is None:
        raise JobFailedPermanently(f"material {material_id} has no domain")
    return _Material(
        material_id=material_id,
        domain_id=row["domain_id"],
        session_id=row["session_id"],
        object_key=row["object_key"],
        mime=row["mime"],
        status=row["status"],
        created_at=row["created_at"],
    )


async def _stored_pages(db: Db, learner_id: UUID, material_id: UUID) -> list[tuple[int, str]]:
    rows = await db.fetch_all(_PAGES, {"learner_id": learner_id, "material_id": material_id})
    return [(r["page"], r["text"]) for r in rows]


def _clean_text(text: str) -> str:
    return text.replace("\x00", "")  # Postgres text does not hold NUL


async def _set_parsed(db: Db, learner_id: UUID, material_id: UUID, pages: int) -> None:
    await db.execute(
        _SET_STATUS,
        {
            "status": STATUS_PARSED,
            "pages": pages,
            "learner_id": learner_id,
            "material_id": material_id,
        },
    )


_SET_FAILED = (
    "UPDATE content.materials SET status = %(status)s WHERE learner_id = %(learner_id)s "
    "AND material_id = %(material_id)s AND status NOT IN (%(parsed)s, %(read)s)"
)
_SET_READ = (
    "UPDATE content.materials SET status = %(status)s, pages_count = %(pages)s "
    "WHERE learner_id = %(learner_id)s AND material_id = %(material_id)s "
    "AND status NOT IN (%(parsed)s)"
)


async def _set_read(db: Db, learner_id: UUID, material_id: UUID, pages: int) -> None:
    """The pages are stored: the material is readable now, whatever happens to its structure."""
    await db.execute(
        _SET_READ,
        {
            "status": STATUS_READ,
            "parsed": STATUS_PARSED,
            "pages": pages,
            "learner_id": learner_id,
            "material_id": material_id,
        },
    )


async def mark_failed(db: Db, job: ClaimedJob) -> None:
    """The parse of the material is over for good (a permanent error or the last attempt): the
    status is `failed`, so the client stops waiting. A parsed material keeps its status, and so
    does a material whose pages are read: its text is there for the teacher, only the graph work
    did not happen."""
    await mark_material_failed(db, job.learner_id, job.payload)


async def mark_material_failed(db: Db, learner_id: UUID | None, payload: dict[str, Any]) -> None:
    if learner_id is None or "material_id" not in payload:
        return
    await db.execute(
        _SET_FAILED,
        {
            "status": STATUS_FAILED,
            "parsed": STATUS_PARSED,
            "read": STATUS_READ,
            "learner_id": learner_id,
            "material_id": UUID(str(payload["material_id"])),
        },
    )


# --- step 1: pages, chunks, marks -------------------------------------------------------------


async def _read_sheet(
    ctx: JobContext, job: ClaimedJob, material: _Material, image: bytes, key: str
) -> str:
    """The text of a picture of a page by `recognizer` in the sheet mode: the lines that are not
    drawings and not crossed out, one per line."""
    reading = await ctx.llm.generate_structured(
        RECOGNIZER_ROLE,
        RECOGNIZER_VERSION,
        SheetReading,
        {"mode": SHEET_MODE},
        _learner(job),
        material.session_id,
        None,
        key,
        images=[ImageInput(PAGE_MIME, image)],
    )
    lines = [
        line.text.strip()
        for line in reading.value.lines
        if line.kind != "drawing" and not line.crossed_out and line.text.strip()
    ]
    return _clean_text("\n".join(lines))


async def _read_scans(
    ctx: JobContext,
    job: ClaimedJob,
    material: _Material,
    pages: list[tuple[int, str, bytes]],
) -> list[tuple[int, str, bytes]]:
    """PDF pages without a text layer (fewer than `material_scan_min_chars` characters) are read
    from their images, the first `material_scan_max_pages` of them; the rest stay empty
    (D-C-214). Parallel, `material_parser_concurrency` calls at a time."""
    settings = ctx.settings
    scans = [
        i
        for i, (_, text, _) in enumerate(pages)
        if len(text.strip()) < settings.material_scan_min_chars
    ]
    if len(scans) > settings.material_scan_max_pages:
        log.warning(
            "material_scan_truncated",
            material_id=str(material.material_id),
            scans=len(scans),
            read=settings.material_scan_max_pages,
        )
        scans = scans[: settings.material_scan_max_pages]
    gate = asyncio.Semaphore(settings.material_parser_concurrency)

    async def read(i: int) -> str:
        page, _, image = pages[i]
        async with gate:
            return await _read_sheet(
                ctx, job, material, image, f"recognizer:material:{material.material_id}:{page}"
            )

    texts = await asyncio.gather(*(read(i) for i in scans))
    out = list(pages)
    for i, text in zip(scans, texts, strict=True):
        out[i] = (out[i][0], text, out[i][2])
    return out


def slide_text(title: str, text: str, notes: str) -> str:
    """The text of a slide as a page: the title, the body, then the notes of the speaker."""
    parts = [title.strip(), text.strip()]
    if notes.strip():
        parts.append("Notes: " + notes.strip())
    return _clean_text("\n".join(p for p in parts if p))


async def _read_slides(ctx: JobContext, encoded: str) -> list[tuple[int, str, bytes]]:
    """The slides of a presentation as pages: text only (a slide has no page image here; the
    pictures of a slide are not read). A file compute refuses is not a presentation: permanent."""
    try:
        found = await ctx.compute.pptx_slides(PptxSlidesRequest(pptx_b64=encoded))
    except ComputeRejected as exc:
        raise JobFailedPermanently(f"the presentation cannot be read: {exc}") from exc
    return [(s.slide, slide_text(s.title, s.text, s.notes), b"") for s in found.slides]


async def _extract_pages(
    ctx: JobContext, job: ClaimedJob, material: _Material
) -> list[tuple[int, str, bytes]]:
    """`(page, text, image)` of the file; an error of compute or of the model goes up: the job
    is retried."""
    data = await ctx.storage.get(material.object_key)
    encoded = base64.b64encode(data).decode()
    if material.mime == PDF_MIME:
        found = await ctx.compute.pdf_pages(PdfPagesRequest(pdf_b64=encoded))
        pages = [
            (p.page, _clean_text(p.text), base64.b64decode(p.image_webp_b64)) for p in found.pages
        ]
        return await _read_scans(ctx, job, material, pages)
    if material.mime == PPTX_MIME:
        return await _read_slides(ctx, encoded)
    if not material.mime.startswith("image/"):
        raise JobFailedPermanently(
            f"material {material.material_id}: unreadable type {material.mime}"
        )
    aligned = await ctx.compute.image_align(ImageRequest(image_b64=encoded))
    image = base64.b64decode(aligned.image_webp_b64)
    text = await _read_sheet(
        ctx, job, material, image, f"recognizer:material:{material.material_id}"
    )
    return [(1, text, image)]


async def _store_pages(
    ctx: JobContext, learner_id: UUID, material_id: UUID, pages: Sequence[tuple[int, str, bytes]]
) -> None:
    """The images first (objects are immutable and a repeat after a crash finds its own), then
    every row of `content.material_pages` in one transaction: a step that died halfway leaves
    no pages, so the repeat extracts the file again instead of taking a part of it for the
    whole material (law 7, WP20.2)."""
    prefix = material_page_key(learner_id, material_id, 0).rsplit("/", 1)[0] + "/"
    present = {o.key for o in await ctx.storage.list_objects(prefix)}
    keys: dict[int, str | None] = {}
    for page, _, image in pages:
        if not image:  # a slide of a presentation: no image
            keys[page] = None
            continue
        key = material_page_key(learner_id, material_id, page)
        keys[page] = key
        if key not in present:
            await ctx.storage.put(key, image, PAGE_MIME)
    async with ctx.db.transaction():
        for page, text, _ in pages:
            await ctx.db.execute(
                _INSERT_PAGE,
                {
                    "material_id": material_id,
                    "page": page,
                    "learner_id": learner_id,
                    "text": text,
                    "image_key": keys[page],
                },
            )


def _names(values: Sequence[str], limit: int) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        name = " ".join(value.split())
        if name:
            seen.setdefault(name.casefold(), name)
    return list(seen.values())[:limit]


def _render(batch: Sequence[Chunk]) -> str:
    return "\n\n".join(f"[{n}] (page {c.page})\n{c.text}" for n, c in enumerate(batch, 1))


def _parser_version(settings: Settings) -> int:
    prompt = settings.llm_roles[PARSER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'material_parser' has no prompt")
    return int(prompt.rsplit("@", 1)[1])


async def _mark_batch(
    ctx: JobContext,
    job: ClaimedJob,
    material: _Material,
    index: int,
    batch: Sequence[Chunk],
    gate: asyncio.Semaphore,
) -> tuple[str, dict[UUID, ParsedChunkV2], list[ParsedDeadline], list[dict[str, Any]]]:
    version = _parser_version(ctx.settings)
    async with gate:
        answer = await ctx.llm.generate_structured(
            PARSER_ROLE,
            version,
            MaterialParserV2Output,
            {"mode": "parse", "chunks": _render(batch)},
            _learner(job),
            material.session_id,
            None,
            f"material_parser:{material.material_id}:{index}",
        )
    marks: dict[UUID, ParsedChunkV2] = {}
    for mark in answer.value.chunks:  # a number out of range or repeated is dropped
        if 1 <= mark.n <= len(batch):
            marks.setdefault(batch[mark.n - 1].chunk_id, mark)
    problems = [
        found for task in answer.value.seminar_tasks if (found := _problem(task, batch)) is not None
    ]
    return answer.value.title.strip(), marks, answer.value.deadlines, problems


def _preview(text: str, limit: int = TOC_PREVIEW_CHARS) -> str:
    return " ".join(text.split())[:limit]


def _clean_builds_on(
    items: Sequence[ParsedBuildsOn], limit: int | None = None
) -> list[dict[str, str]]:
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for item in items:
        concept = " ".join(item.concept.split())
        quote = " ".join(item.quote.split())
        if concept and quote:
            key = (concept.casefold(), quote.casefold())
            seen.setdefault(key, {"concept": concept, "quote": quote})
    out = list(seen.values())
    return out[:limit] if limit is not None else out


def _render_toc_batch(
    chunk_entries: Sequence[tuple[int, Chunk, ParsedChunkV2 | None]],
    tasks: Sequence[tuple[int, dict[str, Any]]],
    chunk_index_by_id: dict[str, int],
) -> str:
    lines = ["Chunks:"]
    for n, chunk, mark in chunk_entries:
        section = " ".join((mark.section or "").split()) if mark else ""
        lines.append(f"[{n}] section: {section or '(none)'} | preview: {_preview(chunk.text)}")
        if mark and mark.builds_on:
            claims = "; ".join(
                f"{b.concept} ({b.quote!r})" for b in mark.builds_on if b.concept.strip()
            )
            if claims:
                lines.append(f"  builds_on: {claims}")
    if tasks:
        lines.append("")
        lines.append("Seminar tasks:")
        for task_idx, task in tasks:
            chunk_n = chunk_index_by_id.get(str(task["chunk_id"]))
            chunk_label = f"[{chunk_n}]" if chunk_n is not None else "[?]"
            lines.append(f"Task {task_idx} (chunk {chunk_label}): {task['statement']}")
    return "\n".join(lines)


def _sanitize_toc(
    output: MaterialTocOutput,
    valid_chunks: set[int],
    valid_tasks: set[int],
) -> tuple[list[dict[str, Any]], dict[int, int | None], dict[int, int | None]]:
    candidates: list[dict[str, Any]] = []
    remap: dict[int, int] = {}
    for orig_idx, cand in enumerate(output.candidates):
        name = " ".join(cand.name.split())
        core_skill_name = " ".join(cand.core_skill_name.split())
        if not name or not core_skill_name:
            continue
        remap[orig_idx] = len(candidates)
        candidates.append(
            {
                "name": name,
                "description": " ".join(cand.description.split()),
                "core_skill_name": core_skill_name,
                "builds_on": _clean_builds_on(cand.builds_on),
            }
        )

    chunk_map: dict[int, int | None] = {}
    for cm in output.chunk_candidates:
        if cm.n not in valid_chunks or cm.n in chunk_map:
            continue
        mapped = remap.get(cm.candidate_index) if cm.candidate_index is not None else None
        chunk_map[cm.n] = mapped

    task_map: dict[int, int | None] = {}
    for tm in output.task_candidates:
        if tm.task_index not in valid_tasks or tm.task_index in task_map:
            continue
        mapped = remap.get(tm.candidate_index) if tm.candidate_index is not None else None
        task_map[tm.task_index] = mapped

    return candidates, chunk_map, task_map


def _render_toc_merge(
    partials: Sequence[tuple[list[dict[str, Any]], dict[int, int | None], dict[int, int | None]]],
    all_chunks: Sequence[int],
    all_tasks: Sequence[tuple[int, dict[str, Any]]],
) -> str:
    lines = ["Partial TOC batches to unify into one table of contents:"]
    mapped_chunks: set[int] = set()
    mapped_tasks: set[int] = set()
    for batch_idx, (cands, chunk_map, task_map) in enumerate(partials, 1):
        lines.append(f"\nBatch {batch_idx}:")
        chunks_by_cand: dict[int, list[int]] = {}
        for n, ci in chunk_map.items():
            mapped_chunks.add(n)
            if ci is not None:
                chunks_by_cand.setdefault(ci, []).append(n)
        tasks_by_cand: dict[int, list[int]] = {}
        for ti, ci in task_map.items():
            mapped_tasks.add(ti)
            if ci is not None:
                tasks_by_cand.setdefault(ci, []).append(ti)
        for ci, cand in enumerate(cands):
            claims = "; ".join(f"{b['concept']} ({b['quote']!r})" for b in cand["builds_on"])
            lines.append(
                f"- Candidate: {cand['name']} | core_skill: {cand['core_skill_name']} | "
                f"description: {cand['description']}"
            )
            if claims:
                lines.append(f"  builds_on: {claims}")
            lines.append(f"  chunks: {sorted(chunks_by_cand.get(ci, []))}")
            if tasks_by_cand.get(ci):
                lines.append(f"  tasks: {sorted(tasks_by_cand[ci])}")
        unmapped_c = sorted(n for n, ci in chunk_map.items() if ci is None)
        unmapped_t = sorted(ti for ti, ci in task_map.items() if ci is None)
        if unmapped_c:
            lines.append(f"- Unmapped chunks: {unmapped_c}")
        if unmapped_t:
            lines.append(f"- Unmapped tasks: {unmapped_t}")
    missing_c = [n for n in all_chunks if n not in mapped_chunks]
    if missing_c:
        lines.append(f"\nOther chunks without mapping: {missing_c}")
    missing_t = [ti for ti, _ in all_tasks if ti not in mapped_tasks]
    if missing_t:
        lines.append(f"Other tasks without mapping: {missing_t}")
    return "\n".join(lines)


async def _run_toc(
    ctx: JobContext,
    job: ClaimedJob,
    material: _Material,
    chunks: Sequence[Chunk],
    marks: dict[UUID, ParsedChunkV2],
    problems: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, int | None], dict[int, int | None]]:
    """Build the material's table of contents via `material_parser` mode `toc` (`effort="low"`),
    batching by `settings.material_toc_max_chunks` with a final merge call when exceeded."""
    if not chunks:
        return [], {}, {}
    version = _parser_version(ctx.settings)
    learner_id = _learner(job)
    chunk_index_by_id = {str(c.chunk_id): i + 1 for i, c in enumerate(chunks)}
    indexed_tasks = list(enumerate(problems))
    toc_max = max(ctx.settings.material_toc_max_chunks, 1)

    if len(chunks) <= toc_max:
        entries = [(i + 1, c, marks.get(c.chunk_id)) for i, c in enumerate(chunks)]
        rendered = _render_toc_batch(entries, indexed_tasks, chunk_index_by_id)
        answer = await ctx.llm.generate_structured(
            PARSER_ROLE,
            version,
            MaterialTocOutput,
            {"mode": "toc", "chunks": rendered},
            learner_id,
            material.session_id,
            None,
            f"material_parser:toc:{material.material_id}",
            reasoning_effort="low",
        )
        return _sanitize_toc(
            answer.value,
            set(range(1, len(chunks) + 1)),
            {ti for ti, _ in indexed_tasks},
        )

    gate = asyncio.Semaphore(ctx.settings.material_parser_concurrency)
    batches = [chunks[i : i + toc_max] for i in range(0, len(chunks), toc_max)]

    async def toc_batch(
        b_idx: int, batch: Sequence[Chunk]
    ) -> tuple[list[dict[str, Any]], dict[int, int | None], dict[int, int | None]]:
        batch_ids = {str(c.chunk_id) for c in batch}
        entries = [(chunk_index_by_id[str(c.chunk_id)], c, marks.get(c.chunk_id)) for c in batch]
        batch_tasks = [(ti, p) for ti, p in indexed_tasks if str(p["chunk_id"]) in batch_ids]
        rendered = _render_toc_batch(entries, batch_tasks, chunk_index_by_id)
        async with gate:
            res = await ctx.llm.generate_structured(
                PARSER_ROLE,
                version,
                MaterialTocOutput,
                {"mode": "toc", "chunks": rendered},
                learner_id,
                material.session_id,
                None,
                f"material_parser:toc:{material.material_id}:batch:{b_idx}",
                reasoning_effort="low",
            )
        return _sanitize_toc(
            res.value,
            {n for n, _, _ in entries},
            {ti for ti, _ in batch_tasks},
        )

    partials = await asyncio.gather(*(toc_batch(i, b) for i, b in enumerate(batches)))
    all_chunk_nums = list(range(1, len(chunks) + 1))
    merge_prompt = _render_toc_merge(partials, all_chunk_nums, indexed_tasks)
    merged = await ctx.llm.generate_structured(
        PARSER_ROLE,
        version,
        MaterialTocOutput,
        {"mode": "toc", "chunks": merge_prompt},
        learner_id,
        material.session_id,
        None,
        f"material_parser:toc:{material.material_id}:merge",
        reasoning_effort="low",
    )
    return _sanitize_toc(
        merged.value,
        set(all_chunk_nums),
        {ti for ti, _ in indexed_tasks},
    )


_CHUNK_LABEL = re.compile(r"\s*P?\s*(\d+)\s*", re.IGNORECASE)


def _problem(task: ParsedSeminarTask, batch: Sequence[Chunk]) -> dict[str, Any] | None:
    """A problem of the batch for the second step, or `None` when it has no statement or no
    place. The chunk is named by its label (`P2`); a label out of range falls back to the first
    chunk of the page the parser gave, else the problem is dropped (D-C-217)."""
    statement = task.statement.strip()
    if not statement:
        return None
    label = _CHUNK_LABEL.fullmatch(task.chunk)
    chunk = batch[int(label[1]) - 1] if label and 1 <= int(label[1]) <= len(batch) else None
    if chunk is None and task.page is not None:
        chunk = next((c for c in batch if c.page == task.page), None)
    if chunk is None:
        return None
    answer = (task.answer or "").strip()
    return {
        "statement": statement,
        "answer": answer or None,
        "chunk_id": str(chunk.chunk_id),
        "page": chunk.page,
    }


def _seminar_payload(found: Sequence[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """The problems of all batches for the second step in the order of the material: a repeat
    of a statement (case and spaces do not matter) is dropped, at most `limit` are kept. The
    place in this list is the number in the key of the task (law 5)."""
    seen: dict[str, dict[str, Any]] = {}
    for item in found:
        seen.setdefault(" ".join(str(item["statement"]).split()).casefold(), item)
    return list(seen.values())[:limit]


def _deadline_payload(found: Sequence[ParsedDeadline], limit: int) -> list[dict[str, Any]]:
    """The dated controls of all batches for the second step: one per label and date (the
    first writing is kept), at most `limit`. Dates are judged in the second step."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for d in found:
        label = " ".join(d.label.split())
        date = d.date.strip()
        if label and date:
            seen.setdefault(
                (label.casefold(), date),
                {
                    "label": label,
                    "date": date,
                    "control_type": d.control_type,
                    "covers": _names(d.covers, 50),
                },
            )
    return list(seen.values())[:limit]


async def parse_step(ctx: JobContext, job: ClaimedJob) -> None:
    """Step 1 (WP20.2 п.1–3): pages, chunks, marks (mode `parse`), TOC (mode `toc`); queues the
    structure step. A repeat finds the stored pages and the same job key of the second step."""
    learner_id = _learner(job)
    material = await _load(ctx.db, learner_id, UUID(str(job.payload["material_id"])))
    if material.status == STATUS_PARSED:
        return
    settings = ctx.settings
    pages = await _stored_pages(ctx.db, learner_id, material.material_id)
    if not pages:
        extracted = await _extract_pages(ctx, job, material)
        await _store_pages(ctx, learner_id, material.material_id, extracted)
        pages = [(page, text) for page, text, _ in extracted]
    # From here the text of the material is stored: the teacher can read it before the graph work
    # (marks by the model, topics and skills, embeddings) is done, or if it never is.
    await _set_read(ctx.db, learner_id, material.material_id, len(pages))
    chunks = split_chunks(settings, material.material_id, pages)
    size = settings.material_parser_batch
    batches = [chunks[i : i + size] for i in range(0, len(chunks), size)]
    gate = asyncio.Semaphore(settings.material_parser_concurrency)
    answers = await asyncio.gather(
        *(_mark_batch(ctx, job, material, i, b, gate) for i, b in enumerate(batches))
    )
    title = next((t for t, _, _, _ in answers if t), "")
    marks = {chunk_id: mark for _, found, _, _ in answers for chunk_id, mark in found.items()}
    deadlines = _deadline_payload(
        [d for _, _, found, _ in answers for d in found], settings.material_deadlines_max
    )
    problems = _seminar_payload(
        [p for _, _, _, found in answers for p in found], settings.material_seminar_tasks_max
    )
    toc_candidates, chunk_cand_map, task_cand_map = await _run_toc(
        ctx, job, material, chunks, marks, problems
    )
    limit = settings.material_marks_per_chunk
    payload: dict[str, Any] = {
        "material_id": str(material.material_id),
        "step": STEP_STRUCTURE,
        "trigger_seq": job.payload.get("trigger_seq"),
        "title": title,
        "deadlines": deadlines,
        "toc_candidates": toc_candidates,
        "seminar_tasks": [
            {
                **p,
                **({"candidate_index": task_cand_map[i]} if i in task_cand_map else {}),
            }
            for i, p in enumerate(problems)
        ],
        "chunks": [
            {
                "chunk_id": str(c.chunk_id),
                "section": (marks[c.chunk_id].section or "").strip() if c.chunk_id in marks else "",
                "notation": _names(marks[c.chunk_id].notation, limit)
                if c.chunk_id in marks
                else [],
                "builds_on": _clean_builds_on(marks[c.chunk_id].builds_on, limit)
                if c.chunk_id in marks
                else [],
                "candidate_index": chunk_cand_map.get(i + 1),
            }
            for i, c in enumerate(chunks)
        ],
    }
    trigger = job.payload.get("trigger_seq")
    await queue_p4.enqueue(
        ctx.db,
        JobPlan(
            kind=job.kind,
            job_key=f"material.parse:{learner_id}:{material.material_id}:{STEP_STRUCTURE}",
            priority_class="P1" if material.session_id is not None else "P2",
            learner_id=learner_id,
            lock_key=f"domain:{material.domain_id}",
            required_seq=trigger if isinstance(trigger, int) else None,
            session_id=material.session_id,
            visible=True,
            payload=payload,
        ),
        redis=ctx.redis,
    )


# --- step 2: structure under the lock of the domain -------------------------------------------


@dataclass(frozen=True)
class BuildsOnClaim:
    concept: str
    quote: str


@dataclass(frozen=True)
class TopicCandidateSpec:
    name: str
    description: str
    core_skill_name: str
    builds_on: tuple[BuildsOnClaim, ...] = ()


@dataclass(frozen=True)
class ResolvedTopic:
    topic_id: UUID
    core_skill_id: UUID
    skill_ids: tuple[UUID, ...] = ()


class StructureResolver(Protocol):
    """The seam to `resolve_material_structure` (stream B): resolves TOC topic candidates to
    `(topic_id, core_skill_id, skill_ids)` aligned 1-to-1 with `candidates` under
    `lock_key = domain:{id}`."""

    async def __call__(
        self,
        ctx: JobContext,
        *,
        learner_id: UUID,
        domain_id: UUID,
        candidates: Sequence[TopicCandidateSpec],
        source_refs: Sequence[dict[str, Any]] = (),
    ) -> list[ResolvedTopic]: ...


@dataclass(frozen=True)
class SeminarSpec:
    """One problem of the material with the skill and topic it is hung on, for stream B's
    `create_seminar_task` (the glue of the assembly layer makes a `SeminarTask` of it). `key` is
    `seminar:{material_id}:{n}`, `n` the place of the problem in the payload of the step."""

    key: str
    statement: str
    answer: str | None
    skill_id: UUID
    skill_name: str
    topic_name: str
    domain_id: UUID
    course_context: str
    topic_id: UUID | None = None
    topic_skill_ids: tuple[UUID, ...] = ()


class SeminarWriter(Protocol):
    """The seam to `quack.practice.seminar_tasks.create_seminar_task` (stream B, D-B-218): a
    task per spec, markup and check included; a task it throws away is not an error; a task
    already made is skipped. It raises once every spec has been tried, if one of them failed:
    the job `material.seminar_tasks` is retried and goes on with the rest."""

    async def __call__(
        self, ctx: JobContext, *, learner_id: UUID, tasks: Sequence[SeminarSpec]
    ) -> None: ...


_LEARNER_ZONE = (
    "SELECT settings->>'timezone' AS tz FROM core.learners WHERE learner_id = %(learner_id)s"
)


async def _learner_zone(db: Db, learner_id: UUID) -> ZoneInfo | None:
    row = await db.fetch_one(_LEARNER_ZONE, {"learner_id": learner_id})
    try:
        return ZoneInfo(row["tz"]) if row and row["tz"] else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _deadline_ms(value: str, zone: ZoneInfo | None, now: datetime) -> int | None:
    """The time of a deadline in ms, or None when the date cannot be read or is not in the
    future. A date alone is the end of that day, 23:59; a time without a zone is the time of
    the learner (UTC when the learner has no zone)."""
    value = value.strip()
    try:
        at = datetime.combine(calendar_date.fromisoformat(value), time(23, 59))
    except ValueError:
        try:
            at = datetime.fromisoformat(value)
        except ValueError:
            return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=zone or UTC)
    return int(at.timestamp() * 1000) if at > now else None


def _syllabus_events(
    material: _Material,
    deadlines: Sequence[dict[str, Any]],
    topic_ids: dict[str, UUID],
    zone: ZoneInfo | None,
    settings: Settings,
    refs: list[dict[str, Any]],
    now: datetime,
) -> list[NewEvent]:
    """A hub for every dated control of a syllabus with the topics it covers and its deadline
    (`hub.deadline_set {source: syllabus}`, Т-§4.8, D-C-213). A date in the past or not readable
    is skipped; the hub id depends on the material, the label and the date (law 5)."""
    events: list[NewEvent] = []
    done: set[UUID] = set()
    for item in deadlines:
        label, date = str(item["label"]), str(item["date"])
        at_ms = _deadline_ms(date, zone, now)
        hub_id = uuid5(
            QUACK_UUID_NS, f"syllabus-hub:{material.material_id}:{label.casefold()}:{date}"
        )
        if at_ms is None or hub_id in done:
            if at_ms is None:
                log.info("syllabus_deadline_skipped", material_id=str(material.material_id))
            continue
        done.add(hub_id)
        control = cast(ControlType, item["control_type"])
        key = f"{{}}:syllabus:{material.material_id}:{hub_id}"
        common: dict[str, Any] = {
            "domain_id": material.domain_id,
            "session_id": material.session_id,
            "source_refs": refs,
            "actor": ACTOR,
        }
        events.append(
            NewEvent(
                type="hub.created",
                payload=HubCreated(
                    hub_id=hub_id,
                    domain_id=material.domain_id,
                    name=label,
                    kind="material",
                    origin="material",
                    control_type=control,
                ),
                idempotency_key=key.format("hub.created"),
                **common,
            )
        )
        covered = list(
            dict.fromkeys(
                topic_ids[" ".join(c.split()).casefold()]
                for c in item.get("covers", [])
                if " ".join(c.split()).casefold() in topic_ids
            )
        )
        if covered:
            levels = settings.control_type_levels[control]
            events.append(
                NewEvent(
                    type="hub.topics_changed",
                    payload=HubTopicsChanged(
                        hub_id=hub_id,
                        topics=[HubTopic(topic_id=t, required_levels=levels) for t in covered],
                    ),
                    idempotency_key=key.format("hub.topics_changed"),
                    **common,
                )
            )
        events.append(
            NewEvent(
                type="hub.deadline_set",
                payload=HubDeadlineSet(hub_id=hub_id, deadline_at_ms=at_ms, source="syllabus"),
                idempotency_key=key.format("hub.deadline_set"),
                **common,
            )
        )
    return events


def _resolved_for_index(
    index: Any, resolved_topics: Sequence[ResolvedTopic]
) -> tuple[int, ResolvedTopic] | None:
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(resolved_topics):
        return index, resolved_topics[index]
    return None


def _seminar_specs(
    material: _Material,
    title: str,
    problems: Sequence[dict[str, Any]],
    marks: dict[str, dict[str, Any]],
    candidates: Sequence[TopicCandidateSpec],
    resolved_topics: Sequence[ResolvedTopic],
) -> list[SeminarSpec]:
    """A spec per problem mapped to a resolved topic candidate (falling back to its chunk's
    candidate only when `candidate_index` is absent from the problem item). A problem with no
    resolved candidate is skipped (D-C-217)."""
    specs: list[SeminarSpec] = []
    for n, problem in enumerate(problems):
        cand_idx = (
            problem["candidate_index"]
            if "candidate_index" in problem
            else marks.get(str(problem["chunk_id"]), {}).get("candidate_index")
        )
        matched = _resolved_for_index(cand_idx, resolved_topics)
        if matched is None:
            log.info("seminar_task_without_skill", material_id=str(material.material_id), n=n)
            continue
        idx, resolved = matched
        cand = candidates[idx]
        specs.append(
            SeminarSpec(
                key=f"seminar:{material.material_id}:{n}",
                statement=str(problem["statement"]),
                answer=problem.get("answer"),
                skill_id=resolved.core_skill_id,
                skill_name=cand.core_skill_name,
                topic_name=cand.name,
                domain_id=material.domain_id,
                course_context=title,
                topic_id=resolved.topic_id,
                topic_skill_ids=tuple(resolved.skill_ids),
            )
        )
    return specs


def _seminar_plan(material: _Material, job: ClaimedJob, specs: Sequence[SeminarSpec]) -> JobPlan:
    """The job `material.seminar_tasks` with the specs it needs: the ids of the skills and topics
    exist only here, in the step that resolved them. No lock of the domain (it writes no
    structure); a lock of the material keeps two runs of one material from making the same tasks
    at once."""
    trigger = job.payload.get("trigger_seq")
    return JobPlan(
        kind=SEMINAR_TASKS_KIND,
        job_key=f"{SEMINAR_TASKS_KIND}:{job.learner_id}:{material.material_id}",
        priority_class="P2",
        learner_id=job.learner_id,
        lock_key=f"material:{material.material_id}:seminar_tasks",
        session_id=material.session_id,
        payload={
            "material_id": str(material.material_id),
            "trigger_seq": trigger if isinstance(trigger, int) else None,
            "tasks": [
                {
                    "key": s.key,
                    "statement": s.statement,
                    "answer": s.answer,
                    "skill_id": str(s.skill_id),
                    "skill_name": s.skill_name,
                    "topic_name": s.topic_name,
                    "domain_id": str(s.domain_id),
                    "course_context": s.course_context,
                    "topic_id": str(s.topic_id) if s.topic_id is not None else None,
                    "topic_skill_ids": [str(sid) for sid in s.topic_skill_ids],
                }
                for s in specs
            ],
        },
    )


async def structure_step(ctx: JobContext, job: ClaimedJob, resolve: StructureResolver) -> None:
    """Step 2 (WP20.2 п.4–5), under `domain:{id}`. Nothing is written while the structure
    resolution fails (law 7); the events of the material are one transaction with the status and
    with the job of the seminar tasks, which are made after the step and outside its lock."""
    learner_id = _learner(job)
    material = await _load(ctx.db, learner_id, UUID(str(job.payload["material_id"])))
    if material.status == STATUS_PARSED:
        return
    pages = await _stored_pages(ctx.db, learner_id, material.material_id)
    chunks = split_chunks(ctx.settings, material.material_id, pages)
    marks = {str(m["chunk_id"]): m for m in job.payload.get("chunks", [])}
    title = str(job.payload.get("title") or "")
    deadlines: list[dict[str, Any]] = list(job.payload.get("deadlines", []))
    candidates = [
        TopicCandidateSpec(
            name=str(c["name"]),
            description=str(c.get("description") or ""),
            core_skill_name=str(c["core_skill_name"]),
            builds_on=tuple(
                BuildsOnClaim(concept=str(b["concept"]), quote=str(b["quote"]))
                for b in c.get("builds_on", ())
            ),
        )
        for c in job.payload.get("toc_candidates", [])
    ]
    seen_names = {" ".join(c.name.split()).casefold() for c in candidates}
    for item in deadlines:
        for raw_cover in item.get("covers", ()):
            cover = " ".join(str(raw_cover).split())
            if cover and cover.casefold() not in seen_names:
                seen_names.add(cover.casefold())
                candidates.append(
                    TopicCandidateSpec(name=cover, description="", core_skill_name=cover)
                )

    trigger = job.payload.get("trigger_seq")
    refs = [{"seq": trigger}] if isinstance(trigger, int) else []
    resolved_topics = (
        await resolve(
            ctx,
            learner_id=learner_id,
            domain_id=material.domain_id,
            candidates=candidates,
            source_refs=refs,
        )
        if candidates
        else []
    )
    if len(resolved_topics) != len(candidates):
        raise ValueError(
            f"structure resolver returned {len(resolved_topics)} "
            f"topics for {len(candidates)} candidates"
        )

    specs = _seminar_specs(
        material,
        title,
        list(job.payload.get("seminar_tasks", [])),
        marks,
        candidates,
        resolved_topics,
    )

    common: dict[str, Any] = {
        "domain_id": material.domain_id,
        "session_id": material.session_id,
        "source_refs": refs,
    }
    events: list[NewEvent] = []
    for chunk in chunks:
        mark = marks.get(str(chunk.chunk_id), {})
        matched = _resolved_for_index(mark.get("candidate_index"), resolved_topics)
        teaches = [matched[1].core_skill_id] if matched is not None else []
        events.append(
            NewEvent(
                type="chunk.indexed",
                payload=ChunkIndexed(
                    chunk_id=chunk.chunk_id,
                    material_id=material.material_id,
                    page=chunk.page,
                    text=chunk.text,
                    notation=list(mark.get("notation", [])),
                    teaches=teaches,
                    uses=[],
                ),
                actor=ACTOR,
                idempotency_key=f"chunk.indexed:{chunk.chunk_id}",
                **common,
            )
        )
    topic_by_name = {
        " ".join(c.name.split()).casefold(): r.topic_id
        for c, r in zip(candidates, resolved_topics, strict=True)
    }
    events.extend(
        _syllabus_events(
            material,
            deadlines,
            topic_by_name,
            await _learner_zone(ctx.db, learner_id) if deadlines else None,
            ctx.settings,
            refs,
            datetime.now(UTC),
        )
    )
    events.append(
        NewEvent(
            type="material.parsed",
            payload=MaterialParsed(material_id=material.material_id, pages_count=len(pages)),
            actor="system",
            idempotency_key=f"material.parsed:{material.material_id}",
            **common,
        )
    )
    async with ctx.db.transaction():
        await append_events(ctx.db, learner_id, events, redis=ctx.redis)
        await _set_parsed(ctx.db, learner_id, material.material_id, len(pages))
        if specs:
            await queue_p4.enqueue(ctx.db, _seminar_plan(material, job, specs), redis=ctx.redis)
    log.info("material_parsed", material_id=str(material.material_id), chunks=len(chunks))


# --- step 3: the seminar tasks, outside the lock of the domain --------------------------------


def _spec_of(item: dict[str, Any]) -> SeminarSpec:
    answer = item.get("answer")
    raw_topic_id = item.get("topic_id")
    return SeminarSpec(
        key=str(item["key"]),
        statement=str(item["statement"]),
        answer=str(answer) if answer else None,
        skill_id=UUID(str(item["skill_id"])),
        skill_name=str(item["skill_name"]),
        topic_name=str(item["topic_name"]),
        domain_id=UUID(str(item["domain_id"])),
        course_context=str(item["course_context"]),
        topic_id=UUID(str(raw_topic_id)) if raw_topic_id else None,
        topic_skill_ids=tuple(UUID(str(sid)) for sid in item.get("topic_skill_ids", ())),
    )


async def seminar_tasks_step(ctx: JobContext, job: ClaimedJob, write: SeminarWriter) -> None:
    """The job `material.seminar_tasks`: a task per problem the structure step linked to a skill.
    The material is `parsed` already and stays so whatever happens here: an error of a model goes
    up and the job is retried (the writer skips the tasks made), it never marks the material."""
    learner_id = _learner(job)
    specs = [_spec_of(item) for item in job.payload.get("tasks", [])]
    if specs:
        await write(ctx, learner_id=learner_id, tasks=specs)
