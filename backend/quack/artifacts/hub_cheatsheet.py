"""The cheat sheet of a hub (ТЗ Ф4 WP23.5; Т-§17.6, Т-§17.7; D-C-143, D-C-144, D-C-148).

One page for the hub: formulas, methods with the signs of when to apply them, typical traps. The
job `artifact.cheatsheet` (P2, `lock_key = artifact:{hub}`, payload `{hub_id, request_id}`) is
put by the learner's consent — `agent.requested {kind: artifact, params: {artifact: cheatsheet,
hub_id}}` (`POST /v1/hubs/{id}/cheatsheet` or the teacher's task), not by a deadline: the offer
itself is a state read by `quack.graph.read.cheatsheet_offer` (D-C-144). A job without a
`request_id` (the old `hub.deadline_set` row of the dispatcher) writes nothing.

Fit to the page (Т-§17.6): the writer's blocks are saved, the whole document — the learner's
blocks too — goes as HTML to compute `/v1/render/pdf` (A4) and comes back with the page count;
more than one page — the writer is asked again, with the page count and the demand to shorten,
up to `cheatsheet_fit_rounds` times. Every round is a saved version (the history of Т-§17.1);
the last one stays even if it still does not fit.

«Собери сам — я проверю»: the learner writes the sheet in the editor (`PUT` of blocks) or on
the canvas; `review_hub_cheatsheet` shows it to the writer with the skills of the hub and the
materials. The learner's blocks are never rewritten: a remark is a supplement next to the block
it concerns (П-11.1), which the learner accepts or deletes.
"""

from __future__ import annotations

import html
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from redis.asyncio import Redis

from quack.artifacts.accuracy import write_blocks
from quack.artifacts.documents import (
    Artifact,
    ArtifactNotFound,
    Block,
    SystemBlock,
    artifact_id_for,
    get_artifact,
    version_blocks,
    write_system_blocks,
    written_version,
)
from quack.artifacts.learner_skills import bound_skill, writer_text
from quack.artifacts.topic_refresh import (
    WriterInput,
    chunks_text,
    needed_hints,
    numbered_blocks_text,
    personal_text,
    phrasings_text,
    worked_phrasings,
)
from quack.compute_client.client import ComputeClient
from quack.compute_client.contracts import RenderPdfRequest
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.graph.ids import QUACK_UUID_NS
from quack.graph.read import GraphReader, TopicMemory, create_graph_reader
from quack.graph.read.materials import ChunkHit, materials_prefetch
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap
from quack.logging import get_logger

__all__ = [
    "ArtifactNotFound",
    "CheatsheetResult",
    "HubWriterInput",
    "Review",
    "cheatsheet_html",
    "consent_event",
    "generate_hub_cheatsheet",
    "hub_writer_input",
    "request_hub_cheatsheet",
    "review_hub_cheatsheet",
]

log = get_logger(__name__)

CHEATSHEET_TYPE = "cheatsheet"
REVIEW_TYPE = (
    "cheatsheet review. The learner wrote this cheat sheet themselves. Check it against the "
    "skills of the hub and the materials. Never rewrite the learner's blocks: put a remark as a "
    "block whose `updates` is the number of the learner's block it concerns; a gap that no block "
    "covers is a new block. Leave the blocks of the system as they are."
)

type Prefetch = Callable[[Sequence[UUID]], Awaitable[list[ChunkHit]]]


def shorten_type(pages: int) -> str:
    return (
        f"{CHEATSHEET_TYPE}. The last version took {pages} pages. Rewrite the whole sheet shorter: "
        "it must fit on one page. Keep the formulas, the methods with the signs of when to apply "
        "them and the typical traps; drop the rest."
    )


@dataclass(frozen=True)
class HubWriterInput(WriterInput):
    """The writer's input for a hub; `title` — the name of the hub, for the printed page."""

    title: str = ""


@dataclass(frozen=True)
class CheatsheetResult:
    """`version` — the last saved one (`None`: nothing was written); `page_count` — of that
    version (`None`: not rendered); `rounds` — how many times the writer was asked again to
    shorten; `fits` — the last version is one page."""

    version: int | None
    page_count: int | None
    rounds: int
    fits: bool


@dataclass(frozen=True)
class Review:
    """The sheet after the review and the remarks the writer added (system blocks: supplements
    next to the learner's blocks and gaps)."""

    artifact: Artifact
    remarks: tuple[Block, ...]


# --- the input of the writer -----------------------------------------------------------------


