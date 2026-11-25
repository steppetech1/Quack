"""WP20.2: the problems of a material become seminar tasks (ТЗ Ф4 WP20.2; Т-§5.6; #271;
D-C-217). `material_parser@1` returns `seminar_tasks` in its layer 2; the structure step of
`material.parse` links them to skills and queues the job `material.seminar_tasks` with the ids
of the skills (decision of 2026-11-25: the tasks do not hold the lock of the domain and cannot
fail the structure); that job hands them to `quack.practice.seminar_tasks.create_seminar_task`
(stream B, D-B-218) through a seam. `reconcile_domain` stays the seam of the skills. The LLM,
compute, storage, the graph reconciliation and the task writer are test doubles."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from quack.agents.reconcile import ReconcileResult, Resolution, Subject
from quack.config import Settings
from quack.db import Db
from quack.jobs.runner import JobContext
from quack.jobs.types import ClaimedJob
from quack.llm.errors import LLMUnavailableError
from quack.materials.parse import (
    SEMINAR_TASKS_KIND,
    MaterialParserOutput,
    SeminarSpec,
    parse_step,
    seminar_tasks_step,
    structure_step,
)
from quack.practice.seminar_tasks import SeminarTask, seminar_task_id
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
    cast(Any, world.ctx).settings = world.settings  # the context of the tests is a namespace
    learner, _, material = await _parsed(world)
    job = await _structure_job(world, learner, material)
    assert [t["statement"] for t in job.payload["seminar_tasks"]] == ["Differentiate sin(x^2)."]


async def _seminar_jobs(world: _World) -> list[dict[str, Any]]:
    return await world.db.fetch_all(
        "SELECT job_key, lock_key, priority_class, visible, payload FROM ops.jobs "
        "WHERE kind = %(kind)s ORDER BY job_key",
        {"kind": SEMINAR_TASKS_KIND},
    )


async def _seminar_job(world: _World, learner: UUID) -> ClaimedJob:
    (queued,) = await _seminar_jobs(world)
    return ClaimedJob(
        id=uuid4(),
        kind=SEMINAR_TASKS_KIND,
        learner_id=learner,
        lock_key=queued["lock_key"],
        coalesce_key=None,
        priority=0,
        payload=queued["payload"],
        attempt=1,
        max_attempts=5,
        job_key=queued["job_key"],
    )


async def _status(world: _World, material: UUID) -> str | None:
    row = await world.db.fetch_one(
        "SELECT status FROM content.materials WHERE material_id = %(m)s", {"m": material}
    )
    return None if row is None else str(row["status"])


async def test_structure_step_queues_the_job_of_the_seminar_tasks(world: _World) -> None:
    """After the skills are linked the step queues `material.seminar_tasks` in the transaction
    that makes the material `parsed`: a spec per problem with the id of the skill the chunk
    teaches (the first one the reconciliation linked), the key `seminar:{material}:{n}`, the topic
    of the chunk; the answer of the material rides along. The job has its own lock (not the
    lock of the domain). A repeat of the step, the material being parsed, queues nothing."""
    learner, domain, material = await _parsed(world)
    job = await _structure_job(world, learner, material)
    resolver = _Resolver()

    await structure_step(world.ctx, job, resolver)

    assert (await world.types(learner, "material.parsed"))[0]["payload"]["material_id"] == str(
        material
    )
    [queued] = await _seminar_jobs(world)
    assert queued["job_key"] == f"{SEMINAR_TASKS_KIND}:{learner}:{material}"
    assert queued["lock_key"] == f"material:{material}:seminar_tasks"
    assert (queued["priority_class"], queued["visible"]) == ("P2", False)
    assert queued["payload"]["material_id"] == str(material)
    writer = _Writer()
    await seminar_tasks_step(world.ctx, await _seminar_job(world, learner), writer)

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

    await structure_step(world.ctx, job, resolver)
    assert len(await _seminar_jobs(world)) == 1


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

    await structure_step(world.ctx, await _structure_job(world, learner, material), resolver)
    await seminar_tasks_step(world.ctx, await _seminar_job(world, learner), writer)

    [(_, [only])] = writer.calls
    assert only.statement == "Differentiate sin(x^2)."
    assert only.skill_id == resolver.ids[("skill", "Power rule")]
    assert len(await world.types(learner, "material.parsed")) == 1


async def test_the_material_is_parsed_whatever_happens_to_the_tasks(world: _World) -> None:
    """The seminar tasks are not a part of the structure step: it commits the chunks, the hub,
    `material.parsed` and the status together with the job of the tasks, and no model of the
    tasks is asked in it. A writer that fails when the job runs does not touch what the step
    wrote; the retry of the job, the writer being well again, makes the same tasks (same keys)."""
    learner, _, material = await _parsed(world)
    await structure_step(world.ctx, await _structure_job(world, learner, material), _Resolver())
    assert await _status(world, material) == "parsed"
    assert len(await world.types(learner, "chunk.indexed")) == 2
    job = await _seminar_job(world, learner)
    writer = _Writer()
    writer.fail = True

    with pytest.raises(RuntimeError):
        await seminar_tasks_step(world.ctx, job, writer)
    assert await _status(world, material) == "parsed"
    assert len(await world.types(learner, "material.parsed")) == 1

    writer.fail = False
    await seminar_tasks_step(world.ctx, job, writer)
    assert [s.key for s in writer.calls[0][1]] == [f"seminar:{material}:{n}" for n in range(3)]


async def test_a_material_without_problems_queues_no_job(world: _World) -> None:
    learner, _, material = await world.upload()
    world.pdf_pages(PAGE_ONE)
    await parse_step(world.ctx, _job(learner, material))
    await structure_step(world.ctx, await _structure_job(world, learner, material), _Resolver())
    assert await _seminar_jobs(world) == []
    assert len(await world.types(learner, "material.parsed")) == 1


# --- the glue of the assembly layer ------------------------------------------------------------


class _FakeCreate:
    """`create_seminar_task` of stream B: remembers every call, makes a task document the way the
    real one does (the glue looks for it by id), fails the keys it is told to."""

    def __init__(self, db: Db) -> None:
        self.db = db
        self.calls: list[tuple[SeminarTask, UUID]] = []
        self.failing: set[str] = set()

    async def __call__(
        self,
        db: Any,
        gateway: Any,
        settings: Any,
        task: SeminarTask,
        *,
        learner_id: UUID,
        compute: Any = None,
    ) -> UUID | None:
        self.calls.append((task, learner_id))
        if task.key in self.failing:
            raise LLMUnavailableError("task_generator@1: provider timeout", reason="timeout")
        if task.statement.startswith("Integrate"):
            return None  # thrown away: not an error
        task_id = seminar_task_id(task.key)
        await self.db.execute(
            "INSERT INTO content.tasks (task_id, learner_id, domain_id, source, version, "
            "condition, answer_spec, created_seq) VALUES (%(t)s, %(l)s, %(d)s, 'seminar', 1, "
            "'{}'::jsonb, '{}'::jsonb, 1)",
            {"t": task_id, "l": learner_id, "d": task.domain_id},
        )
        return task_id


async def _structured(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> tuple[UUID, UUID, UUID, dict[tuple[str, str], UUID]]:
    """The structure step through the handler of the glue, `reconcile_domain` being a double."""
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

    async def never(*a: Any, **kw: Any) -> UUID | None:
        raise AssertionError("the structure step asks no model for a task")

    monkeypatch.setattr(glue, "reconcile_domain", fake_reconcile)
    monkeypatch.setattr(glue, "create_seminar_task", never)

    await glue.handle_material_parse(job, world.ctx)

    assert {origin for origin, _ in reconciled} == {"material"}
    names = [n for _, group in reconciled for n in group]
    assert names.index("Derivatives") < names.index("Chain rule")
    assert len(await world.types(learner, "material.parsed")) == 1
    return learner, domain, material, nodes


async def test_the_handler_reconciles_the_skills_and_the_other_job_makes_the_tasks(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`quack.ops.material_parse`: the structure step calls `reconcile_domain` (origin
    `material`, topics before skills) and asks no model for a task; the job it queued calls
    `create_seminar_task` for every problem with the id of the node `reconcile_domain` gave to
    the skill; a task the function throws away (`None`) is not an error."""
    from quack.ops import material_parse as glue

    learner, domain, material, nodes = await _structured(world, monkeypatch)
    create = _FakeCreate(world.db)
    monkeypatch.setattr(glue, "create_seminar_task", create)

    await glue.handle_material_seminar_tasks(await _seminar_job(world, learner), world.ctx)

    tasks = {t.statement: t for t, _ in create.calls}
    assert len(create.calls) == 3 and {who for _, who in create.calls} == {learner}
    chain = tasks["Differentiate sin(x^2)."]
    assert chain.skill_id == nodes[("skill", "Chain rule")]
    assert (chain.key, chain.answer, chain.domain_id) == (
        f"seminar:{material}:0",
        "2x cos(x^2)",
        domain,
    )


