"""WP20.2: the job `material.parse` (ТЗ Ф4 WP20.2, §8.11 `T-MT1`; Т-§4.3, Т-§3.4.5, Т-§13.4;
D-C-200..D-C-203). LLM, compute, storage and the graph reconciliation are test doubles."""

from __future__ import annotations

import base64
import re
from collections.abc import Sequence
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel

from quack.compute_client.contracts import (
    AlignResponse,
    PdfPage,
    PdfPagesResponse,
    PptxSlide,
    PptxSlidesResponse,
)
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.jobs.kinds import JOB_KINDS, KIND_LLM_ROLES
from quack.jobs.runner import JOB_HANDLERS, JobContext
from quack.jobs.types import ClaimedJob, JobFailedPermanently
from quack.llm.errors import LLMUnavailableError
from quack.llm.gateway import LLMGateway
from quack.materials.library import upload_material
from quack.materials.parse import (
    BuildsOnClaim,
    MaterialParserOutput,
    MaterialParserV2Output,
    MaterialTocOutput,
    ResolvedTopic,
    SheetReading,
    TopicCandidateSpec,
    mark_failed,
    parse_step,
    split_chunks,
    structure_step,
)
from quack.prompts.registry import PromptRegistry
from quack.runtime import HANDLER_MODULES
from quack.storage.objects import StorageUnavailable
from tests.db.fixtures import create_learner
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import ScriptedLLMProvider
from tests.support.storage import InMemoryObjectStore

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PAGES = "/v1/pdf/pages"
SLIDES = "/v1/pptx/slides"
ALIGN = "/v1/image/align"
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 page"

PAGE_ONE = (
    "Chain rule\n\nIf f and g are differentiable, then (f(g(x)))' = f'(g(x)) g'(x). "
    "It needs the derivative of a power first."
)
PAGE_TWO = "Integration by parts\n\nWe use the product rule backwards: int u dv = uv - int v du."


def _job(learner: UUID, material_id: UUID, **extra: Any) -> ClaimedJob:
    return ClaimedJob(
        id=uuid4(),
        kind="material.parse",
        learner_id=learner,
        lock_key="",
        coalesce_key=None,
        priority=0,
        payload={"material_id": str(material_id), "trigger_seq": 1, **extra},
        attempt=1,
        max_attempts=5,
        job_key=f"material.parse:{learner}:{material_id}",
    )


def _parser_answer(request: Any) -> dict[str, Any]:
    """One mark per chunk of the input (`material_parser@2` mode `parse`)."""
    numbers = [int(n) for n in re.findall(r"^\[(\d+)\]", request.prompt, flags=re.M)]
    title = "Calculus lectures"
    chunks = []
    for n in numbers:
        text = request.prompt.split(f"[{n}]", 1)[1]
        if "Chain rule" in text:
            chunks.append(
                {
                    "n": n,
                    "section": "Differentiating composite functions (chain rule)",
                    "notation": ["f'(g(x))"],
                    "builds_on": [
                        {"concept": "Power rule", "quote": "derivative of a power first"}
                    ],
                }
            )
        else:
            chunks.append(
                {
                    "n": n,
                    "section": "Integration by parts",
                    "notation": ["int u dv"],
                    "builds_on": [
                        {"concept": "Product rule", "quote": "product rule backwards"}
                    ],
                }
            )
    return {"title": title, "chunks": chunks, "deadlines": [], "seminar_tasks": []}


def _toc_answer(request: Any) -> dict[str, Any]:
    """TOC output (`material_parser@2` mode `toc`) derived from the chunks in the input."""
    numbers = [int(n) for n in re.findall(r"^\[(\d+)\]", request.prompt, flags=re.M)]
    candidates = [
        {
            "name": "Derivatives",
            "description": "Differentiation of composite functions using the chain rule.",
            "core_skill_name": "Apply the chain rule",
            "builds_on": [{"concept": "Power rule", "quote": "derivative of a power first"}],
        },
        {
            "name": "Integrals",
            "description": "Evaluating integrals with integration by parts.",
            "core_skill_name": "Apply integration by parts",
            "builds_on": [{"concept": "Product rule", "quote": "product rule backwards"}],
        },
    ]
    chunk_candidates = []
    for n in numbers:
        line = request.prompt.split(f"[{n}]", 1)[1].splitlines()[0]
        idx = 0 if "chain rule" in line.lower() or "composite" in line.lower() else 1
        chunk_candidates.append({"n": n, "candidate_index": idx})
    task_matches = re.findall(r"^Task (\d+) \(chunk \[(\d+)\]\):", request.prompt, flags=re.M)
    by_chunk = {m["n"]: m["candidate_index"] for m in chunk_candidates}
    task_candidates = [
        {"task_index": int(ti), "candidate_index": by_chunk.get(int(cn))}
        for ti, cn in task_matches
    ]
    return {
        "candidates": candidates,
        "chunk_candidates": chunk_candidates,
        "task_candidates": task_candidates,
    }


