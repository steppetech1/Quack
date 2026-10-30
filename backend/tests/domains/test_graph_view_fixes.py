"""Tests for T-022 (D-6 + D-7.2 in `quack/domains/graph_view.py`, ТЗ-П1 §2.4):
- `SkillSnapshot.p_mastery` uses `effective_mastery(p_m, stab, l_rev, now_ms)` in both
  `get_domain_graph_view` and `get_topic_detail_view` (forgetting lowers topic `mastery`);
- `prereq_snaps` in `get_topic_detail_view` are computed from prerequisite topics' active
  skills through `recompute_topic_display` rather than the `Topic.mastery` property;
- `_load_topic_detail_from_neo4j` skips archived skills and returns only intra-topic
  non-archived `DEPENDS_ON` edges;
- every Neo4j session in `quack/domains/graph_view.py` opens with
  `default_access_mode=neo4j.READ_ACCESS`.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from neo4j import READ_ACCESS, AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.domains.schemas import PrereqEdge, SkillDepEdge, SkillDraft, TopicDraft
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.graph.memory import prop_float
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.worker import ProjectorWorker


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _eval_events(
    *,
    domain_id: UUID,
    topic_id: UUID,
    skill_id: UUID,
    occurred_at: datetime,
    tag: str,
) -> list[NewEvent]:
    """Build two T1 (`evaluator="compute"`) task generation + evaluation event pairs across two
    distinct families (`fam_a`, `fam_b`) 5 minutes apart so the skill reaches `mastered` with
    `pMastery == 0.8` and `stability == 1.0`.
    """
    session_id = uuid4()
    events: list[NewEvent] = []
    for idx, fam in enumerate(("fam_a", "fam_b")):
        task_id = uuid4()
        turn_id = uuid4()
        at = occurred_at + timedelta(minutes=5 * idx)
        events.append(
            NewEvent(
                type="task.generated",
                actor="code",
                domain_id=domain_id,
                occurred_at=at,
                idempotency_key=f"tgen:{tag}:{task_id}",
                payload={
                    "task_id": str(task_id),
                    "domain_id": str(domain_id),
                    "topic_id": str(topic_id),
                    "family_id": fam,
                    "difficulty": 0.5,
                    "kind": "math",
                    "prompt_text": f"Task {tag} {idx}",
                    "reference_solution": {"answer": "42"},
                    "target_skills": [{"skill": {"skill_id": str(skill_id)}, "weight": 1.0}],
                    "target_misconceptions": [],
                },
            )
        )
        events.append(
            NewEvent(
                type="task.evaluated",
                actor="code",
                domain_id=domain_id,
                session_id=session_id,
                turn_id=turn_id,
                occurred_at=at,
                idempotency_key=f"teval:{tag}:{task_id}",
                payload={
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "task_id": str(task_id),
                    "evaluator": "compute",
                    "is_correct": True,
                    "score": 1.0,
                    "independence": 1.0,
                    "feedback": "Correct",
                    "skill_scores": [
                        {"skill_id": str(skill_id), "score": 1.0, "tested_depth": 1.0}
                    ],
                    "triggered_misconceptions": [],
                },
            )
        )
    return events


async def test_forgetting_lowers_topic_mastery_below_raw_p_mastery(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    """A skill with old `lastReviewedAt` and low `stability` yields a topic `mastery` in both
    `GET /v1/domains/{id}/graph` and `GET /v1/domains/{id}/topics/{topic_id}` that is lower than
    its raw `pMastery` and equal to its decayed `effective_mastery` (D-6.1).
    """
    learner_id, token = seeded_learner
    headers = _auth_headers(token)

    domain_id = uuid4()
    topic_id = uuid4()
    skill_id = uuid4()
    past = datetime.now(UTC) - timedelta(days=14)

    async with db() as session, session.begin():
        await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="domain.created",
                    actor="learner",
                    domain_id=domain_id,
                    occurred_at=past,
                    idempotency_key=f"dom:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "name": "Analysis",
                        "slug": f"analysis-{domain_id.hex[:6]}",
                        "has_materials": False,
                    },
                ),
                NewEvent(
                    type="structure.proposed",
                    actor="structurer@1",
                    domain_id=domain_id,
                    occurred_at=past,
                    idempotency_key=f"prop:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "structure_version": 1,
                        "topics": [
                            TopicDraft(topic_id=topic_id, name="Series").model_dump(mode="json")
                        ],
                        "skills": [
                            SkillDraft(
                                skill_id=skill_id, topic_id=topic_id, name="Ratio Test"
                            ).model_dump(mode="json")
                        ],
                        "prereqs": [],
                        "skill_deps": [],
                        "misconceptions": [],
                    },
                ),
                NewEvent(
                    type="structure.confirmed",
                    actor="learner",
                    domain_id=domain_id,
                    occurred_at=past,
                    idempotency_key=f"conf:{domain_id}",
                    payload={"domain_id": str(domain_id), "structure_version": 1},
                ),
                *_eval_events(
                    domain_id=domain_id,
                    topic_id=topic_id,
                    skill_id=skill_id,
                    occurred_at=past,
                    tag="decay",
                ),
            ],
            publish_wakeup=False,
        )

    worker = ProjectorWorker(settings, pg_engine, db, neo4j, redis)
    await worker.run_once()

    raw_graph = await Neo4jGraphStore(neo4j, str(learner_id)).dump()
    raw_skill = raw_graph.get("Skill", str(skill_id))
    assert raw_skill is not None
    raw_p_mastery = prop_float(raw_skill, "pMastery")
    assert raw_p_mastery == pytest.approx(0.8)

    detail_resp = await api_client.get(
        f"/v1/domains/{domain_id}/topics/{topic_id}", headers=headers
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    skill_view = detail["skills"][0]
    assert skill_view["p_mastery"] == pytest.approx(raw_p_mastery)
    assert skill_view["effective_mastery"] < raw_p_mastery * 0.75
    assert detail["topic"]["mastery"] < raw_p_mastery
    assert detail["topic"]["mastery"] == pytest.approx(skill_view["effective_mastery"], abs=1e-3)

    graph_resp = await api_client.get(f"/v1/domains/{domain_id}/graph", headers=headers)
    assert graph_resp.status_code == 200
    graph_topic = next(t for t in graph_resp.json()["topics"] if UUID(t["topic_id"]) == topic_id)
    assert graph_topic["mastery"] < raw_p_mastery
    assert graph_topic["mastery"] == pytest.approx(skill_view["effective_mastery"], abs=1e-2)


async def test_prereq_mastery_computed_from_active_skills_not_topic_node_property(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    """A topic whose prerequisite topics' active skills are mastered is `available` (not `locked`)
    even when the prerequisite `Topic` nodes in Neo4j have `mastery = 0.0` or no `mastery`
    property (and an archived skill with 0 mastery). Conversely, a topic whose prerequisite
    topic's skill has decayed below `prereq_unlock_mastery` is `locked` even if the prerequisite
    `Topic` node in Neo4j has a high `mastery` property (D-6.1, D-6.2).
    """
    learner_id, token = seeded_learner
    headers = _auth_headers(token)

    domain_id = uuid4()
    t_prereq_zero = uuid4()
    t_prereq_missing = uuid4()
    t_unlocked = uuid4()
    t_prereq_decayed = uuid4()
    t_locked = uuid4()

    s_prereq_zero_active = uuid4()
    s_prereq_zero_archived = uuid4()
    s_prereq_missing_active = uuid4()
    s_unlocked = uuid4()
    s_prereq_decayed = uuid4()
    s_locked = uuid4()

    now = datetime.now(UTC)
    recent = now - timedelta(minutes=15)
    old = now - timedelta(days=30)

    topics = [
        TopicDraft(topic_id=t_prereq_zero, name="Prereq Zero Mastery Prop"),
        TopicDraft(topic_id=t_prereq_missing, name="Prereq Missing Mastery Prop"),
        TopicDraft(topic_id=t_unlocked, name="Unlocked Dependent Topic"),
        TopicDraft(topic_id=t_prereq_decayed, name="Prereq Decayed Skill"),
        TopicDraft(topic_id=t_locked, name="Locked Dependent Topic"),
    ]
    skills = [
        SkillDraft(
            skill_id=s_prereq_zero_active,
            topic_id=t_prereq_zero,
            name="Mastered Active Skill 1",
        ),
        SkillDraft(
            skill_id=s_prereq_zero_archived,
            topic_id=t_prereq_zero,
            name="Archived Unpracticed Skill",
        ),
        SkillDraft(
            skill_id=s_prereq_missing_active,
            topic_id=t_prereq_missing,
            name="Mastered Active Skill 2",
        ),
        SkillDraft(skill_id=s_unlocked, topic_id=t_unlocked, name="Unlocked Target Skill"),
        SkillDraft(
            skill_id=s_prereq_decayed,
            topic_id=t_prereq_decayed,
            name="Long Forgotten Prerequisite Skill",
        ),
        SkillDraft(skill_id=s_locked, topic_id=t_locked, name="Locked Target Skill"),
    ]
    prereqs = [
        PrereqEdge(from_topic_id=t_prereq_zero, to_topic_id=t_unlocked),
        PrereqEdge(from_topic_id=t_prereq_missing, to_topic_id=t_unlocked),
        PrereqEdge(from_topic_id=t_prereq_decayed, to_topic_id=t_locked),
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
                    occurred_at=old,
                    idempotency_key=f"dom:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "name": "Topology",
                        "slug": f"topo-{domain_id.hex[:6]}",
                        "has_materials": False,
                    },
                ),
                NewEvent(
                    type="structure.proposed",
                    actor="structurer@1",
                    domain_id=domain_id,
                    occurred_at=old,
                    idempotency_key=f"prop:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "structure_version": 1,
                        "topics": [t.model_dump(mode="json") for t in topics],
                        "skills": [s.model_dump(mode="json") for s in skills],
                        "prereqs": [p.model_dump(mode="json") for p in prereqs],
                        "skill_deps": [],
                        "misconceptions": [],
                    },
                ),
                NewEvent(
                    type="structure.confirmed",
                    actor="learner",
                    domain_id=domain_id,
                    occurred_at=old,
                    idempotency_key=f"conf:{domain_id}",
                    payload={"domain_id": str(domain_id), "structure_version": 1},
                ),
                NewEvent(
                    type="structure.edited",
                    actor="learner",
                    domain_id=domain_id,
                    occurred_at=old,
                    idempotency_key=f"edit-arch:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "structure_version": 2,
                        "archived_skill_ids": [str(s_prereq_zero_archived)],
                    },
                ),
                *_eval_events(
                    domain_id=domain_id,
                    topic_id=t_prereq_decayed,
                    skill_id=s_prereq_decayed,
                    occurred_at=old,
                    tag="decayed-prereq",
                ),
                *_eval_events(
                    domain_id=domain_id,
                    topic_id=t_prereq_zero,
                    skill_id=s_prereq_zero_active,
                    occurred_at=recent,
                    tag="prereq-zero",
                ),
                *_eval_events(
                    domain_id=domain_id,
                    topic_id=t_prereq_missing,
                    skill_id=s_prereq_missing_active,
                    occurred_at=recent,
                    tag="prereq-missing",
                ),
            ],
            publish_wakeup=False,
        )

    worker = ProjectorWorker(settings, pg_engine, db, neo4j, redis)
    await worker.run_once()

    # Overwrite Topic.mastery in Neo4j: zero on t_prereq_zero, removed on t_prereq_missing,
    # and high (0.95) on t_prereq_decayed so any code reading Topic.mastery gets the wrong state.
    async with neo4j.session() as s:
        await s.run(
            "MATCH (t:Topic {id: $tid, learnerId: $lid}) SET t.mastery = 0.0",
            {"tid": str(t_prereq_zero), "lid": str(learner_id)},
        )
        await s.run(
            "MATCH (t:Topic {id: $tid, learnerId: $lid}) REMOVE t.mastery",
            {"tid": str(t_prereq_missing), "lid": str(learner_id)},
        )
        await s.run(
            "MATCH (t:Topic {id: $tid, learnerId: $lid}) SET t.mastery = 0.95",
            {"tid": str(t_prereq_decayed), "lid": str(learner_id)},
        )

    unlocked_resp = await api_client.get(
        f"/v1/domains/{domain_id}/topics/{t_unlocked}", headers=headers
    )
    assert unlocked_resp.status_code == 200
    assert unlocked_resp.json()["topic"]["display_state"] == "available"

    locked_resp = await api_client.get(
        f"/v1/domains/{domain_id}/topics/{t_locked}", headers=headers
    )
    assert locked_resp.status_code == 200
    assert locked_resp.json()["topic"]["display_state"] == "locked"

    graph_resp = await api_client.get(f"/v1/domains/{domain_id}/graph", headers=headers)
    assert graph_resp.status_code == 200
    by_id = {UUID(t["topic_id"]): t for t in graph_resp.json()["topics"]}
    assert by_id[t_unlocked]["display_state"] == "available"
    assert by_id[t_locked]["display_state"] == "locked"


async def test_topic_detail_excludes_archived_skills_and_non_local_or_archived_edges(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    """`GET /v1/domains/{id}/topics/{topic_id}` omits skills with `status = 'archived'`,
    omits `DEPENDS_ON` edges incident to archived skills, and omits `DEPENDS_ON` edges
    pointing to or from skills of other topics while keeping valid intra-topic active edges
    (D-6.3).
    """
    learner_id, token = seeded_learner
    headers = _auth_headers(token)

    domain_id = uuid4()
    t1_id = uuid4()
    t2_id = uuid4()
    s1_base = uuid4()
    s1_adv = uuid4()
    s1_archived = uuid4()
    s2_other = uuid4()

    topics = [
        TopicDraft(topic_id=t1_id, name="Main Topic"),
        TopicDraft(topic_id=t2_id, name="Other Topic"),
    ]
    skills = [
        SkillDraft(skill_id=s1_base, topic_id=t1_id, name="Base Active Skill"),
        SkillDraft(skill_id=s1_adv, topic_id=t1_id, name="Advanced Active Skill"),
        SkillDraft(skill_id=s1_archived, topic_id=t1_id, name="Archived Skill"),
        SkillDraft(skill_id=s2_other, topic_id=t2_id, name="Other Topic Skill"),
    ]
    skill_deps = [
        SkillDepEdge(skill_id=s1_adv, depends_on_skill_id=s1_base),
        SkillDepEdge(skill_id=s1_base, depends_on_skill_id=s1_archived),
        SkillDepEdge(skill_id=s1_archived, depends_on_skill_id=s1_adv),
        SkillDepEdge(skill_id=s1_base, depends_on_skill_id=s2_other),
        SkillDepEdge(skill_id=s2_other, depends_on_skill_id=s1_base),
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
                    idempotency_key=f"dom:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "name": "Discrete Math",
                        "slug": f"disc-{domain_id.hex[:6]}",
                        "has_materials": False,
                    },
                ),
                NewEvent(
                    type="structure.proposed",
                    actor="structurer@1",
                    domain_id=domain_id,
                    idempotency_key=f"prop:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "structure_version": 1,
                        "topics": [t.model_dump(mode="json") for t in topics],
                        "skills": [s.model_dump(mode="json") for s in skills],
                        "prereqs": [],
                        "skill_deps": [d.model_dump(mode="json") for d in skill_deps],
                        "misconceptions": [],
                    },
                ),
                NewEvent(
                    type="structure.confirmed",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key=f"conf:{domain_id}",
                    payload={"domain_id": str(domain_id), "structure_version": 1},
                ),
                NewEvent(
                    type="structure.edited",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key=f"edit:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "structure_version": 2,
                        "archived_skill_ids": [str(s1_archived)],
                    },
                ),
            ],
            publish_wakeup=False,
        )

    worker = ProjectorWorker(settings, pg_engine, db, neo4j, redis)
    await worker.run_once()

    resp = await api_client.get(f"/v1/domains/{domain_id}/topics/{t1_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    returned_skills = {UUID(s["skill_id"]) for s in data["skills"]}
    assert returned_skills == {s1_base, s1_adv}
    assert s1_archived not in returned_skills
    assert s2_other not in returned_skills
    assert data["topic"]["skill_count"] == 2

    returned_deps = {
        (UUID(edge["skill_id"]), UUID(edge["depends_on_skill_id"])) for edge in data["skill_deps"]
    }
    assert returned_deps == {(s1_adv, s1_base)}


async def test_graph_view_opens_every_neo4j_session_with_read_access(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every `driver.session(...)` call in `quack/domains/graph_view.py` passes
    `default_access_mode=neo4j.READ_ACCESS` (D-7.2).
    """
    learner_id, token = seeded_learner
    headers = _auth_headers(token)

    domain_id = uuid4()
    topic_id = uuid4()
    skill_id = uuid4()

    async with db() as session, session.begin():
        await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="domain.created",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key=f"dom:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "name": "Geometry",
                        "slug": f"geom-{domain_id.hex[:6]}",
                        "has_materials": False,
                    },
                ),
                NewEvent(
                    type="structure.proposed",
                    actor="structurer@1",
                    domain_id=domain_id,
                    idempotency_key=f"prop:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "structure_version": 1,
                        "topics": [
                            TopicDraft(topic_id=topic_id, name="Triangles").model_dump(mode="json")
                        ],
                        "skills": [
                            SkillDraft(
                                skill_id=skill_id, topic_id=topic_id, name="Law of Sines"
                            ).model_dump(mode="json")
                        ],
                        "prereqs": [],
                        "skill_deps": [],
                        "misconceptions": [],
                    },
                ),
                NewEvent(
                    type="structure.confirmed",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key=f"conf:{domain_id}",
                    payload={"domain_id": str(domain_id), "structure_version": 1},
                ),
            ],
            publish_wakeup=False,
        )

    worker = ProjectorWorker(settings, pg_engine, db, neo4j, redis)
    await worker.run_once()

    real_session = neo4j.session
    graph_view_modes: list[str | None] = []

    def _spy_session(*args: Any, **kwargs: Any) -> Any:
        frame = inspect.currentframe()
        caller_file = frame.f_back.f_code.co_filename if frame and frame.f_back else ""
        if caller_file.replace("\\", "/").endswith("quack/domains/graph_view.py"):
            graph_view_modes.append(kwargs.get("default_access_mode"))
        return real_session(*args, **kwargs)

    monkeypatch.setattr(neo4j, "session", _spy_session)

    g_resp = await api_client.get(f"/v1/domains/{domain_id}/graph", headers=headers)
    assert g_resp.status_code == 200
    t_resp = await api_client.get(f"/v1/domains/{domain_id}/topics/{topic_id}", headers=headers)
    assert t_resp.status_code == 200

    assert len(graph_view_modes) >= 2
    assert all(mode == READ_ACCESS for mode in graph_view_modes)
