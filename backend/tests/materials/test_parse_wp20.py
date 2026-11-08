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

from quack.compute_client.contracts import AlignResponse, PdfPage, PdfPagesResponse
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.jobs.kinds import JOB_KINDS, KIND_LLM_ROLES
from quack.jobs.runner import JOB_HANDLERS, JobContext
from quack.jobs.types import ClaimedJob, JobFailedPermanently
from quack.llm.gateway import LLMGateway
from quack.materials.library import upload_material
from quack.materials.parse import (
    MaterialParserOutput,
    SheetReading,
    SubjectSpec,
    parse_step,
    split_chunks,
    structure_step,
)
from quack.prompts.registry import PromptRegistry
from quack.runtime import HANDLER_MODULES
from tests.db.fixtures import create_learner
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import ScriptedLLMProvider
from tests.support.storage import InMemoryObjectStore

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PAGES = "/v1/pdf/pages"
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
    """One mark per chunk of the input: the numbers are read from the prompt."""
    numbers = [int(n) for n in re.findall(r"^\[(\d+)\]", request.prompt, flags=re.M)]
    title = "Calculus lectures"
    chunks = []
    for n in numbers:
        text = request.prompt.split(f"[{n}]", 1)[1]
        if "Chain rule" in text:
            chunks.append(
                {
                    "n": n,
                    "topic": "Derivatives",
                    "notation": ["f'(g(x))"],
                    "teaches": ["Chain rule"],
                    "uses": ["Power rule"],
                }
            )
        else:
            chunks.append(
                {
                    "n": n,
                    "topic": "Integrals",
                    "notation": ["int u dv"],
                    "teaches": ["Integration by parts"],
                    "uses": ["Product rule", "chain rule"],
                }
            )
    return {"title": title, "chunks": chunks, "deadlines": [], "seminar_tasks": []}


