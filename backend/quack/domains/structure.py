"""Domain structure onboarding, `structurer@1` invocation, deterministic DAG cycle breaking,
LLM degradation fallback, confirmation, and manual structure editing
(ТЗ §2.2, §3, §4; Т-§3.1, Т-§3.2, Т-§16.2, Т-§17.1).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from uuid import UUID, uuid5

import neo4j
from sqlalchemy import text

from quack.api.errors import ApiError
from quack.api.resources import AppResources
from quack.config import Settings
from quack.domains.schemas import (
    ConfirmStructureRequest,
    ConfirmStructureResponse,
    EditStructureRequest,
    EditStructureResponse,
    MisconceptionDraft,
    PrereqEdge,
    ProposeStructureRequest,
    ProposeStructureResponse,
    SkillDepEdge,
    SkillDraft,
    StructurerOutput,
    TopicDraft,
    UpdateTopicStatusRequest,
    UpdateTopicStatusResponse,
)
from quack.domains.service import slugify_domain_name
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.graph.ids import QUACK_UUID_NS
from quack.graph.neo4j_store import read_applied_seq
from quack.graph.slice import SliceDeps, read_consistent_slice
from quack.llm.errors import LLMBudgetExceededError, LLMSchemaError, LLMUnavailableError
from quack.llm.gateway import LLMGateway
from quack.logging import get_logger

log = get_logger(__name__)


def deterministic_structure_node_id(
    domain_id: UUID,
    structure_version: int,
    kind: str,
    key: str | int,
) -> UUID:
    """Compute deterministic UUIDv5 for a structure node from `(domain_id, structure_version,
    kind, key)` (ТЗ §2.2, §4; Т-§2.5, Т-§3.2).
    """
    return uuid5(QUACK_UUID_NS, f"{domain_id}:{structure_version}:{kind}:{key}")


def has_cycle[T](
    nodes: Iterable[T],
    edges: Iterable[tuple[T, T]],
) -> bool:
    """Return True if the directed graph `(nodes, edges)` contains any cycle (ТЗ §2.2, Т-§16.2)."""
    all_nodes = set(nodes)
    adj: dict[T, list[T]] = {n: [] for n in all_nodes}
    for u, v in edges:
        if u == v:
            return True
        if u in all_nodes and v in all_nodes:
            adj[u].append(v)

    color: dict[T, int] = {n: 0 for n in all_nodes}

    def dfs(u: T) -> bool:
        color[u] = 1
        for v in adj[u]:
            c = color[v]
            if c == 1:
                return True
            if c == 0 and dfs(v):
                return True
        color[u] = 2
        return False

    return any(color[n] == 0 and dfs(n) for n in all_nodes)


def break_cycles[T](
    nodes: Iterable[T],
    edges: Sequence[tuple[T, T]],
) -> tuple[list[tuple[T, T]], list[tuple[T, T]]]:
    """Deterministically detect and remove back-edges closing cycles in a directed graph,
    returning `(acyclic_edges, removed_edges)` (ТЗ §2.2, Т-§3.2).
    """
    all_nodes = set(nodes)
    kept: list[tuple[T, T]] = []
    removed: list[tuple[T, T]] = []

    for u, v in edges:
        if (
            u not in all_nodes
            or v not in all_nodes
            or u == v
            or (u, v) in kept
            or has_cycle(all_nodes, [*kept, (u, v)])
        ):
            removed.append((u, v))
        else:
            kept.append((u, v))

    return kept, removed


def sanitize_structurer_output(
    domain_id: UUID,
    structure_version: int,
    output: StructurerOutput,
) -> tuple[
    list[TopicDraft],
    list[SkillDraft],
    list[PrereqEdge],
    list[SkillDepEdge],
    list[MisconceptionDraft],
]:
    """Map LLM slugs to deterministic UUIDv5, drop dangling references and self-loops, and
    break cycles in topic `PREREQ_OF` and skill `DEPENDS_ON` edges (ТЗ §2.2, §4; Т-§3.2).
    """
    topics: list[TopicDraft] = []
    topic_map: dict[str, TopicDraft] = {}
    for t in output.topics:
        tid = deterministic_structure_node_id(domain_id, structure_version, "topic", t.slug)
        draft = TopicDraft(topic_id=tid, name=t.name, description=t.description or None)
        topics.append(draft)
        topic_map[t.slug] = draft

    skills: list[SkillDraft] = []
    skill_map: dict[str, SkillDraft] = {}
    for s in output.skills:
        if s.topic_slug not in topic_map:
            continue
        sid = deterministic_structure_node_id(domain_id, structure_version, "skill", s.slug)
        tid = topic_map[s.topic_slug].topic_id
        sdraft = SkillDraft(
            skill_id=sid, topic_id=tid, name=s.name, description=s.description or None
        )
        skills.append(sdraft)
        skill_map[s.slug] = sdraft

    candidate_prereqs: list[tuple[UUID, UUID]] = []
    for p in output.prereqs:
        if p.from_topic_slug in topic_map and p.to_topic_slug in topic_map:
            candidate_prereqs.append(
                (topic_map[p.from_topic_slug].topic_id, topic_map[p.to_topic_slug].topic_id)
            )
    topic_ids = [t.topic_id for t in topics]
    kept_prereqs, removed_prereqs = break_cycles(topic_ids, candidate_prereqs)
    if removed_prereqs:
        log.warning(
            "structurer_prereq_cycles_removed",
            domain_id=str(domain_id),
            structure_version=structure_version,
            removed_count=len(removed_prereqs),
        )
    prereq_edges = [PrereqEdge(from_topic_id=u, to_topic_id=v) for u, v in kept_prereqs]

    candidate_skill_deps: list[tuple[UUID, UUID]] = []
    for d in output.skill_deps:
        if d.skill_slug in skill_map and d.depends_on_skill_slug in skill_map:
            candidate_skill_deps.append(
                (skill_map[d.skill_slug].skill_id, skill_map[d.depends_on_skill_slug].skill_id)
            )
    skill_ids = [s.skill_id for s in skills]
    kept_skill_deps, removed_skill_deps = break_cycles(skill_ids, candidate_skill_deps)
    if removed_skill_deps:
        log.warning(
            "structurer_skill_dep_cycles_removed",
            domain_id=str(domain_id),
            structure_version=structure_version,
            removed_count=len(removed_skill_deps),
        )
    skill_dep_edges = [SkillDepEdge(skill_id=u, depends_on_skill_id=v) for u, v in kept_skill_deps]

    misconceptions: list[MisconceptionDraft] = []
    for idx, m in enumerate(output.misconceptions):
        if m.skill_slug not in skill_map:
            continue
        mid = deterministic_structure_node_id(
            domain_id, structure_version, "misconception", f"{m.skill_slug}:{idx}"
        )
        sid = skill_map[m.skill_slug].skill_id
        mdraft = MisconceptionDraft(
            misconception_id=mid,
            skill_id=sid,
            name=m.name,
            description=m.description or None,
        )
        misconceptions.append(mdraft)

    return topics, skills, prereq_edges, skill_dep_edges, misconceptions


def build_degraded_structure(
    domain_id: UUID,
    structure_version: int,
    focus_areas: Sequence[str],
) -> tuple[
    list[TopicDraft],
    list[SkillDraft],
    list[PrereqEdge],
    list[SkillDepEdge],
    list[MisconceptionDraft],
]:
    """Build a minimal linear DAG skeleton from `focus_areas` when LLM is unavailable or over
    budget (ТЗ §2.2, Т-§17.1).
    """
    areas = list(focus_areas) if focus_areas else ["General"]
    topics: list[TopicDraft] = []
    skills: list[SkillDraft] = []
    prereqs: list[PrereqEdge] = []

    for area in areas:
        slug = slugify_domain_name(area)
        tid = deterministic_structure_node_id(domain_id, structure_version, "topic", slug)
        tdraft = TopicDraft(topic_id=tid, name=area, description=f"Fundamentals of {area}")
        topics.append(tdraft)

        sid = deterministic_structure_node_id(domain_id, structure_version, "skill", f"{slug}-core")
        sdraft = SkillDraft(
            skill_id=sid,
            topic_id=tid,
            name=f"{area} Basics",
            description=f"Core concepts of {area}",
        )
        skills.append(sdraft)

    for i in range(len(topics) - 1):
        prereqs.append(
            PrereqEdge(
                from_topic_id=topics[i].topic_id,
                to_topic_id=topics[i + 1].topic_id,
            )
        )

    return topics, skills, prereqs, [], []


async def propose_structure(
    resources: AppResources,
    settings: Settings,
    llm: LLMGateway,
    learner_id: UUID,
    domain_id: UUID,
    req: ProposeStructureRequest,
) -> ProposeStructureResponse:
    """Record `structure.interview_completed` (if onboarding), call `structurer@1` (with
    degradation fallback on `LLMUnavailableError` / `LLMBudgetExceededError`), sanitize DAG,
    and emit `structure.proposed` (ТЗ §2.2, §3; Т-§3.1, Т-§3.2, Т-§17.1).
    """
    async with resources.session_factory() as session:
        dom = (
            (
                await session.execute(
                    text(
                        "SELECT id, name, status, active_structure_version FROM core.domains "
                        "WHERE id = :id AND learner_id = :l"
                    ),
                    {"id": domain_id, "l": learner_id},
                )
            )
            .mappings()
            .first()
        )
        if not dom:
            raise ApiError("NOT_FOUND", f"Domain {domain_id} not found", status_code=404)
        domain_name = dom["name"]
        domain_status = dom["status"]

    structure_version = 1

    degraded = False
    try:
        res = await llm.generate_structured(
            role="structurer",
            prompt_version=1,
            response_model=StructurerOutput,
            variables={
                "domain_name": domain_name,
                "goals": req.goals,
                "background": req.background,
                "focus_areas": ", ".join(req.focus_areas)
                if isinstance(req.focus_areas, list)
                else str(req.focus_areas),
            },
            learner_id=learner_id,
            session_id=None,
            turn_id=None,
            idempotency_key=req.idempotency_key,
        )
        topics, skills, prereqs, skill_deps, misconceptions = sanitize_structurer_output(
            domain_id, structure_version, res.value
        )
    except (LLMUnavailableError, LLMBudgetExceededError, LLMSchemaError) as exc:
        log.warning(
            "structurer_degraded_fallback",
            domain_id=str(domain_id),
            learner_id=str(learner_id),
            error=str(exc),
        )
        topics, skills, prereqs, skill_deps, misconceptions = build_degraded_structure(
            domain_id, structure_version, req.focus_areas
        )
        degraded = True

    events: list[NewEvent] = []
    if domain_status == "onboarding":
        events.append(
            NewEvent(
                type="structure.interview_completed",
                actor="learner",
                domain_id=domain_id,
                idempotency_key=f"interview:{domain_id}",
                payload={
                    "domain_id": str(domain_id),
                    "goals": req.goals,
                    "background": req.background,
                    "focus_areas": req.focus_areas,
                },
            )
        )

    events.append(
        NewEvent(
            type="structure.proposed",
            actor="code" if degraded else "structurer@1",
            domain_id=domain_id,
            idempotency_key=req.idempotency_key,
            payload={
                "domain_id": str(domain_id),
                "structure_version": structure_version,
                "topics": [t.model_dump(mode="json") for t in topics],
                "skills": [s.model_dump(mode="json") for s in skills],
                "prereqs": [p.model_dump(mode="json") for p in prereqs],
                "skill_deps": [d.model_dump(mode="json") for d in skill_deps],
                "misconceptions": [m.model_dump(mode="json") for m in misconceptions],
            },
        )
    )

    async with resources.session_factory() as session, session.begin():
        await append_events(session, learner_id, events, publish_wakeup=True, redis=resources.redis)

    return ProposeStructureResponse(
        domain_id=domain_id,
        structure_version=structure_version,
        topics=topics,
        skills=skills,
        prereqs=prereqs,
        skill_deps=skill_deps,
        misconceptions=misconceptions,
        degraded=degraded,
    )


async def confirm_structure(
    resources: AppResources,
    learner_id: UUID,
    domain_id: UUID,
    req: ConfirmStructureRequest,
) -> ConfirmStructureResponse:
    """Confirm proposed structure and emit `structure.confirmed` (ТЗ §2.2, §3; Т-§3.2, Т-§16.2)."""
    async with resources.session_factory() as session, session.begin():
        dom = (
            (
                await session.execute(
                    text("SELECT id, status FROM core.domains WHERE id = :id AND learner_id = :l"),
                    {"id": domain_id, "l": learner_id},
                )
            )
            .mappings()
            .first()
        )
        if not dom:
            raise ApiError("NOT_FOUND", f"Domain {domain_id} not found", status_code=404)

        proposed = (
            await session.execute(
                text(
                    "SELECT 1 FROM core.events "
                    "WHERE learner_id = :l AND domain_id = :d AND type = 'structure.proposed' "
                    "AND (payload->>'structure_version')::int = :v LIMIT 1"
                ),
                {"l": learner_id, "d": domain_id, "v": req.structure_version},
            )
        ).first()
        if not proposed:
            raise ApiError(
                "VALIDATION_ERROR",
                f"No proposed structure v{req.structure_version} found for domain {domain_id}",
                status_code=422,
            )

        event = NewEvent(
            type="structure.confirmed",
            actor="learner",
            domain_id=domain_id,
            idempotency_key=req.idempotency_key,
            payload={
                "domain_id": str(domain_id),
                "structure_version": req.structure_version,
            },
        )
        stored = await append_events(
            session, learner_id, [event], publish_wakeup=True, redis=resources.redis
        )

        return ConfirmStructureResponse(
            domain_id=domain_id,
            status="active",
            active_structure_version=req.structure_version,
            seq=stored[0].seq,
        )


async def edit_structure(
    resources: AppResources,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    req: EditStructureRequest,
) -> EditStructureResponse:
    """Validate manual edits against the existing `PREREQ_OF` DAG in Neo4j (rejecting cycles
    with `422 VALIDATION_ERROR`) and emit `structure.edited` (ТЗ §2.2, §3; Т-§16.2).
    """
    async with resources.session_factory() as session:
        dom = (
            (
                await session.execute(
                    text(
                        "SELECT id, active_structure_version FROM core.domains "
                        "WHERE id = :id AND learner_id = :l"
                    ),
                    {"id": domain_id, "l": learner_id},
                )
            )
            .mappings()
            .first()
        )
        if not dom:
            raise ApiError("NOT_FOUND", f"Domain {domain_id} not found", status_code=404)
        active_version = int(dom["active_structure_version"])
        new_version = active_version + 1

    deps = SliceDeps(
        session_factory=resources.session_factory,
        redis=resources.redis,
        applied_seq_reader=lambda lid: read_applied_seq(resources.neo4j, str(lid)),
    )

    async def _load_existing_dag() -> tuple[set[UUID], set[tuple[UUID, UUID]]]:
        async with resources.neo4j.session(default_access_mode=neo4j.READ_ACCESS) as n_session:
            t_res = await n_session.run(
                "MATCH (t:Topic {learnerId: $l, domainId: $d}) RETURN t.id AS id",
                {"l": str(learner_id), "d": str(domain_id)},
            )
            topic_ids = {UUID(r["id"]) async for r in t_res}

            p_res = await n_session.run(
                "MATCH (a:Topic {learnerId: $l, domainId: $d})-[r:PREREQ_OF]->"
                "(b:Topic {learnerId: $l, domainId: $d}) "
                "RETURN a.id AS from_id, b.id AS to_id",
                {"l": str(learner_id), "d": str(domain_id)},
            )
            edges = {(UUID(r["from_id"]), UUID(r["to_id"])) async for r in p_res}
            return topic_ids, edges

    slice_res = await read_consistent_slice(
        learner_id, min_seq=None, loader_fn=_load_existing_dag, deps=deps
    )
    existing_topic_ids, existing_edges = slice_res.data

    all_topic_ids = set(existing_topic_ids)
    for t in req.added_topics:
        all_topic_ids.add(t.topic_id)
    for tid in req.archived_topic_ids:
        all_topic_ids.discard(tid)

    current_edges = set(existing_edges)
    for rem in req.removed_prereqs:
        current_edges.discard((rem.from_topic_id, rem.to_topic_id))
    current_edges = {(u, v) for u, v in current_edges if u in all_topic_ids and v in all_topic_ids}

    for add in req.added_prereqs:
        if add.from_topic_id == add.to_topic_id:
            raise ApiError(
                "VALIDATION_ERROR",
                "Self-loop detected in topic prerequisites",
                status_code=422,
            )
        all_topic_ids.add(add.from_topic_id)
        all_topic_ids.add(add.to_topic_id)
        current_edges.add((add.from_topic_id, add.to_topic_id))

    if has_cycle(all_topic_ids, current_edges):
        raise ApiError(
            "VALIDATION_ERROR",
            "Cycle detected in topic prerequisites",
            status_code=422,
        )

    payload = {
        "domain_id": str(domain_id),
        "structure_version": new_version,
        "added_topics": [t.model_dump(mode="json") for t in req.added_topics],
        "updated_topics": [t.model_dump(mode="json") for t in req.updated_topics],
        "archived_topic_ids": [str(x) for x in req.archived_topic_ids],
        "added_prereqs": [p.model_dump(mode="json") for p in req.added_prereqs],
        "removed_prereqs": [p.model_dump(mode="json") for p in req.removed_prereqs],
        "added_skills": [s.model_dump(mode="json") for s in req.added_skills],
        "updated_skills": [s.model_dump(mode="json") for s in req.updated_skills],
        "archived_skill_ids": [str(x) for x in req.archived_skill_ids],
    }
    event = NewEvent(
        type="structure.edited",
        actor="learner",
        domain_id=domain_id,
        idempotency_key=req.idempotency_key,
        payload=payload,
    )
    async with resources.session_factory() as session, session.begin():
        stored = await append_events(
            session, learner_id, [event], publish_wakeup=True, redis=resources.redis
        )

    return EditStructureResponse(
        domain_id=domain_id,
        active_structure_version=new_version,
        seq=stored[0].seq,
    )


async def update_topic_status(
    resources: AppResources,
    learner_id: UUID,
    domain_id: UUID,
    topic_id: UUID,
    req: UpdateTopicStatusRequest,
) -> UpdateTopicStatusResponse:
    """Change topic status (`active`, `skipped`, `archived`) via `topic.status_changed`
    (ТЗ §2.2, §3; Т-§16.2).
    """
    async with resources.session_factory() as session:
        dom = (
            await session.execute(
                text("SELECT id FROM core.domains WHERE id = :id AND learner_id = :l"),
                {"id": domain_id, "l": learner_id},
            )
        ).first()
        if not dom:
            raise ApiError("NOT_FOUND", f"Domain {domain_id} not found", status_code=404)

    event = NewEvent(
        type="topic.status_changed",
        actor="learner",
        domain_id=domain_id,
        idempotency_key=req.idempotency_key,
        payload={
            "domain_id": str(domain_id),
            "topic_id": str(topic_id),
            "status": req.status,
            "reason": req.reason,
        },
    )
    async with resources.session_factory() as session, session.begin():
        stored = await append_events(
            session, learner_id, [event], publish_wakeup=True, redis=resources.redis
        )

    return UpdateTopicStatusResponse(
        topic_id=topic_id,
        status=req.status,
        seq=stored[0].seq,
    )