class _World:
    def __init__(self, db: Db, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.storage = InMemoryObjectStore()
        self.scenario = ComputeScenario()
        self.provider = ScriptedLLMProvider()
        self.provider.respond("MaterialParserV2Output", _parser_answer)
        self.provider.respond("MaterialTocOutput", _toc_answer)
        self.ctx = cast(
            JobContext,
            SimpleNamespace(
                db=db,
                settings=settings,
                storage=self.storage,
                compute=scripted_compute(self.scenario),
                llm=LLMGateway(self.provider, settings, db=db),
                redis=None,
                neo4j=None,
            ),
        )

    async def upload(
        self, data: bytes = PDF, *, session_id: UUID | None = None
    ) -> tuple[UUID, UUID, UUID]:
        learner = await create_learner(self.db)
        domain = uuid4()
        await append_events(
            self.db,
            learner,
            [
                NewEvent(
                    type="domain.created",
                    payload={"domain_id": str(domain), "name": "Calculus"},
                    actor="learner",
                    idempotency_key=str(uuid4()),
                )
            ],
        )
        if session_id is not None:  # the session must be the learner's (it was a made-up id)
            await append_events(
                self.db,
                learner,
                [
                    NewEvent(
                        type="session.started",
                        payload={
                            "session_id": str(session_id),
                            "domain_id": str(domain),
                            "intent": "learn",
                        },
                        actor="learner",
                        idempotency_key=str(uuid4()),
                        session_id=session_id,
                    )
                ],
            )
        material = await upload_material(
            self.db,
            self.storage,
            self.settings,
            learner_id=learner,
            domain_id=domain,
            data=data,
            session_id=session_id,
        )
        return learner, domain, material.material_id

    def pdf_pages(self, *texts: str) -> None:
        pages = [
            PdfPage(page=i, text=t, image_webp_b64=base64.b64encode(WEBP).decode())
            for i, t in enumerate(texts, 1)
        ]
        self.scenario.push_for(PAGES, PdfPagesResponse(pages=pages))

    def slides(self, *slides: tuple[str, str, str]) -> None:
        found = [
            PptxSlide(slide=i, title=t, text=body, notes=n)
            for i, (t, body, n) in enumerate(slides, 1)
        ]
        self.scenario.push_for(SLIDES, PptxSlidesResponse(slides=found))

    async def types(self, learner: UUID, *types: str) -> list[dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT type, payload, domain_id, idempotency_key FROM core.events "
            "WHERE learner_id = %(l)s AND type = ANY(%(t)s) ORDER BY seq",
            {"l": learner, "t": list(types)},
        )


class _Resolver:
    """The structure writer behind the seam: returns a `ResolvedTopic` per candidate."""

    def __init__(self) -> None:
        self.calls: list[list[TopicCandidateSpec]] = []
        self.topics: dict[str, ResolvedTopic] = {}

    async def __call__(
        self,
        ctx: JobContext,
        *,
        learner_id: UUID,
        domain_id: UUID,
        candidates: Sequence[TopicCandidateSpec],
        source_refs: Sequence[dict[str, Any]] = (),
    ) -> list[ResolvedTopic]:
        self.calls.append(list(candidates))
        out: list[ResolvedTopic] = []
        for c in candidates:
            if c.name not in self.topics:
                core_id = uuid4()
                self.topics[c.name] = ResolvedTopic(
                    topic_id=uuid4(),
                    core_skill_id=core_id,
                    skill_ids=(core_id,),
                )
            out.append(self.topics[c.name])
        return out


@pytest.fixture
def world(pg_db: Db, settings: Settings) -> _World:
    return _World(pg_db, settings)


def test_split_chunks_are_one_to_two_kb_inside_a_page(settings: Settings) -> None:
    """Т-§3.4.5: a chunk is about 1–2 KB and lies on one page; the id depends on the material,
    the page and the place, so a repeat gives the same chunks."""
    material = uuid4()
    paragraph = "A sentence of the lecture goes here. " * 12  # ~450 chars
    pages = [(1, "\n\n".join([paragraph] * 8)), (2, ""), (3, "Short page."), (4, "x" * 5000)]
    chunks = split_chunks(settings, material, pages)
    assert [c.page for c in chunks if c.page == 2] == []
    for c in chunks:
        assert 0 < len(c.text) <= settings.material_chunk_max_chars
    first_page = [c for c in chunks if c.page == 1]
    assert len(first_page) > 1
    assert all(len(c.text) >= settings.material_chunk_chars // 2 for c in first_page[:-1])
    assert [c.text for c in chunks if c.page == 3] == ["Short page."]
    assert len([c for c in chunks if c.page == 4]) >= 3  # a long unbroken text is cut
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert chunks == split_chunks(settings, material, pages)
    assert chunks[0].chunk_id != split_chunks(settings, uuid4(), pages)[0].chunk_id


async def test_material_parse_pipeline(world: _World) -> None:
    """T-MT1: upload → `material.parse` → pages, chunks marked by `material_parser`,
    `chunk.indexed`, the structure under the lock of the domain, `material.parsed`."""
    session_id = uuid4()
    learner, domain, material = await world.upload(session_id=session_id)
    world.pdf_pages(PAGE_ONE, PAGE_TWO)

    await parse_step(world.ctx, _job(learner, material, trigger_seq=7))

    rows = await world.db.fetch_all(
        "SELECT page, text, image_key FROM content.material_pages "
        "WHERE material_id = %(m)s ORDER BY page",
        {"m": material},
    )
    assert [(r["page"], r["text"]) for r in rows] == [(1, PAGE_ONE), (2, PAGE_TWO)]
    for r in rows:
        assert world.storage.objects[r["image_key"]][0] == WEBP
        assert r["image_key"].startswith(f"learners/{learner}/materials/{material}/pages/")
    # the heavy part writes no memory: the structure waits for the lock of the domain
    assert await world.types(learner, "chunk.indexed", "material.parsed") == []
    (queued,) = await world.db.fetch_all(
        "SELECT kind, lock_key, priority_class, required_seq, session_id, visible, payload, "
        "learner_id FROM ops.jobs WHERE kind = 'material.parse'"
    )
    assert queued["lock_key"] == f"domain:{domain}" and queued["learner_id"] == learner
    assert queued["priority_class"] == "P1" and queued["session_id"] == session_id
    assert queued["required_seq"] == 7 and queued["visible"] is True
    assert queued["payload"]["step"] == "structure"
    assert queued["payload"]["material_id"] == str(material)

    resolver = _Resolver()
    payload = {k: v for k, v in queued["payload"].items() if k != "material_id"}
    structure_job = _job(learner, material, **payload)
    await structure_step(world.ctx, structure_job, resolver)

    assert len(resolver.calls) == 1
    candidates = resolver.calls[0]
    by_name = {c.name: c for c in candidates}
    assert {"Derivatives", "Integrals"} <= set(by_name)
    assert by_name["Derivatives"].builds_on == (
        BuildsOnClaim("Power rule", "derivative of a power first"),
    )
    assert by_name["Integrals"].builds_on == (
        BuildsOnClaim("Product rule", "product rule backwards"),
    )

    chunks = await world.types(learner, "chunk.indexed")
    assert [c["payload"]["page"] for c in chunks] == [1, 2]
    first = chunks[0]["payload"]
    assert first["material_id"] == str(material) and first["text"] == PAGE_ONE
    assert first["notation"] == ["f'(g(x))"]
    assert first["teaches"] == [str(resolver.topics["Derivatives"].core_skill_id)]
    assert first["uses"] == []
    assert chunks[0]["domain_id"] == domain
    # No material hub created (syllabus hubs only)
    assert await world.types(learner, "hub.created", "hub.topics_changed") == []
    assert await world.types(learner, "skill.created") == []
    (parsed,) = await world.types(learner, "material.parsed")
    assert parsed["payload"] == {"material_id": str(material), "pages_count": 2}
    row = await world.db.fetch_one(
        "SELECT status, pages_count FROM content.materials WHERE material_id = %(m)s",
        {"m": material},
    )
    assert row == {"status": "parsed", "pages_count": 2}

    # law 5: a repeat of either step writes nothing twice and asks nobody
    before = len(await world.types(learner, "chunk.indexed", "material.parsed"))
    await structure_step(world.ctx, structure_job, resolver)
    await parse_step(world.ctx, _job(learner, material))
    assert len(await world.types(learner, "chunk.indexed", "material.parsed")) == before
    assert len(world.provider.calls) == 2  # 1 parse batch + 1 toc call
    assert len(world.scenario.requests) == 1


async def test_parse_step_repeat_after_a_crash_reuses_pages(world: _World) -> None:
    """The step died after the pages were stored: the repeat does not call compute again and
    does not write the images twice; the queued second step is not duplicated."""
    learner, _, material = await world.upload()
    world.pdf_pages(PAGE_ONE)
    await parse_step(world.ctx, _job(learner, material))
    await world.db.execute(
        "DELETE FROM ops.jobs WHERE kind = 'material.parse'"
    )  # the crash lost the second step, the pages stay
    await parse_step(world.ctx, _job(learner, material))
    assert len(world.scenario.requests) == 1
    (queued,) = await world.db.fetch_all(
        "SELECT payload FROM ops.jobs WHERE kind = 'material.parse'"
    )
    assert queued["payload"]["step"] == "structure"


async def test_a_failure_while_storing_pages_leaves_no_pages(world: _World) -> None:
    """Law 7, WP20.2: the store fails on the second page image. No row of `material_pages`
    stays (a part of the pages would make the repeat skip the extraction and take it for the
    whole file); the repeat extracts again and stores every page."""
    learner, _, material = await world.upload()
    world.pdf_pages(PAGE_ONE, PAGE_TWO)
    world.pdf_pages(PAGE_ONE, PAGE_TWO)
    put = world.storage.put
    calls = 0

    async def flaky(key: str, data: bytes, content_type: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise StorageUnavailable("down")
        await put(key, data, content_type)

    world.storage.put = flaky  # type: ignore[method-assign]  # a double of the test
    with pytest.raises(StorageUnavailable):
        await parse_step(world.ctx, _job(learner, material))
    world.storage.put = put  # type: ignore[method-assign]
    rows = await world.db.fetch_all(
        "SELECT page FROM content.material_pages WHERE material_id = %(m)s", {"m": material}
    )
    assert rows == []
    await parse_step(world.ctx, _job(learner, material))
    rows = await world.db.fetch_all(
        "SELECT page FROM content.material_pages WHERE material_id = %(m)s ORDER BY page",
        {"m": material},
    )
    assert [r["page"] for r in rows] == [1, 2]


async def test_parser_gets_batches_and_loses_nothing_on_bad_numbers(world: _World) -> None:
    """Т-§4.3: large materials go to `material_parser` in parallel batches; a number the model
    invented or repeated is dropped, a chunk it skipped stays unmarked."""
    learner, _, material = await world.upload()
    size = world.settings.material_parser_batch
    texts = [f"Fact number {i}. " + "words " * 40 for i in range(size * 2 + 1)]
    world.pdf_pages(*texts)

    def answer(request: Any) -> dict[str, Any]:
        numbers = [int(n) for n in re.findall(r"^\[(\d+)\]", request.prompt, flags=re.M)]
        marks = [
            {
                "n": n,
                "section": f"Section {n}",
                "notation": [],
                "builds_on": [{"concept": "Basics", "quote": f"Fact number {n}"}],
            }
            for n in numbers[1:]  # the first chunk of every batch is skipped
        ]
        marks.append({"n": 999, "section": "Ghost", "notation": [], "builds_on": []})
        marks.append({**marks[0]})  # a repeated number
        return {"title": "", "chunks": marks, "deadlines": [], "seminar_tasks": []}

    def toc_answer(request: Any) -> dict[str, Any]:
        numbers = [int(n) for n in re.findall(r"^\[(\d+)\]", request.prompt, flags=re.M)]
        return {
            "candidates": [
                {
                    "name": "Topic T",
                    "description": "Covers facts.",
                    "core_skill_name": "Apply Topic T",
                    "builds_on": [],
                }
            ],
            "chunk_candidates": [{"n": n, "candidate_index": 0} for n in numbers],
            "task_candidates": [],
        }

    world.provider.respond("MaterialParserV2Output", answer)
    world.provider.respond("MaterialTocOutput", toc_answer)
    await parse_step(world.ctx, _job(learner, material))

    assert len(world.provider.calls_for(schema_name="MaterialParserV2Output")) == 3
    assert len(world.provider.calls_for(schema_name="MaterialTocOutput")) == 1
    (queued,) = await world.db.fetch_all(
        "SELECT payload FROM ops.jobs WHERE kind = 'material.parse'"
    )
    marks = queued["payload"]["chunks"]
    assert len(marks) == len(texts)
    sections = [m["section"] for m in marks]
    assert "Ghost" not in sections
    assert sum(1 for s in sections if s == "") == 3
    assert queued["payload"]["title"] == ""


async def test_image_material_is_read_by_the_recognizer(world: _World) -> None:
    """Т-§16.5: a photo of notes is aligned by compute and read by `recognizer` in the sheet
    mode; its text lines are the one page; a drawing and a crossed-out line are not text."""
    learner, _, material = await world.upload(PNG)
    world.scenario.push_for(
        ALIGN, AlignResponse(aligned=True, image_webp_b64=base64.b64encode(WEBP).decode())
    )

    def line(text: str, top: float, kind: str = "text", crossed: bool = False) -> dict[str, Any]:
        return {
            "kind": kind,
            "text": text,
            "sympy": None,
            "bbox": {"left": 0.1, "top": top, "right": 0.9, "bottom": top + 0.05},
            "confidence": 0.9,
            "crossed_out": crossed,
        }

    world.provider.respond(
        "SheetReading",
        lambda _r: {
            "lines": [
                line("Chain rule", 0.1),
                line("", 0.2, kind="drawing"),
                line("wrong try", 0.3, crossed=True),
                line("(f(g(x)))' = f'(g(x)) g'(x)", 0.4, kind="latex"),
            ]
        },
    )
    await parse_step(world.ctx, _job(learner, material))

    (page,) = await world.db.fetch_all(
        "SELECT page, text, image_key FROM content.material_pages WHERE material_id = %(m)s",
        {"m": material},
    )
    assert page["page"] == 1
    assert page["text"] == "Chain rule\n(f(g(x)))' = f'(g(x)) g'(x)"
    assert world.storage.objects[page["image_key"]][0] == WEBP
    recognizer_call = world.provider.calls_for(schema_name="SheetReading")
    assert len(recognizer_call) == 1 and recognizer_call[0].images


async def test_unknown_material_fails_permanently(world: _World) -> None:
    learner = await create_learner(world.db)
    with pytest.raises(JobFailedPermanently):
        await parse_step(world.ctx, _job(learner, uuid4()))
    other, _, material = await world.upload()
    with pytest.raises(JobFailedPermanently):  # a material of another learner is not found
        await parse_step(world.ctx, _job(learner, material))
    assert other != learner


async def test_a_final_failure_marks_the_material_failed(world: _World) -> None:
    from dataclasses import replace

    from quack.ops.material_parse import handle_material_parse

    learner, _, material = await world.upload()
    job = _job(learner, material)  # no pages pushed: the compute scenario raises, attempt 1 of 5

    async def status() -> str:
        row = await world.db.fetch_one(
            "SELECT status FROM content.materials WHERE material_id = %(m)s", {"m": material}
        )
        assert row is not None
        return str(row["status"])

    with pytest.raises(Exception):  # noqa: B017 - any error of the parse
        await handle_material_parse(job, world.ctx)
    assert await status() == "uploaded"  # the runner will retry
    with pytest.raises(Exception):  # noqa: B017
        await handle_material_parse(replace(job, attempt=5), world.ctx)
    assert await status() == "failed"  # the last attempt
    await world.db.execute(
        "UPDATE content.materials SET status = 'parsed' WHERE material_id = %(m)s", {"m": material}
    )
    with pytest.raises(JobFailedPermanently):
        await handle_material_parse(_job(learner, uuid4()), world.ctx)  # unknown: not ours
    assert await status() == "parsed"


async def test_a_parsed_material_is_not_parsed_again(world: _World) -> None:
    learner, _, material = await world.upload()
    await world.db.execute(
        "UPDATE content.materials SET status = 'parsed' WHERE material_id = %(m)s", {"m": material}
    )
    await parse_step(world.ctx, _job(learner, material))
    assert world.scenario.requests == [] and world.provider.calls == []


def test_sheet_reading_is_the_schema_of_the_recognizer() -> None:
    """The materials package may not import `quack.canvas`, so it keeps a copy of the sheet
    schema; the copy must not drift from the one the prompt `recognizer@2` is written for."""
    from quack.canvas.recognition import RecognizerSheetOutput

    def shape(model: type[BaseModel]) -> Any:
        schema = model.model_json_schema()
        return re.sub(r"[A-Z][A-Za-z]+", "X", str(schema).replace(model.__name__, ""))

    assert (
        SheetReading.model_json_schema()["required"]
        == RecognizerSheetOutput.model_json_schema()["required"]
    )
    ours = SheetReading.model_json_schema()["$defs"]
    theirs = RecognizerSheetOutput.model_json_schema()["$defs"]
    assert sorted(ours) == sorted(["SheetLine", "SheetBox"]) == sorted(theirs)
    for name in ours:
        assert ours[name]["properties"] == theirs[name]["properties"]
    assert shape(SheetReading)


def test_the_kind_is_registered_by_the_procedure_of_issue_173() -> None:
    """JOB_KINDS, KIND_LLM_ROLES and HANDLER_MODULES carry `material.parse`; importing the
    handler module registers it (the handler is glue in the assembly layer, D-C-200)."""
    import importlib

    assert "material.parse" in JOB_KINDS
    assert KIND_LLM_ROLES["material.parse"] == ("material_parser",)
    assert "quack.ops.material_parse" in HANDLER_MODULES
    importlib.import_module("quack.ops.material_parse")
    assert "material.parse" in JOB_HANDLERS


def test_the_prompt_material_parser_v1_has_its_contract() -> None:
    template = PromptRegistry().load("material_parser", 1)
    assert set(re.findall(r"\{\{\s*(\w+)\s*\}\}", template.text)) == {"chunks"}
    assert MaterialParserOutput.model_json_schema()["required"] == [
        "title",
        "chunks",
        "deadlines",
        "seminar_tasks",
    ]
    assert "deadlines" in template.text and "seminar_tasks" in template.text


def test_the_prompt_material_parser_v2_has_its_contract() -> None:
    from quack.llm.strict_schema import to_strict_json_schema
    from quack.prompts.registry import PROMPTS_DIR
    from tests.llm.prompt_contracts import field_names, output_contract_block

    path = PROMPTS_DIR / "material_parser" / "v2.md"
    raw = path.read_bytes()
    assert b"\r\n" not in raw, "material_parser/v2.md must use LF line endings only"
    text = raw.decode("utf-8")
    assert set(re.findall(r"\{\{\s*(\w+)\s*\}\}", text)) == {"mode", "chunks"}
    for model in (MaterialParserV2Output, MaterialTocOutput):
        schema = to_strict_json_schema(model)
        assert schema["additionalProperties"] is False
    block = output_contract_block(text)
    assert block is not None
    allowed = field_names(MaterialParserV2Output) | field_names(MaterialTocOutput)
    undeclared = sorted({n for n in re.findall(r"`([^`]+)`", block) if n not in allowed})
    assert undeclared == []


# --- the rest of WP20.2: deadlines of a syllabus (D-C-213), a scan without text (D-C-214) ------

SYLLABUS = (
    "Course syllabus\n\nColloquium on derivatives: 2099-06-15. Written test on integrals: "
    "2099-07-01 10:30. The exam of 2020-01-10 is over."
)


def _syllabus_answer(request: Any) -> dict[str, Any]:
    return {
        "title": "Calculus syllabus",
        "chunks": [{"n": 1, "section": "Derivatives", "notation": [], "builds_on": []}],
        "deadlines": [
            {
                "label": "Colloquium",
                "date": "2099-06-15",
                "control_type": "colloquium_proofs",
                "covers": ["Derivatives", "Limits"],
            },
            {
                "label": "colloquium",  # the same one written in another case
                "date": "2099-06-15",
                "control_type": "colloquium_proofs",
                "covers": [],
            },
            {
                "label": "Test",
                "date": "2099-07-01T10:30",
                "control_type": "tasks",
                "covers": ["Integrals"],
            },
            {
                "label": "Old exam",
                "date": "2020-01-10",
                "control_type": "oral_theory",
                "covers": [],
            },
            {"label": "No year", "date": "15 June", "control_type": "tasks", "covers": []},
        ],
        "seminar_tasks": [],
    }


def _syllabus_toc_answer(request: Any) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "name": "Derivatives",
                "description": "Derivatives section.",
                "core_skill_name": "Compute derivatives",
                "builds_on": [],
            }
        ],
        "chunk_candidates": [{"n": 1, "candidate_index": 0}],
        "task_candidates": [],
    }


