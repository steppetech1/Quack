"""Red/Green TDD tests for Contour 2: Domains CRUD, structure onboarding (`structurer@1`),
DAG cycle breaking, LLM degradation fallback, and manual structure editing
(ТЗ §2.2, §3, §4, §5; Т-§3.1–3.2, Т-§16.1–16.2, Т-§17.1).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.domains.schemas import (
    StructurerMisconception,
    StructurerOutput,
    StructurerPrereq,
    StructurerSkill,
    StructurerSkillDep,
    StructurerTopic,
)
from quack.domains.structure import (
    deterministic_structure_node_id,
    has_cycle,
    sanitize_structurer_output,
)
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.worker import ProjectorWorker
from quack.llm.errors import ProviderUnavailableError
from tests.domains.conftest import FakeLLMProvider


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _query_rows(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        rows = (await session.execute(text(sql), params)).mappings()
        return [dict(r) for r in rows]


async def _seed_domain(db: SessionFactory, learner_id: UUID, name: str, slug: str) -> UUID:
    domain_id = uuid4()
    async with db() as session, session.begin():
        await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="domain.created",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key=f"seed:domain:{domain_id}",
                    payload={
                        "domain_id": str(domain_id),
                        "name": name,
                        "slug": slug,
                        "has_materials": False,
                    },
                )
            ],
            publish_wakeup=False,
        )
    return domain_id


async def _drain_projector(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    worker = ProjectorWorker(settings, pg_engine, db, neo4j, redis)
    for _ in range(20):
        if await worker.run_once() == 0:
            return
    raise AssertionError("ProjectorWorker did not converge")


# --- 1. Domains CRUD & Archival (ТЗ §2.2, §3; Т-§16.1) ---------------------------------


async def test_domains_crud_and_archival(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    headers = _auth(token)

    # Create domain: generates slug from name, writes core.domains + domain.created
    create_resp = await api_client.post(
        "/v1/domains",
        headers=headers,
        json={"name": "Linear Algebra & Geometry!", "idempotency_key": "dom:create:1"},
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    domain_id = UUID(created["domain_id"])
    assert created["name"] == "Linear Algebra & Geometry!"
    assert created["slug"] == "linear-algebra-geometry"
    assert created["status"] == "onboarding"
    assert created["seq"] >= 2

    # Verify relational projection in core.domains and event in core.events
    domain_rows = await _query_rows(
        db,
        "SELECT id, slug, status, active_structure_version FROM core.domains "
        "WHERE id = :id AND learner_id = :l",
        id=domain_id,
        l=learner_id,
    )
    assert len(domain_rows) == 1
    assert domain_rows[0]["slug"] == "linear-algebra-geometry"
    assert domain_rows[0]["status"] == "onboarding"
    assert domain_rows[0]["active_structure_version"] == 0

    event_rows = await _query_rows(
        db,
        "SELECT type, payload FROM core.events WHERE learner_id = :l AND domain_id = :d",
        l=learner_id,
        d=domain_id,
    )
    assert [r["type"] for r in event_rows] == ["domain.created"]

    # Creating another domain with the same slug (and a new idempotency_key) -> 409
    conflict_resp = await api_client.post(
        "/v1/domains",
        headers=headers,
        json={"name": "Linear Algebra & Geometry!", "idempotency_key": "dom:create:2"},
    )
    assert conflict_resp.status_code == 409
    assert conflict_resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    # GET /v1/domains and GET /v1/domains/{id}
    list_resp = await api_client.get("/v1/domains", headers=headers)
    assert list_resp.status_code == 200
    assert [d["domain_id"] for d in list_resp.json()["domains"]] == [str(domain_id)]

    get_resp = await api_client.get(f"/v1/domains/{domain_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["domain_id"] == str(domain_id)
    assert get_resp.json()["slug"] == "linear-algebra-geometry"

    # Archive domain -> hidden by default, visible with include_archived=true
    archive_resp = await api_client.post(
        f"/v1/domains/{domain_id}/archive",
        headers=headers,
        json={"reason": "finished semester", "idempotency_key": "dom:archive:1"},
    )
    assert archive_resp.status_code == 200
    assert archive_resp.json()["status"] == "archived"

    active_list = await api_client.get("/v1/domains", headers=headers)
    assert active_list.status_code == 200
    assert active_list.json()["domains"] == []

    all_list = await api_client.get(
        "/v1/domains", headers=headers, params={"include_archived": "true"}
    )
    assert all_list.status_code == 200
    assert len(all_list.json()["domains"]) == 1
    assert all_list.json()["domains"][0]["status"] == "archived"


# --- 2. Onboarding (propose -> confirm) & slug -> UUIDv5 (ТЗ §2.2, §4; Т-§3.1–3.2) -----


async def test_structure_onboarding_propose_and_confirm_uuid5_determinism(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    fake_llm_provider: FakeLLMProvider,
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    learner_id, token = seeded_learner
    headers = _auth(token)
    domain_id = await _seed_domain(db, learner_id, "Calculus", "calculus")

    llm_output = StructurerOutput(
        topics=[
            StructurerTopic(slug="limits", name="Limits", description="Continuity and limits"),
            StructurerTopic(
                slug="derivatives", name="Derivatives", description="Differentiation rules"
            ),
        ],
        skills=[
            StructurerSkill(
                slug="limit-laws",
                topic_slug="limits",
                name="Limit Laws",
                description="Algebraic limit laws",
            ),
            StructurerSkill(
                slug="chain-rule",
                topic_slug="derivatives",
                name="Chain Rule",
                description="Composite derivatives",
            ),
        ],
        prereqs=[StructurerPrereq(from_topic_slug="limits", to_topic_slug="derivatives")],
        skill_deps=[
            StructurerSkillDep(skill_slug="chain-rule", depends_on_skill_slug="limit-laws")
        ],
        misconceptions=[
            StructurerMisconception(
                skill_slug="chain-rule",
                name="Forgetting inner derivative",
                description="Omitting g'(x) in f(g(x))",
            )
        ],
    )
    fake_llm_provider.enqueue(llm_output)

    propose_resp = await api_client.post(
        f"/v1/domains/{domain_id}/structure/propose",
        headers=headers,
        json={
            "goals": "Master single-variable calculus",
            "background": "High-school algebra",
            "focus_areas": ["Limits", "Derivatives"],
            "idempotency_key": "calc:propose:1",
        },
    )
    assert propose_resp.status_code == 200
    proposed = propose_resp.json()
    assert proposed["domain_id"] == str(domain_id)
    assert proposed["structure_version"] == 1
    assert proposed["degraded"] is False

    # Check deterministic slug -> UUIDv5 mapping
    expected_limits_id = deterministic_structure_node_id(domain_id, 1, "topic", "limits")
    expected_deriv_id = deterministic_structure_node_id(domain_id, 1, "topic", "derivatives")
    expected_limit_skill_id = deterministic_structure_node_id(domain_id, 1, "skill", "limit-laws")
    expected_chain_skill_id = deterministic_structure_node_id(domain_id, 1, "skill", "chain-rule")

    topic_ids = {t["name"]: UUID(t["topic_id"]) for t in proposed["topics"]}
    assert topic_ids == {"Limits": expected_limits_id, "Derivatives": expected_deriv_id}

    skill_ids = {s["name"]: UUID(s["skill_id"]) for s in proposed["skills"]}
    assert skill_ids == {
        "Limit Laws": expected_limit_skill_id,
        "Chain Rule": expected_chain_skill_id,
    }
    assert proposed["prereqs"] == [
        {
            "from_topic_id": str(expected_limits_id),
            "to_topic_id": str(expected_deriv_id),
        }
    ]
    assert proposed["skill_deps"] == [
        {
            "skill_id": str(expected_chain_skill_id),
            "depends_on_skill_id": str(expected_limit_skill_id),
        }
    ]
    assert len(proposed["misconceptions"]) == 1
    assert proposed["misconceptions"][0]["skill_id"] == str(expected_chain_skill_id)

    # Verify events and relational domain status ('structuring')
    ev_rows = await _query_rows(
        db,
        "SELECT type FROM core.events WHERE learner_id = :l AND domain_id = :d ORDER BY seq",
        l=learner_id,
        d=domain_id,
    )
    assert [r["type"] for r in ev_rows] == [
        "domain.created",
        "structure.interview_completed",
        "structure.proposed",
    ]
    [dom_row] = await _query_rows(
        db, "SELECT status FROM core.domains WHERE id = :id", id=domain_id
    )
    assert dom_row["status"] == "structuring"

    # Project to Neo4j and check nodes are in 'proposed' status before confirmation
    await _drain_projector(settings, pg_engine, db, neo4j, redis)
    graph_before = await Neo4jGraphStore(neo4j, str(learner_id)).dump()
    assert graph_before.get("Topic", str(expected_limits_id))["status"] == "proposed"  # type: ignore[index]
    assert graph_before.get("Skill", str(expected_chain_skill_id))["status"] == "proposed"  # type: ignore[index]

    # Confirm structure -> transitions domain and Neo4j nodes to 'active'
    confirm_resp = await api_client.post(
        f"/v1/domains/{domain_id}/structure/confirm",
        headers=headers,
        json={"structure_version": 1, "idempotency_key": "calc:confirm:1"},
    )
    assert confirm_resp.status_code == 200
    confirmed = confirm_resp.json()
    assert confirmed["status"] == "active"
    assert confirmed["active_structure_version"] == 1

    [dom_row_after] = await _query_rows(
        db,
        "SELECT status, active_structure_version FROM core.domains WHERE id = :id",
        id=domain_id,
    )
    assert dom_row_after["status"] == "active"
    assert dom_row_after["active_structure_version"] == 1

    await _drain_projector(settings, pg_engine, db, neo4j, redis)
    graph_after = await Neo4jGraphStore(neo4j, str(learner_id)).dump()
    assert graph_after.get("Topic", str(expected_limits_id))["status"] == "active"  # type: ignore[index]
    assert graph_after.get("Topic", str(expected_deriv_id))["status"] == "active"  # type: ignore[index]
    assert graph_after.get("Skill", str(expected_limit_skill_id))["status"] == "active"  # type: ignore[index]
    assert graph_after.get("Skill", str(expected_chain_skill_id))["status"] == "active"  # type: ignore[index]


# --- 3. DAG Sanitization & Cycle Breaking (ТЗ §2.2, Т-§3.2) ----------------------------


async def test_structure_dag_sanitization_and_cycle_breaking(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    headers = _auth(token)
    domain_id = await _seed_domain(db, learner_id, "Graph Theory", "graph-theory")

    # 3-node cycle A -> B -> C -> A in prereqs & skill_deps, plus self-loops and dangling refs
    noisy_output = StructurerOutput(
        topics=[
            StructurerTopic(slug="topic-a", name="Topic A", description="A"),
            StructurerTopic(slug="topic-b", name="Topic B", description="B"),
            StructurerTopic(slug="topic-c", name="Topic C", description="C"),
        ],
        skills=[
            StructurerSkill(slug="skill-a", topic_slug="topic-a", name="Skill A", description=""),
            StructurerSkill(slug="skill-b", topic_slug="topic-b", name="Skill B", description=""),
            StructurerSkill(slug="skill-c", topic_slug="topic-c", name="Skill C", description=""),
            StructurerSkill(
                slug="orphan-skill",
                topic_slug="missing-topic",
                name="Orphan Skill",
                description="Should be dropped",
            ),
        ],
        prereqs=[
            StructurerPrereq(from_topic_slug="topic-a", to_topic_slug="topic-b"),
            StructurerPrereq(from_topic_slug="topic-b", to_topic_slug="topic-c"),
            StructurerPrereq(from_topic_slug="topic-c", to_topic_slug="topic-a"),  # closes cycle
            StructurerPrereq(from_topic_slug="topic-a", to_topic_slug="topic-a"),  # self-loop
            StructurerPrereq(from_topic_slug="topic-a", to_topic_slug="ghost-topic"),  # dangling
        ],
        skill_deps=[
            StructurerSkillDep(skill_slug="skill-a", depends_on_skill_slug="skill-b"),
            StructurerSkillDep(skill_slug="skill-b", depends_on_skill_slug="skill-c"),
            StructurerSkillDep(
                skill_slug="skill-c", depends_on_skill_slug="skill-a"
            ),  # closes cycle
            StructurerSkillDep(skill_slug="skill-b", depends_on_skill_slug="skill-b"),  # self-loop
            StructurerSkillDep(
                skill_slug="skill-a", depends_on_skill_slug="orphan-skill"
            ),  # dangling
        ],
        misconceptions=[
            StructurerMisconception(
                skill_slug="skill-a", name="Valid Misc", description="Attached to skill-a"
            ),
            StructurerMisconception(
                skill_slug="ghost-skill", name="Ghost Misc", description="Should be dropped"
            ),
        ],
    )

    # Check pure sanitizer determinism directly
    unit_domain_id = uuid4()
    topics, skills, prereqs, skill_deps, misconceptions = sanitize_structurer_output(
        unit_domain_id, 1, noisy_output
    )
    assert len(topics) == 3
    assert len(skills) == 3  # orphan-skill dropped
    assert len(misconceptions) == 1  # ghost-skill dropped
    assert len(prereqs) == 2
    assert len(skill_deps) == 2
    assert not has_cycle(
        [t.topic_id for t in topics],
        [(e.from_topic_id, e.to_topic_id) for e in prereqs],
    )
    assert not has_cycle(
        [s.skill_id for s in skills],
        [(d.skill_id, d.depends_on_skill_id) for d in skill_deps],
    )

    # Also verify via POST /v1/domains/{id}/structure/propose endpoint
    fake_llm_provider.enqueue(noisy_output)
    propose_resp = await api_client.post(
        f"/v1/domains/{domain_id}/structure/propose",
        headers=headers,
        json={
            "goals": "Learn DAGs",
            "background": "Discrete math",
            "focus_areas": ["Trees", "DAGs"],
            "idempotency_key": "gt:propose:1",
        },
    )
    assert propose_resp.status_code == 200
    body = propose_resp.json()
    assert len(body["topics"]) == 3
    assert len(body["skills"]) == 3
    assert len(body["prereqs"]) == 2
    assert len(body["skill_deps"]) == 2
    assert len(body["misconceptions"]) == 1

    # Direction of SkillDepEdge(skill_id=dependent, depends_on_skill_id=prerequisite) preserved
    skill_a_id = deterministic_structure_node_id(domain_id, 1, "skill", "skill-a")
    skill_b_id = deterministic_structure_node_id(domain_id, 1, "skill", "skill-b")
    skill_c_id = deterministic_structure_node_id(domain_id, 1, "skill", "skill-c")
    assert body["skill_deps"] == [
        {"skill_id": str(skill_a_id), "depends_on_skill_id": str(skill_b_id)},
        {"skill_id": str(skill_b_id), "depends_on_skill_id": str(skill_c_id)},
    ]

    [ev_row] = await _query_rows(
        db,
        "SELECT payload FROM core.events "
        "WHERE learner_id = :l AND domain_id = :d AND type = 'structure.proposed'",
        l=learner_id,
        d=domain_id,
    )
    assert len(ev_row["payload"]["prereqs"]) == 2
    assert len(ev_row["payload"]["skill_deps"]) == 2


# --- 4. Degradation Without LLM on Propose (ТЗ §2.2, Т-§17.1) --------------------------


async def test_structure_propose_degrades_gracefully_without_llm(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    headers = _auth(token)
    domain_id = await _seed_domain(db, learner_id, "Analysis", "analysis")

    fake_llm_provider.enqueue(ProviderUnavailableError("503 Service Unavailable"))
    focus_areas = ["Limits", "Derivatives", "Integrals"]
    propose_resp = await api_client.post(
        f"/v1/domains/{domain_id}/structure/propose",
        headers=headers,
        json={
            "goals": "Study real analysis",
            "background": "Algebra",
            "focus_areas": focus_areas,
            "idempotency_key": "an:propose:degraded",
        },
    )
    assert propose_resp.status_code == 200
    data = propose_resp.json()
    assert data["degraded"] is True
    assert [t["name"] for t in data["topics"]] == focus_areas
    assert len(data["skills"]) == 3

    # Verify linear PREREQ_OF chain: Limits -> Derivatives -> Integrals
    t0, t1, t2 = (t["topic_id"] for t in data["topics"])
    assert data["prereqs"] == [
        {"from_topic_id": t0, "to_topic_id": t1},
        {"from_topic_id": t1, "to_topic_id": t2},
    ]

    ev_rows = await _query_rows(
        db,
        "SELECT type FROM core.events WHERE learner_id = :l AND domain_id = :d ORDER BY seq",
        l=learner_id,
        d=domain_id,
    )
    assert "structure.proposed" in [r["type"] for r in ev_rows]


# --- 5. Manual Structure Editing & Topic Status Change (ТЗ §2.2, §3; Т-§16.2) ----------


async def test_manual_structure_edit_rejects_cycles_and_updates_topic_status(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> None:
    learner_id, token = seeded_learner
    headers = _auth(token)
    domain_id = await _seed_domain(db, learner_id, "Algorithms", "algorithms")

    # Seed initial confirmed structure: T1 (Arrays) -> PREREQ_OF -> T2 (Sorting)
    t1_id, t2_id = uuid4(), uuid4()
    s1_id, s2_id = uuid4(), uuid4()
    async with db() as session, session.begin():
        await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="structure.interview_completed",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key="alg:interview:1",
                    payload={
                        "domain_id": str(domain_id),
                        "goals": "Pass interviews",
                        "background": "Python basics",
                        "focus_areas": ["Arrays", "Sorting"],
                    },
                ),
                NewEvent(
                    type="structure.proposed",
                    actor="structurer@1",
                    domain_id=domain_id,
                    idempotency_key="alg:proposed:1",
                    payload={
                        "domain_id": str(domain_id),
                        "structure_version": 1,
                        "topics": [
                            {"topic_id": str(t1_id), "name": "Arrays", "description": "Memory"},
                            {"topic_id": str(t2_id), "name": "Sorting", "description": "Sorts"},
                        ],
                        "skills": [
                            {
                                "skill_id": str(s1_id),
                                "topic_id": str(t1_id),
                                "name": "Indexing",
                                "description": "O(1)",
                            },
                            {
                                "skill_id": str(s2_id),
                                "topic_id": str(t2_id),
                                "name": "Merge Sort",
                                "description": "Divide and conquer",
                            },
                        ],
                        "prereqs": [{"from_topic_id": str(t1_id), "to_topic_id": str(t2_id)}],
                        "skill_deps": [],
                        "misconceptions": [],
                    },
                ),
                NewEvent(
                    type="structure.confirmed",
                    actor="learner",
                    domain_id=domain_id,
                    idempotency_key="alg:confirmed:1",
                    payload={"domain_id": str(domain_id), "structure_version": 1},
                ),
            ],
            publish_wakeup=False,
        )
    await _drain_projector(settings, pg_engine, db, neo4j, redis)

    # Attempt to add reverse edge T2 -> PREREQ_OF -> T1 creating a cycle -> 422 VALIDATION_ERROR
    cycle_resp = await api_client.patch(
        f"/v1/domains/{domain_id}/structure",
        headers=headers,
        json={
            "added_prereqs": [{"from_topic_id": str(t2_id), "to_topic_id": str(t1_id)}],
            "idempotency_key": "alg:edit:cycle",
        },
    )
    assert cycle_resp.status_code == 422
    assert cycle_resp.json()["error"]["code"] == "VALIDATION_ERROR"

    edited_events = await _query_rows(
        db,
        "SELECT 1 FROM core.events "
        "WHERE learner_id = :l AND domain_id = :d AND type = 'structure.edited'",
        l=learner_id,
        d=domain_id,
    )
    assert edited_events == []

    # Valid edit: add topic T3, skill S3, and acyclic edge T2 -> PREREQ_OF -> T3
    t3_id = uuid4()
    s3_id = uuid4()
    valid_edit_resp = await api_client.patch(
        f"/v1/domains/{domain_id}/structure",
        headers=headers,
        json={
            "added_topics": [
                {"topic_id": str(t3_id), "name": "Graphs", "description": "BFS and DFS"}
            ],
            "added_skills": [
                {
                    "skill_id": str(s3_id),
                    "topic_id": str(t3_id),
                    "name": "Shortest Path",
                    "description": "Dijkstra",
                }
            ],
            "added_prereqs": [{"from_topic_id": str(t2_id), "to_topic_id": str(t3_id)}],
            "idempotency_key": "alg:edit:valid",
        },
    )
    assert valid_edit_resp.status_code == 200
    edit_body = valid_edit_resp.json()
    assert edit_body["domain_id"] == str(domain_id)
    assert edit_body["active_structure_version"] == 2

    # Change topic status via PATCH /v1/domains/{domain_id}/topics/{topic_id}/status
    status_resp = await api_client.patch(
        f"/v1/domains/{domain_id}/topics/{t1_id}/status",
        headers=headers,
        json={
            "status": "skipped",
            "reason": "Already comfortable with arrays",
            "idempotency_key": "alg:topic-status:1",
        },
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["topic_id"] == str(t1_id)
    assert status_resp.json()["status"] == "skipped"

    all_events = await _query_rows(
        db,
        "SELECT type FROM core.events WHERE learner_id = :l AND domain_id = :d ORDER BY seq",
        l=learner_id,
        d=domain_id,
    )
    event_types = [r["type"] for r in all_events]
    assert "structure.edited" in event_types
    assert "topic.status_changed" in event_types