async def hub_writer_input(
    db: Db,
    reader: GraphReader,
    prefetch: Prefetch,
    settings: Settings,
    *,
    learner_id: UUID,
    hub_id: UUID,
    review: bool = False,
) -> HubWriterInput | None:
    """The layers of Т-§17.3 for the hub: its topics with skills, the chunks teaching them, the
    phrasings that worked and the personal minimum. `None` — the graph does not know the hub or
    it has no topics yet. `ArtifactNotFound` — the hub is not the learner's."""
    sheet = await get_artifact(db, learner_id=learner_id, kind="cheatsheet", owner_id=hub_id)
    goal = next((g for g in await reader.domain_goals(sheet.domain_id) if g.hub_id == hub_id), None)
    if goal is None or not goal.topics:
        return None
    topics = await reader.topic_memory([t.topic_id for t in goal.topics])
    if not topics:
        return None
    skill_ids = list(dict.fromkeys(s.skill_id for t in topics for s in t.skills))
    hits = await prefetch(skill_ids)
    labels = LabelMap.build(
        settings,
        skills=skill_ids,
        chunks=[h.chunk_id for h in hits],
        misconceptions=[m.misconception_id for t in topics for m in t.misconceptions],
    )
    phrasings = await worked_phrasings(db, reader, learner_id=learner_id, skill_ids=skill_ids)
    hinted = [s for s in skill_ids if await needed_hints(reader, s)]
    skill = await bound_skill(db, learner_id=learner_id, artifact_type="cheatsheet")
    variables = {
        "artifact_type": REVIEW_TYPE if review else CHEATSHEET_TYPE,
        "learner_skill": writer_text(skill),
        "topic_graph": _hub_graph(goal.name, goal.control_type, topics, labels),
        "material_chunks": chunks_text(hits, labels),
        "worked_phrasings": phrasings_text(phrasings, labels),
        "personal": _personal(topics, labels, hinted),
        "current_blocks": numbered_blocks_text(sheet.blocks),
    }
    return HubWriterInput(
        variables=variables,
        labels=labels,
        chunks={h.chunk_id: h for h in hits},
        current=sheet.blocks,
        title=goal.name,
        skill_version=None if skill is None else skill.version,
    )


def _hub_graph(
    name: str, control_type: str, topics: Sequence[TopicMemory], labels: LabelMap
) -> str:
    lines = [f"Hub: {name}", f"Control: {control_type}"]
    for topic in topics:
        lines.append(f"Topic: {topic.name}")
        for skill in topic.skills:
            label = labels.label("skill", skill.skill_id)
            if label is not None:
                lines.append(f"- {label}: {skill.name or 'unnamed skill'}")
    return "\n".join(lines)


def _personal(topics: Sequence[TopicMemory], labels: LabelMap, hinted: Sequence[UUID]) -> str:
    parts = [personal_text(t, labels, hinted) for t in topics]
    return "\n".join(p for p in parts if p != "None.") or "None."


def _with_sheet(context: HubWriterInput, sheet: Artifact, artifact_type: str) -> HubWriterInput:
    variables = {
        **context.variables,
        "artifact_type": artifact_type,
        "current_blocks": numbered_blocks_text(sheet.blocks),
    }
    return replace(context, variables=variables, current=sheet.blocks)


# --- the writer ------------------------------------------------------------------------------