async def _structure_job(world: _World, learner: UUID, material: UUID) -> ClaimedJob:
    (queued,) = await world.db.fetch_all(
        "SELECT payload FROM ops.jobs WHERE kind = 'material.parse'"
    )
    payload = {k: v for k, v in queued["payload"].items() if k != "material_id"}
    return _job(learner, material, **payload)


async def test_a_syllabus_gives_hubs_with_deadlines(world: _World) -> None:
    """WP20.2 п.4: a syllabus gives the structure of the course and its deadlines: a hub with
    the topics it covers and `hub.deadline_set {source: syllabus}`; a date in the past, a date
    without a year and a repeat of the same one are not written (D-C-213)."""
    learner, domain, material = await world.upload()
    await world.db.execute(
        "UPDATE core.learners SET settings = settings || "
        '\'{"timezone": "Europe/Moscow"}\'::jsonb WHERE learner_id = %(l)s',
        {"l": learner},
    )
    world.provider.respond("MaterialParserV2Output", _syllabus_answer)
    world.provider.respond("MaterialTocOutput", _syllabus_toc_answer)
    world.pdf_pages(SYLLABUS)
    await parse_step(world.ctx, _job(learner, material))
    job = await _structure_job(world, learner, material)
    assert len(job.payload["deadlines"]) == 4  # a repeat of the same one is merged

    resolver = _Resolver()
    await structure_step(world.ctx, job, resolver)

    topics = {c.name for call in resolver.calls for c in call}
    assert {"Derivatives", "Limits", "Integrals"} <= topics  # the covers are topics of the course
    hubs = {h["payload"]["name"]: h for h in await world.types(learner, "hub.created")}
    assert set(hubs) == {"Colloquium", "Test"}
    colloquium = hubs["Colloquium"]["payload"]
    assert colloquium["control_type"] == "colloquium_proofs" and colloquium["kind"] == "material"
    assert colloquium["origin"] == "material" and colloquium["domain_id"] == str(domain)
    deadlines = {
        d["payload"]["hub_id"]: d["payload"] for d in await world.types(learner, "hub.deadline_set")
    }
    assert len(deadlines) == 2
    moscow = ZoneInfo("Europe/Moscow")
    day_end = int(datetime(2099, 6, 15, 23, 59, tzinfo=moscow).timestamp() * 1000)
    at_time = int(datetime(2099, 7, 1, 10, 30, tzinfo=moscow).timestamp() * 1000)
    assert deadlines[colloquium["hub_id"]] == {
        "hub_id": colloquium["hub_id"],
        "deadline_at_ms": day_end,
        "source": "syllabus",
    }
    assert deadlines[hubs["Test"]["payload"]["hub_id"]]["deadline_at_ms"] == at_time
    covered = {
        t["payload"]["hub_id"]: t["payload"]["topics"]
        for t in await world.types(learner, "hub.topics_changed")
    }
    assert {x["topic_id"] for x in covered[colloquium["hub_id"]]} == {
        str(resolver.topics["Derivatives"].topic_id),
        str(resolver.topics["Limits"].topic_id),
    }
    levels = world.settings.control_type_levels["colloquium_proofs"]
    assert all(x["required_levels"] == levels for x in covered[colloquium["hub_id"]])

    # law 5: a repeat of the second step writes nothing twice
    before = len(await world.types(learner, "hub.created", "hub.deadline_set"))
    await structure_step(world.ctx, job, resolver)
    assert len(await world.types(learner, "hub.created", "hub.deadline_set")) == before


