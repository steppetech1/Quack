"""Non-DB unit tests for `quack.ops.material_parse` glue."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from quack.agents import hygiene
from quack.config import Settings
from quack.jobs.runner import JobContext
from quack.jobs.types import ClaimedJob
from quack.materials.parse import (
    STEP_STRUCTURE,
    BuildsOnClaim,
    ResolvedTopic,
    SeminarSpec,
    TopicCandidateSpec,
)
from quack.ops import material_parse as glue


@dataclass(frozen=True)
class _FakeMaterialBuildsOn:
    concept: str
    quote: str


@dataclass(frozen=True)
class _FakeMaterialTopicCandidate:
    name: str
    description: str
    core_skill_name: str
    builds_on: tuple[_FakeMaterialBuildsOn, ...] = ()


@dataclass(frozen=True)
class _FakeResolvedStructureTopic:
    topic_id: UUID
    core_skill_id: UUID
    skill_ids: tuple[UUID, ...] = ()


def test_node_text_prefers_definition_and_falls_back_to_summary() -> None:
    skill = uuid4()
    assert (
        glue.node_text(
            "skill.created",
            {
                "skill_id": str(skill),
                "name": "Chain rule",
                "definition": "f(g(x))'",
                "summary": "old",
            },
        ).text
        == "Chain rule\nf(g(x))'"
    )
    assert (
        glue.node_text(
            "skill.created",
            {"skill_id": str(skill), "name": "Chain rule", "summary": "old"},
        ).text
        == "Chain rule\nold"
    )


async def test_resolve_structure_candidates_embeds_waits_and_adapts(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    learner, domain = uuid4(), uuid4()
    order: list[str] = []
    captured_candidates: list[Any] = []
    t1, s1, s2 = uuid4(), uuid4(), uuid4()

    async def fake_embed(ctx: JobContext, *, learner_id: UUID, domain_id: UUID) -> None:
        assert (learner_id, domain_id) == (learner, domain)
        order.append("embed")

    async def fake_wait(ctx: JobContext, *, learner_id: UUID) -> None:
        assert learner_id == learner
        order.append("wait")

    async def fake_resolve(
        ctx: JobContext,
        *,
        learner_id: UUID,
        domain_id: UUID,
        candidates: Sequence[Any],
        source_refs: Sequence[dict[str, Any]] = (),
    ) -> list[_FakeResolvedStructureTopic]:
        order.append("resolve")
        captured_candidates.extend(candidates)
        assert source_refs == [{"seq": 9}]
        return [_FakeResolvedStructureTopic(t1, s1, (s1, s2))]

    monkeypatch.setattr(glue, "embed_domain_nodes", fake_embed)
    monkeypatch.setattr(glue, "wait_projected", fake_wait)
    monkeypatch.setattr(hygiene, "MaterialBuildsOn", _FakeMaterialBuildsOn, raising=False)
    monkeypatch.setattr(
        hygiene, "MaterialTopicCandidate", _FakeMaterialTopicCandidate, raising=False
    )
    monkeypatch.setattr(hygiene, "resolve_material_structure", fake_resolve, raising=False)

    ctx = cast(JobContext, SimpleNamespace(settings=settings))
    out = await glue.resolve_structure_candidates(
        ctx,
        learner_id=learner,
        domain_id=domain,
        candidates=[
            TopicCandidateSpec(
                name="Derivatives",
                description="Rates of change.",
                core_skill_name="Differentiate functions",
                builds_on=(BuildsOnClaim("Limits", "as h -> 0"),),
            )
        ],
        source_refs=[{"seq": 9}],
    )

    assert order == ["embed", "wait", "resolve"]
    assert captured_candidates == [
        _FakeMaterialTopicCandidate(
            name="Derivatives",
            description="Rates of change.",
            core_skill_name="Differentiate functions",
            builds_on=(_FakeMaterialBuildsOn("Limits", "as h -> 0"),),
        )
    ]
    assert out == [ResolvedTopic(topic_id=t1, core_skill_id=s1, skill_ids=(s1, s2))]


async def test_create_seminar_tasks_passes_topic_id_and_skills(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    learner, domain, topic_id, core_id, other_id = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    made_key = "seminar:m:0"
    new_key = "seminar:m:1"
    from quack.practice.seminar_tasks import seminar_task_id

    class _Db:
        async def fetch_all(
            self, query: str, params: dict[str, Any] | None = None
        ) -> list[dict[str, Any]]:
            return [{"task_id": seminar_task_id(made_key)}]

    captured: list[dict[str, Any]] = []

    def fake_seminar_task(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(**kwargs)

    async def fake_create(
        db: Any, gateway: Any, s: Any, task: Any, *, learner_id: UUID, compute: Any = None
    ) -> UUID:
        captured.append({"task": task, "learner_id": learner_id})
        return uuid4()

    monkeypatch.setattr(glue, "SeminarTask", fake_seminar_task)
    monkeypatch.setattr(glue, "create_seminar_task", fake_create)

    ctx = cast(
        JobContext,
        SimpleNamespace(db=_Db(), llm=None, settings=settings, compute=None),
    )
    specs = [
        SeminarSpec(
            key=made_key,
            statement="Already made",
            answer=None,
            skill_id=core_id,
            skill_name="Core",
            topic_name="Topic",
            domain_id=domain,
            course_context="Course",
            topic_id=topic_id,
            topic_skill_ids=(core_id, other_id),
        ),
        SeminarSpec(
            key=new_key,
            statement="New problem",
            answer="42",
            skill_id=core_id,
            skill_name="Core",
            topic_name="Topic",
            domain_id=domain,
            course_context="Course",
            topic_id=topic_id,
            topic_skill_ids=(core_id, other_id),
        ),
    ]
    await glue.create_seminar_tasks(ctx, learner_id=learner, tasks=specs)

    assert len(captured) == 1
    task = captured[0]["task"]
    assert task.key == new_key
    assert task.topic_id == topic_id
    assert task.topic_skill_ids == (core_id, other_id)


async def test_handle_material_parse_dispatches_structure_step(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[Any] = []

    async def fake_structure(ctx: JobContext, job: ClaimedJob, resolver: Any) -> None:
        called.append(resolver)

    monkeypatch.setattr(glue, "structure_step", fake_structure)
    job = ClaimedJob(
        id=uuid4(),
        kind="material.parse",
        learner_id=uuid4(),
        lock_key="",
        coalesce_key=None,
        priority=0,
        payload={"material_id": str(uuid4()), "step": STEP_STRUCTURE},
        attempt=1,
        max_attempts=5,
        job_key="k",
    )
    await glue.handle_material_parse(job, cast(JobContext, SimpleNamespace(settings=settings)))
    assert called == [glue.resolve_structure_candidates]