async def generate_hub_cheatsheet(
    db: Db,
    gateway: LLMGateway,
    compute: ComputeClient,
    reader: GraphReader,
    prefetch: Prefetch,
    settings: Settings,
    *,
    learner_id: UUID,
    hub_id: UUID,
    key: str,
    redis: Redis | None = None,
) -> CheatsheetResult:
    """Write the hub's cheat sheet and fit it to one A4 page (Т-§17.6). `key` is the consent
    request: a round that this key has already written is not asked of the model again
    (law 5, D-C-217); a repeat renders the last written version and goes on from there."""
    context = await hub_writer_input(
        db, reader, prefetch, settings, learner_id=learner_id, hub_id=hub_id
    )
    if context is None:
        log.info("cheatsheet_unknown_hub", hub_id=str(hub_id))
        return CheatsheetResult(None, None, 0, False)
    version: int | None = None
    pages: int | None = None
    rounds = 0
    first = 0
    written = await _last_written_round(db, learner_id, hub_id, key, settings.cheatsheet_fit_rounds)
    if written is not None:
        first, version = written[0] + 1, written[1]
        blocks_then = await version_blocks(
            db,
            learner_id=learner_id,
            artifact_id=artifact_id_for(learner_id, "cheatsheet", hub_id),
            version=version,
        )
        rendered = await compute.render_pdf(
            RenderPdfRequest(html=cheatsheet_html(context.title, blocks_then))
        )
        pages, rounds = rendered.page_count, written[0]
    for round_ in range(first, settings.cheatsheet_fit_rounds + 1):
        if pages is not None and pages <= 1:
            break
        if pages is not None:
            sheet = await get_artifact(
                db, learner_id=learner_id, kind="cheatsheet", owner_id=hub_id
            )
            context = _with_sheet(context, sheet, shorten_type(pages))
        blocks, created_by = await write_blocks(
            gateway,
            settings,
            compute,
            context,
            learner_id=learner_id,
            key=f"artifact_writer:cheatsheet:{hub_id}:{key}:r{round_}",
        )
        if not blocks:
            break
        sheet = await write_system_blocks(
            db,
            learner_id=learner_id,
            kind="cheatsheet",
            owner_id=hub_id,
            blocks=blocks,
            created_by=created_by,
            skill_version=context.skill_version,
            redis=redis,
            event_key=_round_key(hub_id, key, round_),
        )
        version = sheet.version
        rendered = await compute.render_pdf(
            RenderPdfRequest(html=cheatsheet_html(context.title, sheet.blocks))
        )
        pages, rounds = rendered.page_count, round_
        if pages <= 1:
            break
    if pages is not None and pages > 1:
        log.info("cheatsheet_over_one_page", hub_id=str(hub_id), pages=pages, rounds=rounds)
    return CheatsheetResult(version, pages, rounds, pages is not None and pages <= 1)


def _round_key(hub_id: UUID, key: str, round_: int) -> str:
    return f"artifact.cheatsheet:{hub_id}:{key}:r{round_}"


async def _last_written_round(
    db: Db, learner_id: UUID, hub_id: UUID, key: str, fit_rounds: int
) -> tuple[int, int] | None:
    """`(round, version)` of the latest round this request key has already written."""
    found: tuple[int, int] | None = None
    for round_ in range(fit_rounds + 1):
        version = await written_version(
            db, learner_id=learner_id, key=_round_key(hub_id, key, round_)
        )
        if version is None:
            break
        found = (round_, version)
    return found


async def review_hub_cheatsheet(
    db: Db,
    gateway: LLMGateway,
    reader: GraphReader,
    prefetch: Prefetch,
    settings: Settings,
    *,
    learner_id: UUID,
    hub_id: UUID,
    key: str,
    redis: Redis | None = None,
    compute: ComputeClient | None = None,
) -> Review:
    """«Собери сам — я проверю» (Т-§17.6): remarks on the learner's own sheet. Nothing to check
    (no learner block, or the graph does not know the hub yet) — no call, no remarks."""
    sheet = await get_artifact(db, learner_id=learner_id, kind="cheatsheet", owner_id=hub_id)
    if not any(block.owner == "learner" for block in sheet.blocks):
        return Review(sheet, ())
    context = await hub_writer_input(
        db, reader, prefetch, settings, learner_id=learner_id, hub_id=hub_id, review=True
    )
    if context is None:
        return Review(sheet, ())
    written, created_by = await write_blocks(
        gateway,
        settings,
        compute,
        context,
        learner_id=learner_id,
        key=f"artifact_writer:cheatsheet_review:{hub_id}:{key}",
    )
    if not written:
        return Review(sheet, ())
    named = {block.block_id for block in written if block.block_id is not None}
    kept = [
        SystemBlock(
            block_id=b.block_id,
            kind=b.kind,
            content=b.content,
            sources=b.sources,
            skills=b.skills,
            basis=b.basis,
            unverified=b.unverified,
        )
        for b in sheet.blocks
        if b.owner == "system" and b.supplement_to is None and b.block_id not in named
    ]
    after = await write_system_blocks(
        db,
        learner_id=learner_id,
        kind="cheatsheet",
        owner_id=hub_id,
        blocks=[*kept, *written],
        created_by=created_by,
        skill_version=context.skill_version,
        redis=redis,
    )
    before = {b.block_id for b in sheet.blocks}
    remarks = tuple(
        b
        for b in after.blocks
        if b.owner == "system" and (b.supplement_to is not None or b.block_id not in before)
    )
    return Review(after, remarks)


# --- consent ---------------------------------------------------------------------------------