async def test_a_material_without_dates_writes_no_deadline(world: _World) -> None:
    learner, _, material = await world.upload()
    world.pdf_pages(PAGE_ONE)
    await parse_step(world.ctx, _job(learner, material))
    await structure_step(world.ctx, await _structure_job(world, learner, material), _Resolver())
    assert await world.types(learner, "hub.deadline_set") == []


async def test_deadlines_are_capped_by_the_setting(world: _World) -> None:
    learner, _, material = await world.upload()
    cap = world.settings.material_deadlines_max

    def many(request: Any) -> dict[str, Any]:
        found = [
            {"label": f"Test {i}", "date": "2099-06-15", "control_type": "tasks", "covers": []}
            for i in range(cap + 3)
        ]
        return {"title": "", "chunks": [], "deadlines": found, "seminar_tasks": []}

    world.provider.respond("MaterialParserV2Output", many)
    world.pdf_pages(SYLLABUS)
    await parse_step(world.ctx, _job(learner, material))
    await structure_step(world.ctx, await _structure_job(world, learner, material), _Resolver())
    assert len(await world.types(learner, "hub.deadline_set")) == cap


def _sheet_line(text: str) -> dict[str, Any]:
    return {
        "kind": "text",
        "text": text,
        "sympy": None,
        "bbox": {"left": 0.1, "top": 0.1, "right": 0.9, "bottom": 0.2},
        "confidence": 0.9,
        "crossed_out": False,
    }


