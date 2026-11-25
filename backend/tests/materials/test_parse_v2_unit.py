"""Non-DB unit tests for the v2 material parsing and TOC pipeline (`quack.materials.parse`)."""

from __future__ import annotations

import base64
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from quack.compute_client.contracts import PdfPage, PdfPagesResponse
from quack.config import Settings
from quack.events.log import NewEvent
from quack.jobs.rules_p4 import JobPlan
from quack.jobs.runner import JobContext
from quack.jobs.types import ClaimedJob
from quack.llm.gateway import LLMGateway
from quack.materials import parse as parse_mod
from quack.materials.parse import (
    SEMINAR_TASKS_KIND,
    STEP_STRUCTURE,
    BuildsOnClaim,
    MaterialParserV2Output,
    MaterialTocOutput,
    ResolvedTopic,
    SeminarSpec,
    TopicCandidateSpec,
    _spec_of,
    parse_step,
    structure_step,
)
from quack.prompts.registry import PROMPTS_DIR
from tests.llm.prompt_contracts import field_names, output_contract_block
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import ScriptedLLMProvider
from tests.support.storage import InMemoryObjectStore


class _FakeDb:
    def __init__(
        self,
        *,
        material_id: UUID,
        domain_id: UUID,
        session_id: UUID | None = None,
        status: str = "uploaded",
        pages: list[tuple[int, str]] | None = None,
        timezone: str | None = "UTC",
    ) -> None:
        self.material_id = material_id
        self.domain_id = domain_id
        self.session_id = session_id
        self.status = status
        self.pages: list[tuple[int, str]] = list(pages or [])
        self.timezone = timezone
        self.status_updates: list[tuple[str, int | None]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetch_one(
        self, query: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if "FROM content.materials" in query:
            return {
                "material_id": self.material_id,
                "domain_id": self.domain_id,
                "session_id": self.session_id,
                "object_key": f"learners/x/materials/{self.material_id}/original.pdf",
                "mime": "application/pdf",
                "status": self.status,
                "created_at": datetime.now(),
            }
        if "FROM core.learners" in query:
            return {"tz": self.timezone} if self.timezone else None
        return None

    async def fetch_all(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        if "FROM content.material_pages" in query:
            return [{"page": p, "text": t} for p, t in self.pages]
        return []

    async def execute(self, query: str, params: dict[str, Any] | None = None) -> None:
        assert params is not None
        if "INSERT INTO content.material_pages" in query:
            self.pages.append((int(params["page"]), str(params["text"])))
        elif "UPDATE content.materials" in query:
            self.status = str(params["status"])
            self.status_updates.append((self.status, params.get("pages")))


def _make_ctx(
    db: _FakeDb,
    settings: Settings,
    provider: ScriptedLLMProvider,
    scenario: ComputeScenario,
    storage: InMemoryObjectStore,
) -> JobContext:
    return cast(
        JobContext,
        SimpleNamespace(
            db=db,
            settings=settings,
            storage=storage,
            compute=scripted_compute(scenario),
            llm=LLMGateway(provider, settings, db=cast(Any, db)),
            redis=None,
            neo4j=None,
        ),
    )


def _job(learner_id: UUID, material_id: UUID, payload: dict[str, Any] | None = None) -> ClaimedJob:
    return ClaimedJob(
        id=uuid4(),
        kind="material.parse",
        learner_id=learner_id,
        lock_key="",
        coalesce_key=None,
        priority=0,
        payload={"material_id": str(material_id), "trigger_seq": 11, **(payload or {})},
        attempt=1,
        max_attempts=5,
        job_key=f"material.parse:{learner_id}:{material_id}",
    )


def test_v2_prompt_lf_and_tm5_contract() -> None:
    path = PROMPTS_DIR / "material_parser" / "v2.md"
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    text = raw.decode("utf-8")
    assert set(re.findall(r"\{\{\s*(\w+)\s*\}\}", text)) == {"mode", "chunks"}
    block = output_contract_block(text)
    assert block is not None
    allowed = field_names(MaterialParserV2Output) | field_names(MaterialTocOutput)
    assert sorted({n for n in re.findall(r"`([^`]+)`", block) if n not in allowed}) == []


async def test_parse_step_runs_parse_and_single_toc_call(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    learner, domain, material = uuid4(), uuid4(), uuid4()
    db = _FakeDb(material_id=material, domain_id=domain)
    storage = InMemoryObjectStore()
    await storage.put(f"learners/x/materials/{material}/original.pdf", b"%PDF", "application/pdf")
    scenario = ComputeScenario()
    scenario.push_for(
        "/v1/pdf/pages",
        PdfPagesResponse(
            pages=[
                PdfPage(
                    page=1,
                    text="Title page\nCalculus 101",
                    image_webp_b64=base64.b64encode(b"webp").decode(),
                ),
                PdfPage(
                    page=2,
                    text="Chain rule\nDerivative of f(g(x)). Needs power rule first.",
                    image_webp_b64=base64.b64encode(b"webp").decode(),
                ),
            ]
        ),
    )
    provider = ScriptedLLMProvider()
    provider.respond(
        "MaterialParserV2Output",
        lambda _r: {
            "title": "Calculus 101",
            "chunks": [
                {"n": 1, "section": None, "notation": [], "builds_on": []},
                {
                    "n": 2,
                    "section": "Chain rule for composite functions",
                    "notation": ["f'(g(x))"],
                    "builds_on": [{"concept": "Power rule", "quote": "Needs power rule first"}],
                },
            ],
            "deadlines": [],
            "seminar_tasks": [
                {
                    "statement": "Differentiate sin(x^2).",
                    "answer": "2x cos(x^2)",
                    "chunk": "P2",
                    "page": 2,
                }
            ],
        },
    )
    provider.respond(
        "MaterialTocOutput",
        lambda _r: {
            "candidates": [
                {
                    "name": "Chain rule",
                    "description": "Differentiating composite functions.",
                    "core_skill_name": "Apply the chain rule",
                    "builds_on": [{"concept": "Power rule", "quote": "Needs power rule first"}],
                }
            ],
            "chunk_candidates": [
                {"n": 1, "candidate_index": None},
                {"n": 2, "candidate_index": 0},
            ],
            "task_candidates": [{"task_index": 0, "candidate_index": 0}],
        },
    )
    enqueued: list[JobPlan] = []

    async def fake_enqueue(_db: Any, plan: JobPlan, **_kw: Any) -> bool:
        enqueued.append(plan)
        return True

    monkeypatch.setattr(parse_mod.queue_p4, "enqueue", fake_enqueue)
    ctx = _make_ctx(db, settings, provider, scenario, storage)
    await parse_step(ctx, _job(learner, material))

    assert len(provider.calls_for(schema_name="MaterialParserV2Output")) == 1
    toc_calls = provider.calls_for(schema_name="MaterialTocOutput")
    assert len(toc_calls) == 1
    assert toc_calls[0].reasoning_effort == "low"
    assert len(enqueued) == 1
    plan = enqueued[0]
    assert plan.payload["step"] == STEP_STRUCTURE
    assert plan.payload["toc_candidates"] == [
        {
            "name": "Chain rule",
            "description": "Differentiating composite functions.",
            "core_skill_name": "Apply the chain rule",
            "builds_on": [{"concept": "Power rule", "quote": "Needs power rule first"}],
        }
    ]
    assert [c["candidate_index"] for c in plan.payload["chunks"]] == [None, 0]
    assert "teaches" not in plan.payload["chunks"][1]
    assert "uses" not in plan.payload["chunks"][1]
    assert plan.payload["seminar_tasks"][0]["candidate_index"] == 0


async def test_toc_batches_and_merges_when_chunks_exceed_max(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = settings.model_copy(
        update={"material_toc_max_chunks": 2, "material_parser_batch": 10}
    )
    learner, domain, material = uuid4(), uuid4(), uuid4()
    pages = [(i, f"Chunk {i} text about topic {i}.") for i in range(1, 6)]
    db = _FakeDb(material_id=material, domain_id=domain, status="read", pages=pages)
    provider = ScriptedLLMProvider()
    provider.respond(
        "MaterialParserV2Output",
        lambda r: {
            "title": "Big notes",
            "chunks": [
                {"n": int(n), "section": f"Section {n}", "notation": [], "builds_on": []}
                for n in re.findall(r"^\[(\d+)\]", r.prompt, flags=re.M)
            ],
            "deadlines": [],
            "seminar_tasks": [],
        },
    )

    def toc_responder(req: Any) -> dict[str, Any]:
        if "Partial TOC batches" in req.prompt:
            return {
                "candidates": [
                    {
                        "name": "Unified Topic",
                        "description": "Merged description.",
                        "core_skill_name": "Apply unified topic",
                        "builds_on": [],
                    }
                ],
                "chunk_candidates": [{"n": i, "candidate_index": 0} for i in range(1, 6)],
                "task_candidates": [],
            }
        nums = [int(n) for n in re.findall(r"^\[(\d+)\]", req.prompt, flags=re.M)]
        return {
            "candidates": [
                {
                    "name": f"Partial {nums[0]}",
                    "description": "Batch topic.",
                    "core_skill_name": f"Skill {nums[0]}",
                    "builds_on": [],
                }
            ],
            "chunk_candidates": [{"n": n, "candidate_index": 0} for n in nums],
            "task_candidates": [],
        }

    provider.respond("MaterialTocOutput", toc_responder)
    enqueued: list[JobPlan] = []

    async def fake_enqueue(_db: Any, plan: JobPlan, **_kw: Any) -> bool:
        enqueued.append(plan)
        return True

    monkeypatch.setattr(parse_mod.queue_p4, "enqueue", fake_enqueue)
    ctx = _make_ctx(db, small, provider, ComputeScenario(), InMemoryObjectStore())
    await parse_step(ctx, _job(learner, material))

    toc_calls = provider.calls_for(schema_name="MaterialTocOutput")
    assert len(toc_calls) == 4  # 3 batches (2+2+1) + 1 merge
    assert all(c.reasoning_effort == "low" for c in toc_calls)
    assert len(enqueued[0].payload["toc_candidates"]) == 1
    assert [c["candidate_index"] for c in enqueued[0].payload["chunks"]] == [0, 0, 0, 0, 0]


async def test_structure_step_links_chunks_skips_material_hub_and_preserves_syllabus_and_seminar(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    learner, domain, material = uuid4(), uuid4(), uuid4()
    pages = [
        (1, "Cover page"),
        (2, "Chain rule lesson"),
    ]
    db = _FakeDb(material_id=material, domain_id=domain, status="read", pages=pages)
    chunks = parse_mod.split_chunks(settings, material, pages)
    structure_payload = {
        "step": STEP_STRUCTURE,
        "title": "Calculus",
        "deadlines": [
            {
                "label": "Colloquium",
                "date": "2099-06-15",
                "control_type": "colloquium_proofs",
                "covers": ["Chain rule", "Limits"],
            }
        ],
        "toc_candidates": [
            {
                "name": "Chain rule",
                "description": "Composite differentiation.",
                "core_skill_name": "Apply chain rule",
                "builds_on": [{"concept": "Power rule", "quote": "Needs power rule"}],
            }
        ],
        "chunks": [
            {
                "chunk_id": str(chunks[0].chunk_id),
                "section": "",
                "notation": [],
                "builds_on": [],
                "candidate_index": None,
            },
            {
                "chunk_id": str(chunks[1].chunk_id),
                "section": "Chain rule",
                "notation": ["f'(g(x))"],
                "builds_on": [],
                "candidate_index": 0,
            },
        ],
        "seminar_tasks": [
            {
                "statement": "Differentiate sin(x^2).",
                "answer": "2x cos(x^2)",
                "chunk_id": str(chunks[1].chunk_id),
                "page": 2,
                "candidate_index": 0,
            }
        ],
    }

    topic_chain, core_chain, extra_chain = uuid4(), uuid4(), uuid4()
    topic_limits, core_limits = uuid4(), uuid4()
    seen_candidates: list[TopicCandidateSpec] = []

    async def fake_resolve(
        ctx: JobContext,
        *,
        learner_id: UUID,
        domain_id: UUID,
        candidates: Sequence[TopicCandidateSpec],
        source_refs: Sequence[dict[str, Any]] = (),
    ) -> list[ResolvedTopic]:
        seen_candidates.extend(candidates)
        return [
            ResolvedTopic(topic_chain, core_chain, (core_chain, extra_chain)),
            ResolvedTopic(topic_limits, core_limits, (core_limits,)),
        ]

    written_events: list[NewEvent] = []
    enqueued: list[JobPlan] = []

    async def fake_append(_db: Any, _learner: UUID, events: Sequence[NewEvent], **_kw: Any) -> None:
        written_events.extend(events)

    async def fake_enqueue(_db: Any, plan: JobPlan, **_kw: Any) -> bool:
        enqueued.append(plan)
        return True

    monkeypatch.setattr(parse_mod, "append_events", fake_append)
    monkeypatch.setattr(parse_mod.queue_p4, "enqueue", fake_enqueue)
    ctx = _make_ctx(db, settings, ScriptedLLMProvider(), ComputeScenario(), InMemoryObjectStore())

    await structure_step(ctx, _job(learner, material, structure_payload), fake_resolve)

    assert [c.name for c in seen_candidates] == ["Chain rule", "Limits"]
    assert seen_candidates[0].builds_on == (BuildsOnClaim("Power rule", "Needs power rule"),)

    by_type: dict[str, list[NewEvent]] = {}
    for ev in written_events:
        by_type.setdefault(ev.type, []).append(ev)

    assert "skill.created" not in by_type
    indexed = by_type["chunk.indexed"]
    assert len(indexed) == 2
    assert indexed[0].payload.teaches == [] and indexed[0].payload.uses == []  # type: ignore[attr-defined]
    assert indexed[1].payload.teaches == [core_chain] and indexed[1].payload.uses == []  # type: ignore[attr-defined]

    # Only syllabus hub created; NO non-syllabus material hub
    hubs = by_type["hub.created"]
    assert len(hubs) == 1
    assert hubs[0].payload.name == "Colloquium"  # type: ignore[attr-defined]
    assert len(by_type["hub.deadline_set"]) == 1
    topics_changed = by_type["hub.topics_changed"][0].payload.topics  # type: ignore[attr-defined]
    assert [t.topic_id for t in topics_changed] == [topic_chain, topic_limits]

    assert len(by_type["material.parsed"]) == 1
    assert db.status == "parsed"

    assert len(enqueued) == 1 and enqueued[0].kind == SEMINAR_TASKS_KIND
    task_payload = enqueued[0].payload["tasks"][0]
    assert task_payload["skill_id"] == str(core_chain)
    assert task_payload["topic_id"] == str(topic_chain)
    assert task_payload["topic_skill_ids"] == [str(core_chain), str(extra_chain)]


def test_spec_of_supports_new_and_legacy_payloads() -> None:
    skill_id, domain_id, topic_id, s2 = uuid4(), uuid4(), uuid4(), uuid4()
    legacy = _spec_of(
        {
            "key": "seminar:m:0",
            "statement": "Solve x=1.",
            "answer": None,
            "skill_id": str(skill_id),
            "skill_name": "Solve linear",
            "topic_name": "Algebra",
            "domain_id": str(domain_id),
            "course_context": "Math",
        }
    )
    assert legacy == SeminarSpec(
        key="seminar:m:0",
        statement="Solve x=1.",
        answer=None,
        skill_id=skill_id,
        skill_name="Solve linear",
        topic_name="Algebra",
        domain_id=domain_id,
        course_context="Math",
        topic_id=None,
        topic_skill_ids=(),
    )

    modern = _spec_of(
        {
            "key": "seminar:m:1",
            "statement": "Solve x=2.",
            "answer": "2",
            "skill_id": str(skill_id),
            "skill_name": "Solve linear",
            "topic_name": "Algebra",
            "domain_id": str(domain_id),
            "course_context": "Math",
            "topic_id": str(topic_id),
            "topic_skill_ids": [str(skill_id), str(s2)],
        }
    )
    assert modern.topic_id == topic_id
    assert modern.topic_skill_ids == (skill_id, s2)