def consent_event(hub_id: UUID, request_id: UUID, *, idempotency_key: str) -> NewEvent:
    """`agent.requested {kind: artifact, params: {artifact: cheatsheet, hub_id}}` — the kind and
    the hub are parameters, never words (law 2, D-C-143)."""
    return NewEvent(
        type="agent.requested",
        payload={
            "request_id": request_id,
            "kind": "artifact",
            "params": {"artifact": "cheatsheet", "hub_id": hub_id},
            "needed_in_session": False,
        },
        actor="learner",
        idempotency_key=f"hub-cheatsheet:{hub_id}:{idempotency_key}",
    )


async def request_hub_cheatsheet(
    db: Db, learner_id: UUID, hub_id: UUID, *, idempotency_key: str
) -> UUID:
    """The learner's consent to the hub's cheat sheet; returns the request id (derived from the
    key, so a repeat is the same request). `ArtifactNotFound` — the hub is not the learner's."""
    await get_artifact(db, learner_id=learner_id, kind="cheatsheet", owner_id=hub_id)
    request_id = uuid5(QUACK_UUID_NS, f"cheatsheet-request:{learner_id}:{hub_id}:{idempotency_key}")
    await append_events(
        db, learner_id, [consent_event(hub_id, request_id, idempotency_key=idempotency_key)]
    )
    return request_id


# --- the HTML of the sheet -------------------------------------------------------------------

_STYLE = (
    "@page { size: A4; margin: 10mm; } "
    "body { font: 9pt/1.3 sans-serif; margin: 0; } "
    "h1 { font-size: 13pt; margin: 0 0 4mm; } "
    "section { margin: 0 0 2.5mm; break-inside: avoid; } "
    "section[data-owner='learner'] { border-left: 1mm solid #888; padding-left: 2mm; } "
    "p, ul, ol { margin: 0 0 1.5mm; } ul, ol { padding-left: 5mm; } "
    "code { font-family: monospace; } .math { font-style: italic; }"
)
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_CODE = re.compile(r"`([^`]+)`")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*)$")


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = _CODE.sub(r"<code>\1</code>", out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return _ITALIC.sub(r"<em>\1</em>", out)


def _markdown(text: str) -> str:
    """The few Markdown forms of a cheat sheet — paragraphs, headings, lists, bold, italic and
    code; everything is escaped first. Formulas (`$…$`) stay as TeX for the typesetter."""
    parts: list[str] = []
    for chunk in re.split(r"\n\s*\n", text.strip()):
        lines = [line for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        for pattern, tag in ((_BULLET, "ul"), (_NUMBERED, "ol")):
            items = [pattern.match(line) for line in lines]
            if all(items):
                body = "".join(f"<li>{_inline(m.group(1))}</li>" for m in items if m)
                parts.append(f"<{tag}>{body}</{tag}>")
                break
        else:
            heading = _HEADING.match(lines[0])
            if heading and len(lines) == 1:
                parts.append(f"<h3>{_inline(heading.group(1))}</h3>")
            else:
                parts.append(f"<p>{'<br>'.join(_inline(line) for line in lines)}</p>")
    return "".join(parts)


def cheatsheet_html(title: str, blocks: Sequence[Block]) -> str:
    """One self-contained A4 document of the blocks in their order (compute has no network,
    Д-§3.2), the learner's blocks marked by `data-owner`."""
    sections = "".join(
        f'<section class="block {b.kind}" data-owner="{b.owner}" data-kind="{b.kind}">'
        f"{_markdown(b.content)}</section>"
        for b in blocks
    )
    escaped = html.escape(title)
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{escaped}</title><style>{_STYLE}</style></head>"
        f"<body><h1>{escaped}</h1>{sections}</body></html>"
    )


# --- the job ---------------------------------------------------------------------------------


@register_job_handler("artifact.cheatsheet")
async def _cheatsheet_handler(job: ClaimedJob, ctx: JobContext) -> None:
    learner_id = job.learner_id
    if learner_id is None:
        raise ValueError("artifact.cheatsheet without a learner")
    request_id = job.payload.get("request_id")
    if request_id is None:
        log.info("cheatsheet_job_without_consent", job_id=str(job.id))
        return

    async def prefetch(skill_ids: Sequence[UUID]) -> list[ChunkHit]:
        return await materials_prefetch(
            ctx.neo4j, learner_id=learner_id, skill_ids=skill_ids, k=ctx.settings.materials_k
        )

    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=learner_id)  # D-A-144
    await generate_hub_cheatsheet(
        ctx.db, ctx.llm, ctx.compute, reader, prefetch, ctx.settings,
        learner_id=learner_id, hub_id=UUID(job.payload["hub_id"]), key=str(request_id),
        redis=ctx.redis,
    )  # fmt: skip