async def test_a_pdf_page_without_a_text_layer_is_read_by_the_recognizer(world: _World) -> None:
    """D-C-214: a scanned page (no text from PyMuPDF) is read from its image by `recognizer` in
    the sheet mode; a page with a text layer is not sent to it."""
    learner, _, material = await world.upload()
    world.pdf_pages("", PAGE_TWO, "  \n ")
    world.provider.respond(
        "SheetReading", lambda _r: {"lines": [_sheet_line("Scanned: chain rule"), _sheet_line("")]}
    )
    await parse_step(world.ctx, _job(learner, material))

    rows = await world.db.fetch_all(
        "SELECT page, text FROM content.material_pages WHERE material_id = %(m)s ORDER BY page",
        {"m": material},
    )
    assert [(r["page"], r["text"]) for r in rows] == [
        (1, "Scanned: chain rule"),
        (2, PAGE_TWO),
        (3, "Scanned: chain rule"),
    ]
    calls = world.provider.calls_for(schema_name="SheetReading")
    assert len(calls) == 2 and all(c.images for c in calls)
    assert len(world.scenario.requests) == 1  # only the PDF went to compute: no alignment


async def test_a_scan_is_read_up_to_the_cap_of_pages(world: _World) -> None:
    learner, _, material = await world.upload()
    cap = world.settings.material_scan_max_pages
    world.pdf_pages(*[""] * (cap + 2))
    world.provider.respond("SheetReading", lambda _r: {"lines": [_sheet_line("Text")]})
    await parse_step(world.ctx, _job(learner, material))
    assert len(world.provider.calls_for(schema_name="SheetReading")) == cap
    rows = await world.db.fetch_all(
        "SELECT text FROM content.material_pages WHERE material_id = %(m)s", {"m": material}
    )
    assert len(rows) == cap + 2 and sum(1 for r in rows if r["text"] == "") == 2


