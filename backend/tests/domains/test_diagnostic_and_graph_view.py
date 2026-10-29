"""Red-phase TDD tests for Contour 3: Adaptive Diagnostics, Knowledge Map Views for UI,
and Background Graph Curation (`graph.curate`) (ТЗ §2.3–2.5, §3, §4, §5).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import presence_key
from quack.domains.curation import handle_graph_curate
from quack.domains.diagnostic import compute_inferred_priors
from quack.domains.schemas import (
    CuratorMerge,
    CuratorOutput,
    DiagnosticOutput,
    PrereqEdge,
    SkillDepEdge,
    SkillDraft,
    TopicDraft,
)
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.events.reader import read_learner_events
from quack.graph.ids import deterministic_id
from quack.graph.memory import prop_int
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.worker import ProjectorWorker
from quack.jobs import kinds
from quack.jobs.queue import claim_next_job, complete_job, enqueue_job
from quack.jobs.runner import JobContext
from tests.domains.conftest import FakeLLMProvider


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_linear_dag_domain(
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
    learner_id: UUID,
) -> tuple[UUID, tuple[UUID, UUID, UUID], tuple[UUID, UUID, UUID]]:
    """Seed an active domain with chain `T1 -> PREREQ_OF -> T2 -> PREREQ_OF -> T3` and
    `S2 -> DEPENDS_ON -> S1`, `S3 -> DEPENDS_ON -> S2` directly via `append_events` and
    project it into Neo4j via `ProjectorWorker`.
    """
    domain_id = uuid4()
    t1_id, t2_id, t3_id = uuid4(), uuid4(), uuid4()
    s1_id, s2_id, s3_id = uuid4(), uuid4(), uuid4()

    topics = [
        TopicDraft(topic_id=t1_id, name="T1 Basics", description="Foundational topic"),
        TopicDraft(topic_id=t2_id, name="T2 Core Hub", description="Middle hub topic"),
        TopicDraft(topic_id=t3_id, name="T3 Advanced", description="Advanced dependent topic"),
    ]
    skills = [
        SkillDraft(skill_id=s1_id, topic_id=t1_id, name="S1 Basic Skill"),
        SkillDraft(skill_id=s2_id, topic_id=t2_id, name="S2 Hub Skill"),
        SkillDraft(skill_id=s3_id, topic_id=t3_id, name="S3 Advanced Skill"),
    ]
    prereqs = [
        PrereqEdge(from_topic_id=t1_id, to_topic_id=t2_id),
        PrereqEdge(from_topic_id=t2_id, to_topic_id=t3_id),
    ]
    skill_deps = [
        SkillDepEdge(skill_id=s2_id, depends_on_skill_id=s1_id),
        SkillDepEdge(skill_id=s3_id, depends_on_skill_id=s2_id),
    ]

    async with db() as session, session.begin():
        await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="domain.created",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key=f"seed-domain:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "name": "Linear Algebra",
                        "slug": f"linalg-{domain_id.hex[:6]}",
                        "has_materials": False,
                    },
                ),
                NewEvent(
                    type="structure.proposed",
                    actor="structurer@1",
                    domain_id=domain_id,
                    idempotency_key=f"seed-propose:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "structure_version": 1,
                        "topics": [t.model_dump(mode="json") for t in topics],
                        "skills": [s.model_dump(mode="json") for s in skills],
                        "prereqs": [e.model_dump(mode="json") for e in prereqs],
                        "skill_deps": [d.model_dump(mode="json") for d in skill_deps],
                        "misconceptions": [],
                    },
                ),
                NewEvent(
                    type="structure.confirmed",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key=f"seed-confirm:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "structure_version": 1,
                    },
                ),
            ],
            publish_wakeup=False,
        )

    worker = ProjectorWorker(settings, pg_engine, db, neo4j, redis)
    await worker.run_once()
    return domain_id, (t1_id, t2_id, t3_id), (s1_id, s2_id, s3_id)


async def test_adaptive_diagnostic_cycle_and_projector_priors(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    """1. Full adaptive diagnostic cycle (`start` -> `answer` upward -> `answer` downward ->
    `finish`) and projection of `inferred_priors` onto `Skill` nodes in Neo4j (ТЗ §2.3, §5).
    """
    learner_id, token = seeded_learner
    headers = _auth_headers(token)
    domain_id, (t1_id, t2_id, t3_id), (s1_id, s2_id, s3_id) = await _seed_linear_dag_domain(
        db, settings, pg_engine, neo4j, redis, learner_id
    )

    # Queue diagnostic@1 outputs for question generation (and evaluation if invoked).
    for idx in range(6):
        fake_llm_provider.enqueue(
            DiagnosticOutput(
                prompt_text=f"Diagnostic question #{idx + 1}?",
                options=["A", "B", "C"],
                correct_answer="A",
                is_correct=True,
                score=1.0,
                tested_depth=0.5,
            )
        )

    # 1. Start diagnostic: should pick mid-DAG / hub topic T2 first.
    start_resp = await api_client.post(
        f"/v1/domains/{domain_id}/diagnostic/start",
        headers=headers,
        json={"idempotency_key": f"diag-start:{domain_id}"},
    )
    assert start_resp.status_code == 200
    start_data = start_resp.json()
    session_id = UUID(start_data["session_id"])
    assert str(t2_id) in [str(tid) for tid in start_data["candidate_topic_ids"]]
    first_q = start_data["next_question"]
    assert UUID(first_q["topic_id"]) == t2_id
    assert UUID(first_q["skill_id"]) == s2_id

    async with db() as session:
        events = await read_learner_events(session, learner_id)
    event_types = [e.type for e in events]
    assert "task.generated" in event_types
    assert "diagnostic.started" in event_types

    # 2. Answer T2 correctly -> adaptive transition moves UPWARD along DAG to dependent T3.
    ans1_resp = await api_client.post(
        f"/v1/domains/{domain_id}/diagnostic/answer",
        headers=headers,
        json={
            "session_id": str(session_id),
            "task_id": first_q["task_id"],
            "answer": "A",
            "confidence_self_report": 0.9,
            "response_time_sec": 4.2,
            "idempotency_key": f"diag-ans-1:{session_id}",
        },
    )
    assert ans1_resp.status_code == 200
    ans1_data = ans1_resp.json()
    assert ans1_data["is_correct"] is True
    assert ans1_data["score"] >= 0.7
    second_q = ans1_data["next_question"]
    assert second_q is not None
    assert UUID(second_q["topic_id"]) == t3_id
    assert UUID(second_q["skill_id"]) == s3_id

    # 3. Answer T3 incorrectly -> adaptive transition moves DOWNWARD along DAG to untested T1.
    ans2_resp = await api_client.post(
        f"/v1/domains/{domain_id}/diagnostic/answer",
        headers=headers,
        json={
            "session_id": str(session_id),
            "task_id": second_q["task_id"],
            "answer": "B",
            "confidence_self_report": 0.2,
            "response_time_sec": 6.0,
            "idempotency_key": f"diag-ans-2:{session_id}",
        },
    )
    assert ans2_resp.status_code == 200
    ans2_data = ans2_resp.json()
    assert ans2_data["is_correct"] is False
    assert ans2_data["score"] < 0.5
    third_q = ans2_data["next_question"]
    assert third_q is not None
    assert UUID(third_q["topic_id"]) == t1_id
    assert UUID(third_q["skill_id"]) == s1_id

    # 4. Finish diagnostic (leaving T1/S1 untested so S1 gets a downward propagated prior from S2).
    finish_resp = await api_client.post(
        f"/v1/domains/{domain_id}/diagnostic/finish",
        headers=headers,
        json={
            "session_id": str(session_id),
            "idempotency_key": f"diag-finish:{session_id}",
        },
    )
    assert finish_resp.status_code == 200
    finish_data = finish_resp.json()
    tested_topics = {UUID(tid) for tid in finish_data["tested_topic_ids"]}
    assert tested_topics == {t2_id, t3_id}

    priors_by_skill = {UUID(item["skill_id"]): item for item in finish_data["inferred_priors"]}
    assert priors_by_skill[s2_id]["source"] == "tested"
    assert priors_by_skill[s3_id]["source"] == "tested"
    assert priors_by_skill[s1_id]["source"] == "propagated"

    # Run ProjectorWorker and verify Skill nodes in Neo4j have prior properties updated.
    worker = ProjectorWorker(settings, pg_engine, db, neo4j, redis)
    await worker.run_once()

    graph = await Neo4jGraphStore(neo4j, str(learner_id)).dump()
    s2_props = graph.get("Skill", str(s2_id))
    s3_props = graph.get("Skill", str(s3_id))
    s1_props = graph.get("Skill", str(s1_id))
    assert s2_props is not None
    assert s3_props is not None
    assert s1_props is not None

    assert s2_props["priorSource"] == "tested"
    assert s2_props["priorMastery"] == pytest.approx(0.5)
    assert s2_props["priorConfidence"] == pytest.approx(0.5)

    assert s3_props["priorSource"] == "tested"
    assert s3_props["priorMastery"] == pytest.approx(0.0)
    assert s3_props["priorConfidence"] == pytest.approx(0.5)

    assert s1_props["priorSource"] == "propagated"
    assert s1_props["priorMastery"] == pytest.approx(0.4 * 0.85)
    assert s1_props["priorConfidence"] == pytest.approx(0.35 * 0.85)


async def test_inferred_priors_propagation_formulas_up_and_down_dag() -> None:
    """2. Exact formulas for `inferred_priors` (`compute_inferred_priors`):
    - tested skill: `source == "tested"`, `prior_mastery == min(0.5, score * 0.5)`,
      `confidence == 0.5`;
    - downward along `PREREQ_OF`/`DEPENDS_ON` from passed advanced skill (`score >= 0.7`):
      `source == "propagated"`, `prior_mastery == 0.4 * (0.85 ** distance)`,
      `confidence == 0.35 * (0.85 ** distance)`;
    - upward along `PREREQ_OF`/`DEPENDS_ON` from failed basic skill (`score < 0.5`):
      `source == "propagated"`, `prior_mastery == 0.05`,
      `confidence == 0.4 * (0.85 ** distance)` (ТЗ §2.3, §5).
    """
    t1_id, t2_id, t3_id = uuid4(), uuid4(), uuid4()
    s1_id, s2_id, s3_id = uuid4(), uuid4(), uuid4()

    skills = [(s1_id, t1_id), (s2_id, t2_id), (s3_id, t3_id)]
    topic_prereqs = [
        PrereqEdge(from_topic_id=t1_id, to_topic_id=t2_id),
        PrereqEdge(from_topic_id=t2_id, to_topic_id=t3_id),
    ]
    skill_deps = [
        SkillDepEdge(skill_id=s2_id, depends_on_skill_id=s1_id),
        SkillDepEdge(skill_id=s3_id, depends_on_skill_id=s2_id),
    ]

    # Case A: S3 passed (score = 0.8 >= 0.7) -> propagates downward to S2 (d=1) and S1 (d=2).
    down_items = compute_inferred_priors(
        skills=skills,
        skill_deps=skill_deps,
        topic_prereqs=topic_prereqs,
        tested_scores={s3_id: 0.8},
    )
    down_by_skill = {item.skill_id: item for item in down_items}

    assert down_by_skill[s3_id].source == "tested"
    assert down_by_skill[s3_id].prior_mastery == pytest.approx(min(0.5, 0.8 * 0.5))
    assert down_by_skill[s3_id].confidence == pytest.approx(0.5)

    assert down_by_skill[s2_id].source == "propagated"
    assert down_by_skill[s2_id].prior_mastery == pytest.approx(0.4 * (0.85**1))
    assert down_by_skill[s2_id].confidence == pytest.approx(0.35 * (0.85**1))

    assert down_by_skill[s1_id].source == "propagated"
    assert down_by_skill[s1_id].prior_mastery == pytest.approx(0.4 * (0.85**2))
    assert down_by_skill[s1_id].confidence == pytest.approx(0.35 * (0.85**2))

    # Case B: S1 failed (score = 0.2 < 0.5) -> propagates upward to S2 (d=1) and S3 (d=2).
    up_items = compute_inferred_priors(
        skills=skills,
        skill_deps=skill_deps,
        topic_prereqs=topic_prereqs,
        tested_scores={s1_id: 0.2},
    )
    up_by_skill = {item.skill_id: item for item in up_items}

    assert up_by_skill[s1_id].source == "tested"
    assert up_by_skill[s1_id].prior_mastery == pytest.approx(min(0.5, 0.2 * 0.5))
    assert up_by_skill[s1_id].confidence == pytest.approx(0.5)

    assert up_by_skill[s2_id].source == "propagated"
    assert up_by_skill[s2_id].prior_mastery == pytest.approx(0.05)
    assert up_by_skill[s2_id].confidence == pytest.approx(0.4 * (0.85**1))

    assert up_by_skill[s3_id].source == "propagated"
    assert up_by_skill[s3_id].prior_mastery == pytest.approx(0.05)
    assert up_by_skill[s3_id].confidence == pytest.approx(0.4 * (0.85**2))


async def test_knowledge_map_graph_and_topic_view_rusty_recomputation_and_filtering(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    """3. Read knowledge map (`GET /v1/domains/{id}/graph` and `GET /v1/domains/{id}/topics/{id}`):
    recomputes `display_state == "rusty"` and `is_rusty == True` (`effective_mastery < p_mastery`)
    at read time (`now_ms > nearestReviewAt`) when Neo4j holds `solid`, and filters `merged` and
    `archived` nodes (ТЗ §2.4, §5).
    """
    learner_id, token = seeded_learner
    headers = _auth_headers(token)

    domain_id = uuid4()
    t_solid_id = uuid4()
    t_merged_id = uuid4()
    t_archived_id = uuid4()
    s_solid_id = uuid4()
    s_merged_id = uuid4()
    task1_id = uuid4()
    task2_id = uuid4()
    session_id = uuid4()
    turn1_id = uuid4()
    turn2_id = uuid4()

    past_1 = datetime.now(UTC) - timedelta(days=10)
    past_2 = past_1 + timedelta(minutes=5)

    topics = [
        TopicDraft(topic_id=t_solid_id, name="Solid Topic Becoming Rusty"),
        TopicDraft(topic_id=t_merged_id, name="Duplicate Topic To Merge"),
        TopicDraft(topic_id=t_archived_id, name="Archived Topic"),
    ]
    skills = [
        SkillDraft(skill_id=s_solid_id, topic_id=t_solid_id, name="Mastered Skill"),
        SkillDraft(skill_id=s_merged_id, topic_id=t_solid_id, name="Merged Skill"),
    ]

    async with db() as session, session.begin():
        await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="domain.created",
                    actor="learner",
                    domain_id=domain_id,
                    occurred_at=past_1,
                    idempotency_key=f"created:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "name": "Calculus",
                        "slug": f"calc-{domain_id.hex[:6]}",
                        "has_materials": False,
                    },
                ),
                NewEvent(
                    type="structure.proposed",
                    actor="structurer@1",
                    domain_id=domain_id,
                    occurred_at=past_1,
                    idempotency_key=f"proposed:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "structure_version": 1,
                        "topics": [t.model_dump(mode="json") for t in topics],
                        "skills": [s.model_dump(mode="json") for s in skills],
                        "prereqs": [
                            PrereqEdge(
                                from_topic_id=t_solid_id, to_topic_id=t_archived_id
                            ).model_dump(mode="json")
                        ],
                        "skill_deps": [],
                        "misconceptions": [],
                    },
                ),
                NewEvent(
                    type="structure.confirmed",
                    actor="learner",
                    domain_id=domain_id,
                    occurred_at=past_1,
                    idempotency_key=f"confirmed:{domain_id}",
                    payload={"domain_id": str(domain_id), "structure_version": 1},
                ),
                NewEvent(
                    type="topic.merged",
                    actor="curator@1",
                    domain_id=domain_id,
                    occurred_at=past_1,
                    idempotency_key=f"tmerge:{t_merged_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "source_topic_id": str(t_merged_id),
                        "target_topic_id": str(t_solid_id),
                        "reason": "duplicate topic",
                    },
                ),
                NewEvent(
                    type="skill.merged",
                    actor="curator@1",
                    domain_id=domain_id,
                    occurred_at=past_1,
                    idempotency_key=f"smerge:{s_merged_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "source_skill_id": str(s_merged_id),
                        "target_skill_id": str(s_solid_id),
                        "reason": "duplicate skill",
                    },
                ),
                NewEvent(
                    type="topic.status_changed",
                    actor="learner",
                    domain_id=domain_id,
                    occurred_at=past_1,
                    idempotency_key=f"tarch:{t_archived_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "topic_id": str(t_archived_id),
                        "status": "archived",
                        "reason": "not needed",
                    },
                ),
                NewEvent(
                    type="task.generated",
                    actor="code",
                    domain_id=domain_id,
                    occurred_at=past_1,
                    idempotency_key=f"tgen:{task1_id}",
                    payload={
                        "task_id": str(task1_id),
                        "domain_id": str(domain_id),
                        "topic_id": str(t_solid_id),
                        "family_id": "fam_a",
                        "difficulty": 0.5,
                        "kind": "math",
                        "prompt_text": "Derivative 1",
                        "reference_solution": {"answer": "2x"},
                        "target_skills": [{"skill": {"skill_id": str(s_solid_id)}, "weight": 1.0}],
                        "target_misconceptions": [],
                    },
                ),
                NewEvent(
                    type="task.evaluated",
                    actor="code",
                    domain_id=domain_id,
                    session_id=session_id,
                    turn_id=turn1_id,
                    occurred_at=past_1,
                    idempotency_key=f"teval:{task1_id}",
                    payload={
                        "session_id": str(session_id),
                        "turn_id": str(turn1_id),
                        "task_id": str(task1_id),
                        "evaluator": "compute",
                        "is_correct": True,
                        "score": 1.0,
                        "independence": 1.0,
                        "feedback": "Correct",
                        "skill_scores": [
                            {"skill_id": str(s_solid_id), "score": 1.0, "tested_depth": 1.0}
                        ],
                        "triggered_misconceptions": [],
                    },
                ),
                NewEvent(
                    type="task.generated",
                    actor="code",
                    domain_id=domain_id,
                    occurred_at=past_2,
                    idempotency_key=f"tgen:{task2_id}",
                    payload={
                        "task_id": str(task2_id),
                        "domain_id": str(domain_id),
                        "topic_id": str(t_solid_id),
                        "family_id": "fam_b",
                        "difficulty": 0.6,
                        "kind": "math",
                        "prompt_text": "Derivative 2",
                        "reference_solution": {"answer": "3x^2"},
                        "target_skills": [{"skill": {"skill_id": str(s_solid_id)}, "weight": 1.0}],
                        "target_misconceptions": [],
                    },
                ),
                NewEvent(
                    type="task.evaluated",
                    actor="code",
                    domain_id=domain_id,
                    session_id=session_id,
                    turn_id=turn2_id,
                    occurred_at=past_2,
                    idempotency_key=f"teval:{task2_id}",
                    payload={
                        "session_id": str(session_id),
                        "turn_id": str(turn2_id),
                        "task_id": str(task2_id),
                        "evaluator": "compute",
                        "is_correct": True,
                        "score": 1.0,
                        "independence": 1.0,
                        "feedback": "Correct",
                        "skill_scores": [
                            {"skill_id": str(s_solid_id), "score": 1.0, "tested_depth": 1.0}
                        ],
                        "triggered_misconceptions": [],
                    },
                ),
            ],
            publish_wakeup=False,
        )

    worker = ProjectorWorker(settings, pg_engine, db, neo4j, redis)
    await worker.run_once()

    # Verify that in Neo4j at projection time (as_of = past_2) the topic is stored as "solid".
    raw_graph = await Neo4jGraphStore(neo4j, str(learner_id)).dump()
    raw_topic = raw_graph.get("Topic", str(t_solid_id))
    assert raw_topic is not None
    assert raw_topic["displayState"] == "solid"
    assert prop_int(raw_topic, "nearestReviewAt") > 0

    # 1. GET /v1/domains/{id}/graph (include_archived=false by default)
    graph_resp = await api_client.get(f"/v1/domains/{domain_id}/graph", headers=headers)
    assert graph_resp.status_code == 200
    graph_data = graph_resp.json()
    assert graph_data["lag_degraded"] is False
    topic_ids_default = {UUID(t["topic_id"]) for t in graph_data["topics"]}
    assert topic_ids_default == {t_solid_id}
    assert t_merged_id not in topic_ids_default
    assert t_archived_id not in topic_ids_default

    solid_topic_view = graph_data["topics"][0]
    assert solid_topic_view["display_state"] == "rusty"

    # 2. GET /v1/domains/{id}/graph?include_archived=true
    arch_resp = await api_client.get(
        f"/v1/domains/{domain_id}/graph",
        params={"include_archived": "true"},
        headers=headers,
    )
    assert arch_resp.status_code == 200
    arch_data = arch_resp.json()
    topic_ids_with_arch = {UUID(t["topic_id"]) for t in arch_data["topics"]}
    assert topic_ids_with_arch == {t_solid_id, t_archived_id}
    assert t_merged_id not in topic_ids_with_arch

    # 3. GET /v1/domains/{id}/topics/{topic_id}
    detail_resp = await api_client.get(
        f"/v1/domains/{domain_id}/topics/{t_solid_id}",
        headers=headers,
    )
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data["topic"]["display_state"] == "rusty"

    skill_views = detail_data["skills"]
    returned_skill_ids = {UUID(s["skill_id"]) for s in skill_views}
    assert returned_skill_ids == {s_solid_id}
    assert s_merged_id not in returned_skill_ids

    skill_view = skill_views[0]
    assert skill_view["is_rusty"] is True
    assert skill_view["effective_mastery"] < skill_view["p_mastery"]


async def test_graph_curate_defers_when_learner_presence_active(
    seeded_learner: tuple[UUID, str],
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    """4. `graph.curate` worker checks `presence:learner:{id}` in Redis: when present, does not
    merge or call LLM and defers the job in `ops.jobs` by ~10 minutes (ТЗ §2.5, §5).
    """
    learner_id, _ = seeded_learner
    domain_id, _, _ = await _seed_linear_dag_domain(
        db, settings, pg_engine, neo4j, redis, learner_id
    )

    await redis.set(presence_key(learner_id), "active-session", ex=300)

    async with db() as session, session.begin():
        events_before = await read_learner_events(session, learner_id)
        job_id = await enqueue_job(
            session,
            kinds.graph_curate(learner_id, domain_id, {"domain_id": str(domain_id)}),
        )

    before_run = datetime.now(UTC)
    async with db() as session, session.begin():
        claimed = await claim_next_job(session, "test-worker", ["graph.curate"])
    assert claimed is not None
    assert claimed.id == job_id

    await handle_graph_curate(claimed, JobContext(settings=settings, session_factory=db))
    async with db() as session, session.begin():
        await complete_job(session, claimed.id, "test-worker")

    # Verify LLM was not called and no curation events were emitted.
    assert fake_llm_provider.calls == []
    async with db() as session:
        events_after = await read_learner_events(session, learner_id)
        row = (
            await session.execute(
                text(
                    "SELECT status, run_after FROM ops.jobs "
                    "WHERE kind = 'graph.curate' AND learner_id = :l "
                    "ORDER BY updated_at DESC LIMIT 1"
                ),
                {"l": learner_id},
            )
        ).one()

    assert len(events_after) == len(events_before)
    assert row[0] == "queued"
    assert row[1] >= before_run + timedelta(minutes=9, seconds=30)


async def test_graph_curate_deterministic_promotion_dedup_and_curator_model_pass(
    seeded_learner: tuple[UUID, str],
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    """5. `graph.curate` without active presence:
    - promotes provisional skill with `evidenceCount >= 2` (`skill.promoted`),
    - deduplicates provisional skill matching `normName` of an `active` skill (`skill.merged`
      with `source_skill_id = provisional`, `target_skill_id = active`, creating `Alias`),
    - runs `curator@1` for non-trivial merges (`actor="curator@1"`) while filtering out topic
      merges that would create a cycle in `PREREQ_OF` (ТЗ §2.5, §4, §5).
    """
    learner_id, _ = seeded_learner
    domain_id, (t1_id, _t2_id, t3_id), (s1_id, _s2_id, _s3_id) = await _seed_linear_dag_domain(
        db, settings, pg_engine, neo4j, redis, learner_id
    )

    session_id = uuid4()
    turn_id = uuid4()
    s_active_dup_id = uuid4()

    # Seed provisional skills via turn.extracted and then add an active skill sharing normName.
    async with db() as session, session.begin():
        stored = await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="turn.extracted",
                    actor="extractor@1",
                    domain_id=domain_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    idempotency_key=f"extract:{turn_id}",
                    payload={
                        "session_id": str(session_id),
                        "turn_id": str(turn_id),
                        "observations": [
                            {
                                "skill": {
                                    "new_skill": {
                                        "name": "LU Decomposition",
                                        "topic_id": str(t1_id),
                                    }
                                },
                                "direction": "positive",
                                "strength": 0.9,
                                "independence": 1.0,
                                "tested_depth": 0.6,
                                "polarity_weight": 1.0,
                                "evidence_text": "Factored A = LU",
                            },
                            {
                                "skill": {
                                    "new_skill": {
                                        "name": "LU Decomposition",
                                        "topic_id": str(t1_id),
                                    }
                                },
                                "direction": "positive",
                                "strength": 0.85,
                                "independence": 1.0,
                                "tested_depth": 0.7,
                                "polarity_weight": 1.0,
                                "evidence_text": "Used LU for system solve",
                            },
                            {
                                "skill": {
                                    "new_skill": {
                                        "name": "Matrix Trace",
                                        "topic_id": str(t1_id),
                                    }
                                },
                                "direction": "positive",
                                "strength": 0.8,
                                "independence": 1.0,
                                "tested_depth": 0.5,
                                "polarity_weight": 1.0,
                                "evidence_text": "Summed diagonal entries",
                            },
                            {
                                "skill": {
                                    "new_skill": {
                                        "name": "Row Echelon Reduction",
                                        "topic_id": str(t1_id),
                                    }
                                },
                                "direction": "positive",
                                "strength": 0.75,
                                "independence": 1.0,
                                "tested_depth": 0.5,
                                "polarity_weight": 1.0,
                                "evidence_text": "Reduced matrix to echelon form",
                            },
                        ],
                        "misconceptions": [],
                    },
                ),
                NewEvent(
                    type="structure.edited",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key=f"edit-dup:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "structure_version": 2,
                        "added_skills": [
                            SkillDraft(
                                skill_id=s_active_dup_id,
                                topic_id=t1_id,
                                name="Matrix Trace",
                                description="Canonical active trace skill",
                            ).model_dump(mode="json")
                        ],
                    },
                ),
            ],
            publish_wakeup=False,
        )

    extract_seq = stored[0].seq
    s_prov_promote_id = UUID(deterministic_id(learner_id, extract_seq, "skill:0"))
    s_prov_dup_id = UUID(deterministic_id(learner_id, extract_seq, "skill:2"))
    s_prov_llm_id = UUID(deterministic_id(learner_id, extract_seq, "skill:3"))

    worker = ProjectorWorker(settings, pg_engine, db, neo4j, redis)
    await worker.run_once()

    # Queue curator@1 output: valid merge s_prov_llm -> s1_id, plus invalid cycle topic merge.
    fake_llm_provider.enqueue(
        CuratorOutput(
            promotions=[],
            skill_merges=[
                CuratorMerge(
                    source_id=s_prov_llm_id,
                    target_id=s1_id,
                    reason="Equivalent row reduction skill",
                )
            ],
            topic_merges=[
                CuratorMerge(
                    source_id=t3_id,
                    target_id=t1_id,
                    reason="Invalid merge creating cycle T1 -> T2 -> T1",
                )
            ],
            misconception_merges=[],
        )
    )

    async with db() as session, session.begin():
        await enqueue_job(
            session,
            kinds.graph_curate(learner_id, domain_id, {"domain_id": str(domain_id)}),
        )
    async with db() as session, session.begin():
        claimed = await claim_next_job(session, "test-worker", ["graph.curate"])
    assert claimed is not None

    await handle_graph_curate(claimed, JobContext(settings=settings, session_factory=db))
    async with db() as session, session.begin():
        await complete_job(session, claimed.id, "test-worker")

    await worker.run_once()

    async with db() as session:
        all_events = await read_learner_events(session, learner_id)

    promoted_events = [e for e in all_events if e.type == "skill.promoted"]
    merged_skill_events = [e for e in all_events if e.type == "skill.merged"]
    merged_topic_events = [e for e in all_events if e.type == "topic.merged"]

    assert any(UUID(e.payload["skill_id"]) == s_prov_promote_id for e in promoted_events)
    assert any(
        UUID(e.payload["source_skill_id"]) == s_prov_dup_id
        and UUID(e.payload["target_skill_id"]) == s_active_dup_id
        for e in merged_skill_events
    )
    assert any(
        UUID(e.payload["source_skill_id"]) == s_prov_llm_id
        and UUID(e.payload["target_skill_id"]) == s1_id
        and e.actor == "curator@1"
        for e in merged_skill_events
    )
    assert merged_topic_events == []

    # Verify Neo4j state after projection: promoted skill is active, merged skill has Alias.
    graph = await Neo4jGraphStore(neo4j, str(learner_id)).dump()
    promoted_props = graph.get("Skill", str(s_prov_promote_id))
    assert promoted_props is not None
    assert promoted_props["status"] == "active"

    dup_props = graph.get("Skill", str(s_prov_dup_id))
    assert dup_props is not None
    assert dup_props["status"] == "merged"

    alias_props = graph.get("Alias", str(s_prov_dup_id))
    assert alias_props is not None
    assert alias_props["targetId"] == str(s_active_dup_id)
