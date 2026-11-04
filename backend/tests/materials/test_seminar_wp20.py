"""WP20.2: the problems of a material become seminar tasks (ТЗ Ф4 WP20.2; Т-§5.6; #271;
D-C-217). `material_parser@1` returns `seminar_tasks` in its layer 2; the structure step of
`material.parse` hands them, with the id of the skill the chunk teaches, to
`quack.practice.seminar_tasks.create_seminar_task` (stream B, D-B-218) through a seam, and
`reconcile_domain` stays the seam of the skills. The LLM, compute, storage, the graph
reconciliation and the task writer are test doubles."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.agents.reconcile import ReconcileResult, Resolution, Subject
from quack.config import Settings
from quack.db import Db
from quack.jobs.runner import JobContext
from quack.materials.parse import (
    MaterialParserOutput,
    SeminarSpec,
    parse_step,
    structure_step,
)
from quack.practice.seminar_tasks import SeminarTask
from tests.materials.test_parse_wp20 import (
    PAGE_ONE,
    PAGE_TWO,
    _job,
    _parser_answer,
    _Resolver,
    _structure_job,
    _World,
)


@pytest.fixture
def world(pg_db: Db, settings: Settings) -> _World:
    return _World(pg_db, settings)


PROBLEMS = [
    {"statement": "Differentiate sin(x^2).", "answer": "2x cos(x^2)", "chunk": "P1", "page": 1},
    {"statement": "Integrate x e^x.", "answer": None, "chunk": "P2", "page": 2},
    {"statement": "  differentiate   SIN(x^2). ", "answer": None, "chunk": "P1", "page": 1},
    {"statement": "   ", "answer": None, "chunk": "P1", "page": 1},
    {"statement": "A problem of a lost chunk.", "answer": None, "chunk": "P9", "page": 2},
    {"statement": "A problem nowhere.", "answer": None, "chunk": "P9", "page": None},
]


def _answer_with_problems(request: Any) -> dict[str, Any]:
    return _parser_answer(request) | {"seminar_tasks": PROBLEMS}


class _Writer:
    """The seam to `create_seminar_task`: remembers what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, list[SeminarSpec]]] = []
        self.fail = False

    async def __call__(
        self, ctx: JobContext, *, learner_id: UUID, tasks: Sequence[SeminarSpec]
    ) -> None:
        if self.fail:
            raise RuntimeError("the model is down")
        self.calls.append((learner_id, list(tasks)))


async def _parsed(world: _World) -> tuple[UUID, UUID, UUID]:
    learner, domain, material = await world.upload()
    world.provider.respond("MaterialParserOutput", _answer_with_problems)
    world.pdf_pages(PAGE_ONE, PAGE_TWO)
    await parse_step(world.ctx, _job(learner, material))
    return learner, domain, material


def test_the_layer_two_of_the_parser_has_the_seminar_tasks() -> None:
    schema = MaterialParserOutput.model_json_schema()
    assert schema["required"] == ["title", "chunks", "deadlines", "seminar_tasks"]
    item = schema["$defs"]["ParsedSeminarTask"]
    assert item["required"] == ["statement", "answer", "chunk", "page"]
    assert item["additionalProperties"] is False


async def test_parse_step_carries_the_problems_of_the_chunks(world: _World) -> None:
    """The label of a chunk is `P` and its number in the input; a problem of a chunk that is
    not there goes to the first chunk of its page, a problem with neither is dropped, and so
    are an empty statement and a repeat of a statement (case and spaces do not matter)."""
    learner, _, material = await _parsed(world)
    job = await _structure_job(world, learner, material)
    chunks = [c["chunk_id"] for c in job.payload["chunks"]]
    assert [
        (t["statement"], t["answer"], t["chunk_id"], t["page"])
        for t in job.payload["seminar_tasks"]
    ] == [
        ("Differentiate sin(x^2).", "2x cos(x^2)", chunks[0], 1),
        ("Integrate x e^x.", None, chunks[1], 2),
        ("A problem of a lost chunk.", None, chunks[1], 2),
    ]


async def test_the_number_of_problems_is_capped_by_the_setting(world: _World) -> None:
    world.settings = world.settings.model_copy(update={"material_seminar_tasks_max": 1})
    world.ctx = replace(world.ctx, settings=world.settings)
    learner, _, material = await _parsed(world)
    job = await _structure_job(world, learner, material)
    assert [t["statement"] for t in job.payload["seminar_tasks"]] == ["Differentiate sin(x^2)."]


async def test_structure_step_hands_the_problems_to_the_writer(world: _World) -> None:
    """After the skills are linked and before the material is `parsed`: a task per problem
    with the id of the skill the chunk teaches (the first one the reconciliation linked), the
    key `seminar:{material}:{n}`, the topic of the chunk; the answer of the material rides
    along. A repeat of the step, the material being parsed, hands nothing."""
    learner, domain, material = await _parsed(world)
    job = await _structure_job(world, learner, material)
    resolver, writer = _Resolver(), _Writer()

    await structure_step(world.ctx, job, resolver, writer)

    [(who, specs)] = writer.calls
    assert who == learner
    first, second, third = specs
    assert first.key == f"seminar:{material}:0" and second.key == f"seminar:{material}:1"
    assert third.key == f"seminar:{material}:2"
    assert first.skill_id == resolver.ids[("skill", "Chain rule")]
    assert (first.skill_name, first.topic_name) == ("Chain rule", "Derivatives")
    assert (first.statement, first.answer, first.domain_id) == (
        "Differentiate sin(x^2).",
        "2x cos(x^2)",
        domain,
    )
    assert second.skill_id == resolver.ids[("skill", "Integration by parts")]
    assert (second.answer, second.topic_name) == (None, "Integrals")
    assert first.course_context == "Calculus lectures"
    assert (await world.types(learner, "material.parsed"))[0]["payload"]["material_id"] == str(
        material
    )

    await structure_step(world.ctx, job, resolver, writer)
    assert len(writer.calls) == 1