DECK = b"PK\x03\x04"  # a zip: the structure decides, and the test deck is built below


def _deck_bytes() -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p/>")
    return buffer.getvalue()


async def test_a_presentation_is_read_slide_by_slide_and_is_readable_before_the_graph_work(
    world: _World,
) -> None:
    """A lecture in PowerPoint: the slides become pages (title, text, notes of the speaker), with no
    page image; from the moment the pages are stored the material is `read`, and a parse that
    fails later keeps that status (the text is there for the teacher; the graph work is not)."""
    learner, _, material = await world.upload(_deck_bytes())
    world.slides(
        ("Derivatives", "Chain rule: f'(g(x)) g'(x)", "Stress the order"),
        ("", "A slide with no title", ""),
    )

    await parse_step(world.ctx, _job(learner, material, trigger_seq=3))

    rows = await world.db.fetch_all(
        "SELECT page, text, image_key FROM content.material_pages "
        "WHERE material_id = %(m)s ORDER BY page",
        {"m": material},
    )
    assert [(r["page"], r["text"]) for r in rows] == [
        (1, "Derivatives\nChain rule: f'(g(x)) g'(x)\nNotes: Stress the order"),
        (2, "A slide with no title"),
    ]
    assert all(r["image_key"] is None for r in rows) and world.storage.objects == {
        k: v for k, v in world.storage.objects.items() if "/pages/" not in k
    }
    row = await world.db.fetch_one(
        "SELECT status, pages_count, mime FROM content.materials WHERE material_id = %(m)s",
        {"m": material},
    )
    assert row is not None and (row["status"], row["pages_count"]) == ("read", 2)

    # the structure step cannot be made (say, no embeddings): the last attempt marks the failure,
    # and a material whose pages are read stays readable
    await mark_failed(world.ctx.db, _job(learner, material))
    again = await world.db.fetch_one(
        "SELECT status FROM content.materials WHERE material_id = %(m)s", {"m": material}
    )
    assert again is not None and again["status"] == "read"