async def test_a_failing_task_does_not_throw_away_the_others_and_the_retry_goes_on(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One `LLMUnavailableError` (a provider timeout) on one task: every other task is still
    tried and kept, the job fails with that error (so it is retried), the material stays
    `parsed`. The retry asks the model only for what is left: a task already made is skipped
    before any model call."""
    from quack.ops import material_parse as glue

    learner, _, material, _ = await _structured(world, monkeypatch)
    create = _FakeCreate(world.db)
    create.failing = {f"seminar:{material}:2"}
    monkeypatch.setattr(glue, "create_seminar_task", create)
    job = await _seminar_job(world, learner)

    with pytest.raises(LLMUnavailableError):
        await glue.handle_material_seminar_tasks(job, world.ctx)
    assert sorted(t.key for t, _ in create.calls) == [f"seminar:{material}:{n}" for n in range(3)]
    made = await world.db.fetch_all(
        "SELECT task_id FROM content.tasks WHERE learner_id = %(l)s", {"l": learner}
    )
    assert {r["task_id"] for r in made} == {seminar_task_id(f"seminar:{material}:0")}
    assert await _status(world, material) == "parsed"

    create.calls.clear()
    create.failing = set()
    await glue.handle_material_seminar_tasks(job, world.ctx)
    # task 0 is made: skipped, no model. Task 1 was thrown away (`None`, nothing stored): asked
    # again. Task 2 is new.
    assert sorted(t.key for t, _ in create.calls) == [
        f"seminar:{material}:1",
        f"seminar:{material}:2",
    ]


async def test_the_step_that_reconciles_nothing_queues_no_task_job(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the reconciliation skipped every skill, there is no skill to hang a task on."""
    from quack.ops import material_parse as glue

    learner, _, material = await _parsed(world)

    async def nothing(ctx: Any, **kw: Any) -> ReconcileResult:
        return ReconcileResult([])

    monkeypatch.setattr(glue, "reconcile_domain", nothing)
    await glue.handle_material_parse(await _structure_job(world, learner, material), world.ctx)
    assert len(await world.types(learner, "material.parsed")) == 1
    assert await _seminar_jobs(world) == []
