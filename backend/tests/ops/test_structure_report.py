"""Unit tests for `quack.ops.structure_report` and `ops structure-report` CLI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from neo4j import AsyncDriver

from quack.cli import build_parser
from quack.config import ModelPrice, Settings
from quack.graph.ids import deterministic_id
from quack.ops.structure_report import (
    GraphSnapshot,
    RequiresEdgeRow,
    StructureEventRow,
    TopicGraphRow,
    TopicSkillRow,
    UsageSummaryRow,
    compute_structure_report,
    format_structure_report,
    read_graph_snapshot,
)


def _norm(name: str) -> str:
    return " ".join(name.casefold().split())


def test_structure_report_computation_and_formatting() -> None:
    learner_id = uuid4()
    domain_id = uuid4()
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)

    t1, t2, t3 = str(uuid4()), str(uuid4()), str(uuid4())
    s_core1, s_core2 = str(uuid4()), str(uuid4())
    s_unassigned = deterministic_id(learner_id, 1, f"skill:{_norm('Pivot tables')}")
    s_kept = deterministic_id(learner_id, 2, f"skill:{_norm('Chain rule')}")
    s_moved = deterministic_id(learner_id, 3, f"skill:{_norm('Integration by parts')}")
    s_merged_empty = deterministic_id(learner_id, 4, f"skill:{_norm('Excel solver')}")
    s_deleted_empty = deterministic_id(learner_id, 5, f"skill:{_norm('Temp slide skill')}")

    graph = GraphSnapshot(
        topics=(
            TopicGraphRow(
                topic_id=t1,
                name="Derivatives",
                skills=(
                    TopicSkillRow(skill_id=s_core1, core=True),
                    TopicSkillRow(skill_id=s_kept, core=False, status="provisional"),
                ),
            ),
            TopicGraphRow(
                topic_id=t2,
                name="Integrals",
                skills=(
                    TopicSkillRow(skill_id=s_core2, core=True),
                    TopicSkillRow(skill_id=s_moved, core=False),
                    TopicSkillRow(skill_id=str(uuid4()), core=False),
                ),
            ),
            # Empty topic with missing core skill (to verify both counters)
            TopicGraphRow(topic_id=t3, name="Empty", skills=()),
        ),
        unassigned_skill_ids=frozenset({s_unassigned}),
        requires_edges=(
            RequiresEdgeRow(origin="material", origins=("material", "model"), confidence=0.85),
            RequiresEdgeRow(origin="model", origins=("model",), confidence=0.5),
            RequiresEdgeRow(origin="learner", origins=(), confidence=1.0),
        ),
        hubs_by_kind={"request": 2, "domain_all": 1, "material": 0},
    )

    t_born_kept = now - timedelta(days=10)
    task_kept = str(uuid4())
    task_moved = str(uuid4())

    events = [
        # 1. unassigned birth from teacher
        StructureEventRow(
            seq=1,
            type="task.created",
            occurred_at=now - timedelta(days=5),
            idempotency_key="turn:1:task",
            payload={
                "task_id": str(uuid4()),
                "source": "teacher",
                "markup": {"tests": [{"new_skill": {"name": "Pivot tables"}, "level": 3}]},
            },
        ),
        # 2. kept birth from generated, accumulates 3 evidence items within 14d
        StructureEventRow(
            seq=2,
            type="task.created",
            occurred_at=t_born_kept,
            idempotency_key="gen:2",
            payload={
                "task_id": task_kept,
                "source": "generated",
                "markup": {"tests": [{"new_skill": {"name": "Chain rule"}, "level": 3}]},
            },
        ),
        # 3. moved birth from seminar, placed by structure writer
        StructureEventRow(
            seq=3,
            type="task.created",
            occurred_at=now - timedelta(days=8),
            idempotency_key="sem:3",
            payload={
                "task_id": task_moved,
                "source": "seminar",
                "markup": {"tests": [{"new_skill": {"name": "Integration by parts"}, "level": 3}]},
            },
        ),
        # 4. empty birth from teacher, merged into core by hygiene:empty_birth
        StructureEventRow(
            seq=4,
            type="task.created",
            occurred_at=now - timedelta(days=2),
            idempotency_key="turn:4:task",
            payload={
                "task_id": str(uuid4()),
                "source": "teacher",
                "markup": {"tests": [{"new_skill": {"name": "Excel solver"}, "level": 3}]},
            },
        ),
        # 5. empty birth from extractor, deleted by hygiene:empty_birth
        StructureEventRow(
            seq=5,
            type="observation.extracted",
            occurred_at=now - timedelta(days=1),
            idempotency_key="obs:5",
            payload={
                "elements": [
                    {
                        "kind": "exposure",
                        "new_skill": {"name": "Temp slide skill"},
                    }
                ]
            },
        ),
        # Structure writer events
        StructureEventRow(
            seq=6,
            type="skill.placed",
            occurred_at=now - timedelta(days=7),
            idempotency_key=f"hygiene:skill.placed:{s_moved}:{t2}",
            payload={"skill_id": s_moved, "topic_id": t2, "domain_id": str(domain_id)},
        ),
        StructureEventRow(
            seq=7,
            type="skill.merged",
            occurred_at=now - timedelta(days=1),
            idempotency_key=f"hygiene:empty_birth:skill.merged:{s_merged_empty}:{s_core1}",
            payload={"from_id": s_merged_empty, "into_id": s_core1},
        ),
        StructureEventRow(
            seq=8,
            type="skill.deleted",
            occurred_at=now - timedelta(hours=12),
            idempotency_key=f"hygiene:empty_birth:skill.deleted:{s_deleted_empty}",
            payload={"skill_id": s_deleted_empty, "reason": "empty_session_birth"},
        ),
        # 3 evidence items for s_kept within 14 days
        StructureEventRow(
            seq=9,
            type="answer.submitted",
            occurred_at=t_born_kept + timedelta(days=1),
            idempotency_key="ans:9",
            payload={"task_id": task_kept},
        ),
        StructureEventRow(
            seq=10,
            type="verdict.recorded",
            occurred_at=t_born_kept + timedelta(days=1),
            idempotency_key="verd:10",
            payload={"answer_seq": 9, "part": "main", "outcome": "correct"},
        ),
        StructureEventRow(
            seq=11,
            type="verdict.recorded",
            occurred_at=t_born_kept + timedelta(days=2),
            idempotency_key="verd:11",
            payload={"answer_seq": 9, "part": "main", "outcome": "correct"},
        ),
        StructureEventRow(
            seq=12,
            type="verdict.recorded",
            occurred_at=t_born_kept + timedelta(days=3),
            idempotency_key="verd:12",
            payload={"answer_seq": 9, "part": "main", "outcome": "incorrect"},
        ),
        # 1 evidence item on s_moved (not enough for 3)
        StructureEventRow(
            seq=13,
            type="answer.submitted",
            occurred_at=now - timedelta(days=6),
            idempotency_key="ans:13",
            payload={"task_id": task_moved},
        ),
        StructureEventRow(
            seq=14,
            type="verdict.recorded",
            occurred_at=now - timedelta(days=6),
            idempotency_key="verd:14",
            payload={"answer_seq": 13, "part": "main", "outcome": "correct"},
        ),
    ]

    settings = Settings(
        llm_prices={
            "muse-spark-1.3": ModelPrice(input=1.0, cached_input=0.5, output=4.0),
        }
    )
    usage = [
        UsageSummaryRow(
            role="builder",
            model="muse-spark-1.3",
            calls=2,
            input_tokens=1_000_000,
            cached_input_tokens=200_000,
            output_tokens=500_000,
            reasoning_tokens=100_000,
            recorded_cost_usd=0.0,
        ),
        UsageSummaryRow(
            role="teacher",
            model="unlisted-model",
            calls=1,
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=50,
            reasoning_tokens=0,
            recorded_cost_usd=0.12,
        ),
    ]

    report = compute_structure_report(
        learner_id=learner_id,
        domain_id=domain_id,
        graph=graph,
        events=events,
        usage_rows=usage,
        settings=settings,
        days=14,
        now=now,
    )

    assert report["topics"] == {"total": 3, "empty": 1, "missing_core": 1}
    assert report["skills_per_topic"] == {
        "min": 0,
        "median": 2.0,
        "avg": pytest.approx(5 / 3),
        "max": 3,
        "histogram": {"0": 1, "2": 1, "3": 1},
    }

    ns = report["new_skills"]
    assert ns["total"] == 5
    assert ns["empty_births_total"] == 2
    assert ns["by_source"]["teacher"]["total"] == 2
    assert ns["by_source"]["teacher"]["outcomes"]["unassigned"] == 1
    assert ns["by_source"]["teacher"]["outcomes"]["merged"] == 1
    assert ns["by_source"]["teacher"]["empty_births"] == 1
    assert ns["by_source"]["generated"]["outcomes"]["kept"] == 1
    assert ns["by_source"]["seminar"]["outcomes"]["moved"] == 1
    assert ns["by_source"]["extractor"]["outcomes"]["deleted"] == 1
    assert ns["by_source"]["extractor"]["empty_births"] == 1

    ev14 = ns["evidence_3_within_14d"]
    assert ev14["total_new_skills"] == 5
    assert ev14["with_at_least_3_evidence"] == 1
    assert ev14["share"] == pytest.approx(0.2)

    req = report["requires_edges"]
    assert req["total"] == 3
    assert req["by_origin"] == {"model": 2, "material": 1, "learner": 1}
    assert req["confidence_distribution"] == {"0.50": 1, "0.85": 1, "1.00": 1}

    assert report["hubs_by_kind"] == {"material": 0, "request": 2, "domain_all": 1}

    # Builder cost: (800k * 1.0 + 200k * 0.5 + 500k * 4.0) / 1M = 0.8 + 0.1 + 2.0 = 2.9 USD
    cost = report["llm_cost"]
    assert cost["by_role"]["builder"]["cost_usd"] == pytest.approx(2.9)
    assert cost["by_role"]["teacher"]["cost_usd"] == pytest.approx(0.12)
    assert cost["total_cost_usd"] == pytest.approx(3.02)

    text = format_structure_report(report)
    assert "Topics:" in text
    assert "REQUIRES edges (total=3):" in text
    assert "builder:" in text


def test_structure_report_new_skill_share_repeats_and_syllabus_hubs() -> None:
    learner_id, domain_id = uuid4(), uuid4()
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    syllabus_hub, other_hub = str(uuid4()), str(uuid4())

    def task(seq: int, tests: list[dict[str, object]]) -> StructureEventRow:
        return StructureEventRow(
            seq=seq,
            type="task.created",
            occurred_at=now - timedelta(days=1),
            idempotency_key=f"t:{seq}",
            payload={"task_id": str(uuid4()), "source": "generated", "markup": {"tests": tests}},
        )

    events = [
        task(1, [{"new_skill": {"name": "Chain rule", "reason": "no_fit"}, "level": 3}]),
        task(2, [{"new_skill": {"name": "chain  Rule"}, "level": 3}]),
        task(3, [{"skill_id": str(uuid4()), "level": 3}]),
        task(4, [{"skill_id": str(uuid4()), "level": 3}]),
        StructureEventRow(
            seq=5,
            type="hub.created",
            occurred_at=now,
            idempotency_key=f"hub.created:syllabus:{uuid4()}:{syllabus_hub}",
            payload={"hub_id": syllabus_hub},
        ),
    ]
    graph = GraphSnapshot(
        hubs_by_kind={"material": 2},
        material_hub_ids=frozenset({syllabus_hub, other_hub}),
    )
    report = compute_structure_report(
        learner_id=learner_id,
        domain_id=domain_id,
        graph=graph,
        events=events,
        usage_rows=[],
        settings=Settings(),
        now=now,
    )
    assert report["tasks_new_skill"] == {
        "tasks": 4,
        "with_new_skill": 2,
        "share": 0.5,
        "reasons": {"no_fit": 1, "none": 1},
    }
    assert report["new_skills"]["repeat_births"] == {
        "names": 1,
        "extra_births": 1,
        "top": {"chain rule": 2},
    }
    assert report["material_hubs"] == {"syllabus": 1, "other": 1}
    text = format_structure_report(report)
    assert "tasks with new_skill: 2/4" in text
    assert "material: syllabus=1 other=1" in text


def test_structure_report_cli_args() -> None:
    learner, domain = uuid4(), uuid4()
    args = build_parser().parse_args(
        [
            "ops",
            "structure-report",
            "--learner",
            str(learner),
            "--domain",
            str(domain),
            "--days",
            "30",
            "--json",
        ]
    )
    assert args.command == "ops"
    assert args.op == "structure-report"
    assert args.learner == learner
    assert args.domain == domain
    assert args.days == 30
    assert args.as_json is True


async def test_graph_snapshot_reads_material_hub_ids(neo4j: AsyncDriver) -> None:
    learner, domain = str(uuid4()), str(uuid4())
    material, request = str(uuid4()), str(uuid4())
    async with neo4j.session() as session:
        await session.run(
            "CREATE (d:Domain {id: $d, learnerId: $l}) "
            "CREATE (d)-[:HAS_HUB]->(:Hub {id: $m, learnerId: $l, kind: 'material', "
            "status: 'active'}) "
            "CREATE (d)-[:HAS_HUB]->(:Hub {id: $r, learnerId: $l, kind: 'request', "
            "status: 'active'})",
            d=domain,
            l=learner,
            m=material,
            r=request,
        )
    graph = await read_graph_snapshot(neo4j, learner_id=UUID(learner), domain_id=UUID(domain))
    assert graph.hubs_by_kind == {"material": 1, "request": 1}
    assert graph.material_hub_ids == frozenset({material})
