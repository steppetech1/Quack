"""Parse of a material — the job `material.parse` (ТЗ Ф4 WP20.2; Т-§4.3, Т-§3.4.5, Т-§16.5,
Т-§13.4; D-C-200..D-C-203).

One job kind, two steps; the second is queued by the first.

1. **parse** (no lock — the heavy part, Т-§4.3). The file comes from the object store and goes to
   compute in the body of the request (`Д-§3.2`): a PDF to `/v1/pdf/pages`, a photo of notes to
   `/v1/image/align` and `recognizer` in the sheet mode. The text by pages goes to
   `content.material_pages`, the page images to the store. Code cuts the pages into chunks of
   about `material_chunk_chars`; `material_parser` marks them in parallel batches: notation, what
   the chunk teaches and what it uses, as names in the words of the material. The marks ride in
   the payload of the second step; no event is written yet.
2. **structure** (`lock_key = domain:{id}`, D-C-200). The names go to the reconciliation of the
   graph (`reconcile_domain`, stream B, behind the `StructureResolver` seam): topics first, then
   skills in batches of `candidate_k`; `uses` of the chunk that teaches a skill become its
   `REQUIRES` (origin `material`). Then, in one transaction: `chunk.indexed` for every chunk with
   the ids of the skills, the hub «from the material» with its topics, `material.parsed` and the
   status of the row.

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

from quack.compute_client.contracts import ImageRequest, PdfPagesRequest
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
RECOGNIZER_VERSION = 1
SHEET_MODE = "sheet"
PAGE_MIME = "image/webp"
PDF_MIME = "application/pdf"
STATUS_PARSED = "parsed"
STEP_STRUCTURE = "structure"
HUB_CONTROL_TYPE = "no_goal"
HUB_NAME_FALLBACK = "Untitled material"
ACTOR = "material_parser@1"

_MATERIAL = (
    "SELECT material_id, domain_id, session_id, object_key, mime, status FROM content.materials "
    "WHERE learner_id = %(learner_id)s AND material_id = %(material_id)s"
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
    """The marks of one chunk. `n` is the number of the chunk in the input (from 1); names are
    in the words of the material, not labels: the graph is not in front of the parser, the
    reconciliation of the structure step chooses between the names and the nodes (D-C-202)."""

    model_config = ConfigDict(extra="forbid")

    n: int
    topic: str | None
    notation: list[str]
    teaches: list[str]
    uses: list[str]


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


class MaterialParserOutput(BaseModel):
    """`material_parser@1`: the title of the material as its pages call it (empty when they
    give none; the first non-empty one of the batches is kept), the marks of the chunks and the
    dated controls of the course (empty unless the chunks are a syllabus)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    chunks: list[ParsedChunk]
    deadlines: list[ParsedDeadline]


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
    """`recognizer@1` in the sheet mode as the parse of a photo of notes reads it. A copy of
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
    prefix = material_page_key(learner_id, material_id, 0).rsplit("/", 1)[0] + "/"
    present = {o.key for o in await ctx.storage.list_objects(prefix)}
    for page, text, image in pages:
        key = material_page_key(learner_id, material_id, page)
        if key not in present:  # objects are immutable; a repeat after a crash finds its own
            await ctx.storage.put(key, image, PAGE_MIME)
        await ctx.db.execute(
            _INSERT_PAGE,
            {
                "material_id": material_id,
                "page": page,
                "learner_id": learner_id,
                "text": text,
                "image_key": key,
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


async def _mark_batch(
    ctx: JobContext,
    job: ClaimedJob,
    material: _Material,
    index: int,
    batch: Sequence[Chunk],
    gate: asyncio.Semaphore,
) -> tuple[str, dict[UUID, ParsedChunk], list[ParsedDeadline]]:
    prompt = ctx.settings.llm_roles[PARSER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'material_parser' has no prompt")
    async with gate:
        answer = await ctx.llm.generate_structured(
            PARSER_ROLE,
            int(prompt.rsplit("@", 1)[1]),
            MaterialParserOutput,
            {"chunks": _render(batch)},
            _learner(job),
            material.session_id,
            None,
            f"material_parser:{material.material_id}:{index}",
        )
    marks: dict[UUID, ParsedChunk] = {}
    for mark in answer.value.chunks:  # a number out of range or repeated is dropped
        if 1 <= mark.n <= len(batch):
            marks.setdefault(batch[mark.n - 1].chunk_id, mark)
    return answer.value.title.strip(), marks, answer.value.deadlines


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
    """Step 1 (WP20.2 п.1–3): pages, chunks, marks; queues the structure step. A repeat finds
    the stored pages and the same job key of the second step."""
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
    chunks = split_chunks(settings, material.material_id, pages)
    size = settings.material_parser_batch
    batches = [chunks[i : i + size] for i in range(0, len(chunks), size)]
    gate = asyncio.Semaphore(settings.material_parser_concurrency)
    answers = await asyncio.gather(
        *(_mark_batch(ctx, job, material, i, b, gate) for i, b in enumerate(batches))
    )
    title = next((t for t, _, _ in answers if t), "")
    marks = {chunk_id: mark for _, found, _ in answers for chunk_id, mark in found.items()}
    deadlines = _deadline_payload(
        [d for _, _, found in answers for d in found], settings.material_deadlines_max
    )
    limit = settings.material_marks_per_chunk
    payload: dict[str, Any] = {
        "material_id": str(material.material_id),
        "step": STEP_STRUCTURE,
        "trigger_seq": job.payload.get("trigger_seq"),
        "title": title,
        "deadlines": deadlines,
        "chunks": [
            {
                "chunk_id": str(c.chunk_id),
                "topic": (marks[c.chunk_id].topic or "").strip() if c.chunk_id in marks else "",
                "notation": _names(marks[c.chunk_id].notation, limit)
                if c.chunk_id in marks
                else [],
                "teaches": _names(marks[c.chunk_id].teaches, limit) if c.chunk_id in marks else [],
                "uses": _names(marks[c.chunk_id].uses, limit) if c.chunk_id in marks else [],
            }
            for c in chunks
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
class SubjectSpec:
    """One thing found in the material for the reconciliation (`quack.agents.reconcile.Subject`
    in the glue of the assembly layer): a topic or a skill with the words it is found in, the
    topic of a new skill and the names of the skills it needs first."""

    name: str
    kind: Literal["topic", "skill"]
    text: str = ""
    topic: str | None = None
    requires: tuple[str, ...] = ()


class StructureResolver(Protocol):
    """The seam to `reconcile_domain` (stream B, WP16.3): an id of the node for each subject
    `(kind, name)` it linked or created; a subject it skipped is not in the answer. Called with
    at most `candidate_k` subjects; the caller holds `lock_key = domain:{id}`."""

    async def __call__(
        self,
        ctx: JobContext,
        *,
        learner_id: UUID,
        domain_id: UUID,
        subjects: Sequence[SubjectSpec],
    ) -> dict[tuple[str, str], UUID]: ...


@dataclass
class _Skill:
    name: str
    text: str
    topic: str = ""
    requires: list[str] | None = None


def _canonical(names: Sequence[str], seen: dict[str, str]) -> list[str]:
    return [seen.setdefault(n.casefold(), n) for n in names]


def _subjects(
    chunks: Sequence[Chunk],
    marks: dict[str, dict[str, Any]],
    title: str,
    covers: Sequence[str] = (),
) -> tuple[list[SubjectSpec], list[SubjectSpec]]:
    """Topic subjects and skill subjects in the order of the material; the topics a syllabus
    says its controls cover come after those of the chunks."""
    skill_names: dict[str, str] = {}
    topic_names: dict[str, str] = {}
    skills: dict[str, _Skill] = {}
    for chunk in chunks:
        mark = marks.get(str(chunk.chunk_id), {})
        raw_topic = " ".join(str(mark.get("topic") or "").split())
        topic = _canonical([raw_topic], topic_names)[0] if raw_topic else title
        teaches = _canonical(mark.get("teaches", []), skill_names)
        uses = _canonical(mark.get("uses", []), skill_names)
        for name in [*teaches, *uses]:
            skill = skills.setdefault(name, _Skill(name, chunk.text, "", []))
            if name in teaches and not skill.topic:
                skill.topic = topic
        for name in teaches:
            needs = skills[name].requires or []
            skills[name].requires = needs + [u for u in uses if u != name and u not in needs]
    _canonical([" ".join(c.split()) for c in covers if c.strip()], topic_names)
    topics = [SubjectSpec(name, "topic") for name in topic_names.values()]
    specs = [
        SubjectSpec(s.name, "skill", s.text, s.topic or title or None, tuple(s.requires or ()))
        for s in skills.values()
    ]
    return topics, specs


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


async def structure_step(ctx: JobContext, job: ClaimedJob, resolve: StructureResolver) -> None:
    """Step 2 (WP20.2 п.4–5), under `domain:{id}`. Nothing is written while the reconciliation
    fails (law 7); the events of the material are one transaction with the status."""
    learner_id = _learner(job)
    material = await _load(ctx.db, learner_id, UUID(str(job.payload["material_id"])))
    if material.status == STATUS_PARSED:
        return
    pages = await _stored_pages(ctx.db, learner_id, material.material_id)
    chunks = split_chunks(ctx.settings, material.material_id, pages)
    marks = {str(m["chunk_id"]): m for m in job.payload.get("chunks", [])}
    title = str(job.payload.get("title") or "")
    deadlines: list[dict[str, Any]] = list(job.payload.get("deadlines", []))
    covers = [c for d in deadlines for c in d.get("covers", [])]
    topics, skills = _subjects(chunks, marks, title, covers)

    size = max(ctx.settings.candidate_k, 1)
    found: dict[tuple[str, str], UUID] = {}
    for group in (topics, skills):
        for i in range(0, len(group), size):
            found.update(
                await resolve(
                    ctx,
                    learner_id=learner_id,
                    domain_id=material.domain_id,
                    subjects=group[i : i + size],
                )
            )

    canonical = {n.casefold(): n for n in (s.name for s in skills)}

    def ids(names: Sequence[str]) -> list[UUID]:
        resolved = (found.get(("skill", canonical.get(n.casefold(), n))) for n in names)
        return list(dict.fromkeys(i for i in resolved if i is not None))

    trigger = job.payload.get("trigger_seq")
    refs = [{"seq": trigger}] if isinstance(trigger, int) else []
    common: dict[str, Any] = {
        "domain_id": material.domain_id,
        "session_id": material.session_id,
        "source_refs": refs,
    }
    events: list[NewEvent] = []
    topic_ids = list(
        dict.fromkeys(found[("topic", t.name)] for t in topics if ("topic", t.name) in found)
    )
    if topic_ids:
        hub_id = uuid5(QUACK_UUID_NS, f"material-hub:{material.material_id}")
        levels = ctx.settings.control_type_levels[HUB_CONTROL_TYPE]
        events.append(
            NewEvent(
                type="hub.created",
                payload=HubCreated(
                    hub_id=hub_id,
                    domain_id=material.domain_id,
                    name=title or HUB_NAME_FALLBACK,
                    kind="material",
                    origin="material",
                    control_type=HUB_CONTROL_TYPE,
                ),
                actor=ACTOR,
                idempotency_key=f"hub.created:material:{material.material_id}",
                **common,
            )
        )
        events.append(
            NewEvent(
                type="hub.topics_changed",
                payload=HubTopicsChanged(
                    hub_id=hub_id,
                    topics=[HubTopic(topic_id=t, required_levels=levels) for t in topic_ids],
                ),
                actor=ACTOR,
                idempotency_key=f"hub.topics_changed:material:{material.material_id}",
                **common,
            )
        )
    for chunk in chunks:
        mark = marks.get(str(chunk.chunk_id), {})
        events.append(
            NewEvent(
                type="chunk.indexed",
                payload=ChunkIndexed(
                    chunk_id=chunk.chunk_id,
                    material_id=material.material_id,
                    page=chunk.page,
                    text=chunk.text,
                    notation=list(mark.get("notation", [])),
                    teaches=ids(mark.get("teaches", [])),
                    uses=ids(mark.get("uses", [])),
                ),
                actor=ACTOR,
                idempotency_key=f"chunk.indexed:{chunk.chunk_id}",
                **common,
            )
        )
    topic_by_name = {
        t.name.casefold(): found[("topic", t.name)] for t in topics if ("topic", t.name) in found
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
    log.info("material_parsed", material_id=str(material.material_id), chunks=len(chunks))