async def test_a_problem_of_a_chunk_that_teaches_nothing_uses_what_the_chunk_uses(
    world: _World,
) -> None:
    """A page of a problem set teaches nothing, it takes skills as known: the problem goes
    to the first of them; a chunk with no skill at all gives no task, and the material is
    parsed all the same."""
    learner, _, material = await world.upload()

    def parser(request: Any) -> dict[str, Any]:
        base = _parser_answer(request)
        base["chunks"][0] |= {"teaches": [], "uses": ["Power rule", "Chain rule"]}
        base["chunks"][1] |= {"teaches": [], "uses": []}
        return base | {"seminar_tasks": PROBLEMS[:2]}

    world.provider.respond("MaterialParserOutput", parser)
    world.pdf_pages(PAGE_ONE, PAGE_TWO)
    await parse_step(world.ctx, _job(learner, material))
    resolver, writer = _Resolver(), _Writer()

    await structure_step(
        world.ctx, await _structure_job(world, learner, material), resolver, writer
    )

    [(_, [only])] = writer.calls
    assert only.statement == "Differentiate sin(x^2)."
    assert only.skill_id == resolver.ids[("skill", "Power rule")]
    assert len(await world.types(learner, "material.parsed")) == 1


async def test_a_failing_writer_leaves_the_material_unparsed_and_the_repeat_finishes(
    world: _World,
) -> None:
    """Law 7: a step that fails commits nothing of the material; the tasks are idempotent by
    key, so the retry of the job gives the same ones and ends the step."""
    learner, _, material = await _parsed(world)
    job = await _structure_job(world, learner, material)
    resolver, writer = _Resolver(), _Writer()
    writer.fail = True

    with pytest.raises(RuntimeError):
        await structure_step(world.ctx, job, resolver, writer)
    assert await world.types(learner, "chunk.indexed", "material.parsed") == []

    writer.fail = False
    await structure_step(world.ctx, job, resolver, writer)
    assert len(await world.types(learner, "material.parsed")) == 1
    assert [s.key for s in writer.calls[0][1]] == [f"seminar:{material}:{n}" for n in range(3)]


async def test_a_material_without_problems_calls_no_writer(world: _World) -> None:
    learner, _, material = await world.upload()
    world.pdf_pages(PAGE_ONE)
    await parse_step(world.ctx, _job(learner, material))
    writer = _Writer()
    await structure_step(
        world.ctx, await _structure_job(world, learner, material), _Resolver(), writer
    )
    assert writer.calls == []


# --- the glue of the assembly layer ------------------------------------------------------------


async def test_the_handler_reconciles_the_skills_and_creates_the_seminar_tasks(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`quack.ops.material_parse`: the structure step calls `reconcile_domain` (origin
    `material`, topics before skills) and then `create_seminar_task` for every problem with
    the id of the node `reconcile_domain` gave to the skill; a task the function throws away
    (`None`) is not an error."""
    from quack.ops import material_parse as glue

    learner, domain, material = await _parsed(world)
    job = await _structure_job(world, learner, material)
    nodes: dict[tuple[str, str], UUID] = {}
    reconciled: list[tuple[str, list[str]]] = []

    async def fake_reconcile(
        ctx: Any, *, learner_id: UUID, domain_id: UUID, subjects: Sequence[Subject], origin: str
    ) -> ReconcileResult:
        assert (learner_id, domain_id) == (learner, domain)
        reconciled.append((origin, [s.name for s in subjects]))
        return ReconcileResult(
            [Resolution(s, "link", nodes.setdefault((s.kind, s.name), uuid4())) for s in subjects]
        )

    created: list[tuple[SeminarTask, UUID]] = []

    async def fake_create(
        db: Any,
        gateway: Any,
        settings: Any,
        task: SeminarTask,
        *,
        learner_id: UUID,
        compute: Any = None,
    ) -> UUID | None:
        created.append((task, learner_id))
        return None if task.statement.startswith("Integrate") else uuid4()

    monkeypatch.setattr(glue, "reconcile_domain", fake_reconcile)
    monkeypatch.setattr(glue, "create_seminar_task", fake_create)

    await glue.handle_material_parse(job, world.ctx)

    assert {origin for origin, _ in reconciled} == {"material"}
    names = [n for _, group in reconciled for n in group]
    assert names.index("Derivatives") < names.index("Chain rule")
    tasks = {t.statement: t for t, _ in created}
    assert len(created) == 3 and {who for _, who in created} == {learner}
    chain = tasks["Differentiate sin(x^2)."]
    assert chain.skill_id == nodes[("skill", "Chain rule")]
    assert (chain.key, chain.answer, chain.domain_id) == (
        f"seminar:{material}:0",
        "2x cos(x^2)",
        domain,
    )
    assert len(await world.types(learner, "material.parsed")) == 1


async def test_the_step_that_reconciles_nothing_creates_no_task(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the reconciliation skipped every skill, there is no skill to hang a task on."""
    from quack.ops import material_parse as glue

    learner, _, material = await _parsed(world)

    async def nothing(ctx: Any, **kw: Any) -> ReconcileResult:
        return ReconcileResult([])

    async def never(*a: Any, **kw: Any) -> UUID | None:
        raise AssertionError("no task without a skill")

    monkeypatch.setattr(glue, "reconcile_domain", nothing)
    monkeypatch.setattr(glue, "create_seminar_task", never)
    await glue.handle_material_parse(await _structure_job(world, learner, material), world.ctx)
    assert len(await world.types(learner, "material.parsed")) == 1