class _World:
    def __init__(self, db: Db, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.storage = InMemoryObjectStore()
        self.scenario = ComputeScenario()
        self.provider = ScriptedLLMProvider()
        self.provider.respond("MaterialParserOutput", _parser_answer)
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

    async def types(self, learner: UUID, *types: str) -> list[dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT type, payload, domain_id, idempotency_key FROM core.events "
            "WHERE learner_id = %(l)s AND type = ANY(%(t)s) ORDER BY seq",
            {"l": learner, "t": list(types)},
        )


class _Resolver:
    """The reconciliation of B behind the seam: an id for every subject, remembered by name."""

    def __init__(self) -> None:
        self.calls: list[list[SubjectSpec]] = []
        self.ids: dict[tuple[str, str], UUID] = {}

    async def __call__(
        self, ctx: JobContext, *, learner_id: UUID, domain_id: UUID, subjects: Sequence[SubjectSpec]
    ) -> dict[tuple[str, str], UUID]:
        self.calls.append(list(subjects))
        return {(s.kind, s.name): self.ids.setdefault((s.kind, s.name), uuid4()) for s in subjects}


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

    # one reconciliation per batch of subjects; topics first, then skills with their order
    subjects = [s for call in resolver.calls for s in call]
    by_name = {(s.kind, s.name): s for s in subjects}
    assert {("topic", "Derivatives"), ("topic", "Integrals")} <= set(by_name)
    chain = by_name[("skill", "Chain rule")]
    assert chain.topic == "Derivatives" and chain.requires == ("Power rule",)
    parts = by_name[("skill", "Integration by parts")]
    assert parts.topic == "Integrals" and set(parts.requires) == {"Product rule", "Chain rule"}
    # the same name written in another case is one subject
    assert [s.name for s in subjects if s.name.casefold() == "chain rule"] == ["Chain rule"]

    chunks = await world.types(learner, "chunk.indexed")
    assert [c["payload"]["page"] for c in chunks] == [1, 2]
    first = chunks[0]["payload"]
    assert first["material_id"] == str(material) and first["text"] == PAGE_ONE
    assert first["notation"] == ["f'(g(x))"]
    assert first["teaches"] == [str(resolver.ids[("skill", "Chain rule")])]
    assert first["uses"] == [str(resolver.ids[("skill", "Power rule")])]
    assert chunks[0]["domain_id"] == domain
    hub, topics = await world.types(learner, "hub.created", "hub.topics_changed")
    assert hub["payload"]["kind"] == "material" and hub["payload"]["origin"] == "material"
    assert hub["payload"]["name"] == "Calculus lectures" and hub["payload"]["domain_id"] == str(
        domain
    )
    assert {t["topic_id"] for t in topics["payload"]["topics"]} == {
        str(resolver.ids[("topic", "Derivatives")]),
        str(resolver.ids[("topic", "Integrals")]),
    }
    assert all(t["required_levels"] == [3] for t in topics["payload"]["topics"])
    (parsed,) = await world.types(learner, "material.parsed")
    assert parsed["payload"] == {"material_id": str(material), "pages_count": 2}
    row = await world.db.fetch_one(
        "SELECT status, pages_count FROM content.materials WHERE material_id = %(m)s",
        {"m": material},
    )
    assert row == {"status": "parsed", "pages_count": 2}

    # law 5: a repeat of either step writes nothing twice and asks nobody
    before = len(await world.types(learner, "chunk.indexed", "hub.created", "material.parsed"))
    await structure_step(world.ctx, structure_job, resolver)
    await parse_step(world.ctx, _job(learner, material))
    assert (
        len(await world.types(learner, "chunk.indexed", "hub.created", "material.parsed")) == before
    )
    assert len(world.provider.calls) == 1
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
            {"n": n, "topic": "T", "notation": [], "teaches": [f"Skill {n}"], "uses": []}
            for n in numbers[1:]  # the first chunk of every batch is skipped
        ]
        marks.append({"n": 999, "topic": "T", "notation": [], "teaches": ["Ghost"], "uses": []})
        marks.append({**marks[0]})  # a repeated number
        return {"title": "", "chunks": marks, "deadlines": [], "seminar_tasks": []}

    world.provider.respond("MaterialParserOutput", answer)
    await parse_step(world.ctx, _job(learner, material))

    assert len(world.provider.calls) == 3
    (queued,) = await world.db.fetch_all(
        "SELECT payload FROM ops.jobs WHERE kind = 'material.parse'"
    )
    marks = queued["payload"]["chunks"]
    assert len(marks) == len(texts)
    taught = [m["teaches"] for m in marks]
    assert ["Ghost"] not in taught
    assert sum(1 for t in taught if t == []) == 3
    assert all(len(t) <= 1 for t in taught)
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
    schema; the copy must not drift from the one the prompt `recognizer@1` is written for."""
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


# --- the rest of WP20.2: deadlines of a syllabus (D-C-213), a scan without text (D-C-214) ------

SYLLABUS = (
    "Course syllabus\n\nColloquium on derivatives: 2099-06-15. Written test on integrals: "
    "2099-07-01 10:30. The exam of 2020-01-10 is over."
)


def _syllabus_answer(request: Any) -> dict[str, Any]:
    return {
        "title": "Calculus syllabus",
        "chunks": [{"n": 1, "topic": "Derivatives", "notation": [], "teaches": [], "uses": []}],
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
    world.provider.respond("MaterialParserOutput", _syllabus_answer)
    world.pdf_pages(SYLLABUS)
    await parse_step(world.ctx, _job(learner, material))
    job = await _structure_job(world, learner, material)
    assert len(job.payload["deadlines"]) == 4  # a repeat of the same one is merged

    resolver = _Resolver()
    await structure_step(world.ctx, job, resolver)

    topics = {s.name for call in resolver.calls for s in call if s.kind == "topic"}
    assert {"Derivatives", "Limits", "Integrals"} <= topics  # the covers are topics of the course
    hubs = {h["payload"]["name"]: h for h in await world.types(learner, "hub.created")}
    assert set(hubs) == {"Calculus syllabus", "Colloquium", "Test"}
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
        str(resolver.ids[("topic", "Derivatives")]),
        str(resolver.ids[("topic", "Limits")]),
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

    world.provider.respond("MaterialParserOutput", many)
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