async def test_a_material_that_was_never_read_is_marked_failed(world: _World) -> None:
    learner, _, material = await world.upload()
    await mark_failed(world.ctx.db, _job(learner, material))
    row = await world.db.fetch_one(
        "SELECT status FROM content.materials WHERE material_id = %(m)s", {"m": material}
    )
    assert row is not None and row["status"] == "failed"


def test_slide_text_puts_the_title_first_and_the_notes_last() -> None:
    from quack.materials.parse import slide_text

    assert slide_text("T", "body", "n") == "T\nbody\nNotes: n"
    assert slide_text("", "body", "") == "body"
    assert slide_text("T\x00", "", "") == "T"


async def test_a_presentation_compute_refuses_fails_the_job_for_good(world: _World) -> None:
    """compute answers 422 for a file that is not what its type says: retrying cannot help, so the
    job stops at once (not five attempts), and the material is marked failed."""
    from quack.compute_client.client import ComputeRejected, ComputeUnavailableError
    from quack.jobs.types import JobFailedPermanently

    assert issubclass(ComputeRejected, ComputeUnavailableError)  # callers that retry still work
    learner, _, material = await world.upload(_deck_bytes())
    world.scenario.push_for(SLIDES, 422)

    with pytest.raises(JobFailedPermanently, match="cannot be read"):
        await parse_step(world.ctx, _job(learner, material, trigger_seq=3))

    await mark_failed(world.ctx.db, _job(learner, material))
    row = await world.db.fetch_one(
        "SELECT status FROM content.materials WHERE material_id = %(m)s", {"m": material}
    )
    assert row is not None and row["status"] == "failed"


async def test_a_later_material_does_not_wait_for_an_older_one(world: _World) -> None:
    """Upload order no longer blocks `structure_step` (`_wait_for_older` removed;
    `lock_key = domain:{id}` serializes writes)."""
    learner, domain, _older = await world.upload()
    later = (
        await upload_material(
            world.db, world.storage, world.settings, learner_id=learner, domain_id=domain, data=PDF
        )
    ).material_id
    world.pdf_pages(PAGE_ONE)
    await parse_step(world.ctx, _job(learner, later))
    job = await _structure_job(world, learner, later)

    await structure_step(world.ctx, job, _Resolver())
    assert len(await world.types(learner, "material.parsed")) == 1


class _FlakyResolver(_Resolver):
    """Fails once before succeeding on retry, verifying Law 5 / Law 7 in `structure_step`."""

    def __init__(self) -> None:
        super().__init__()
        self.attempt = 0

    async def __call__(
        self,
        ctx: JobContext,
        *,
        learner_id: UUID,
        domain_id: UUID,
        candidates: Sequence[TopicCandidateSpec],
        source_refs: Sequence[dict[str, Any]] = (),
    ) -> list[ResolvedTopic]:
        self.attempt += 1
        if self.attempt == 1:
            raise LLMUnavailableError("builder@2: provider timeout", reason="timeout")
        return await super().__call__(
            ctx,
            learner_id=learner_id,
            domain_id=domain_id,
            candidates=candidates,
            source_refs=source_refs,
        )


async def test_a_retry_of_the_structure_step_commits_once(world: _World) -> None:
    """Law 7: a resolver failure leaves `material.parsed` and `chunk.indexed` unwritten;
    the retry commits cleanly once."""
    learner, _, material = await world.upload()
    world.pdf_pages(PAGE_ONE, PAGE_TWO)
    await parse_step(world.ctx, _job(learner, material))
    job = await _structure_job(world, learner, material)
    resolver = _FlakyResolver()

    with pytest.raises(LLMUnavailableError):
        await structure_step(world.ctx, job, resolver)
    assert await world.types(learner, "material.parsed", "chunk.indexed") == []

    await structure_step(world.ctx, job, resolver)
    assert len(await world.types(learner, "material.parsed")) == 1
    assert len(await world.types(learner, "chunk.indexed")) == 2
