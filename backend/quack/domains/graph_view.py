"""Knowledge map and topic detail views for the UI using `read_consistent_slice` with
read-time recomputation of `effective_mastery`, `is_rusty`, and `display_state`
(ТЗ §2.4, §3; Т-§8.6, Т-§22.2).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import neo4j

from quack.api.errors import ApiError
from quack.api.resources import AppResources
from quack.config import Settings
from quack.domains.schemas import (
    ArtifactRefView,
    DomainGraphResponse,
    MisconceptionNodeView,
    PrereqEdge,
    SkillDepEdge,
    SkillNodeView,
    TopicDetailResponse,
    TopicNodeView,
)
from quack.graph.folds import SkillFoldResult, effective_mastery, skill_is_rusty
from quack.graph.neo4j_store import read_applied_seq
from quack.graph.slice import SliceDeps, read_consistent_slice
from quack.graph.topic_display import (
    PrereqSnapshot,
    SkillSnapshot,
    TopicDisplayResult,
    recompute_topic_display,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def to_ms(moment: datetime) -> int:
    return (moment - _EPOCH) // timedelta(milliseconds=1)


def _skill_snapshot(sk_id: str, props: dict[str, Any], now_ms: int) -> SkillSnapshot:
    """Skill snapshot whose `p_mastery` is the forgetting-adjusted `effective_mastery`."""
    return SkillSnapshot(
        skill_id=sk_id,
        status=str(props.get("status", "unknown")),
        p_mastery=effective_mastery(
            float(props.get("pMastery", 0.0)),
            float(props.get("stability", 0.0)),
            int(props.get("lastReviewedAt", 0)),
            now_ms,
        ),
        confidence=float(props.get("confidence", 0.0)),
        evidence_count=int(props.get("evidenceCount", 0)),
        exposure_count=int(props.get("exposureCount", 0)),
        mastery_status=str(props.get("masteryStatus", "unknown")),
        next_review_at=int(props.get("nextReviewAt", 0)),
        last_practiced_at=int(props.get("lastPracticedAt", 0)),
    )


def _recompute_display(
    skills: list[SkillSnapshot],
    raw_p_mastery: dict[str, float],
    dependents: dict[str, int],
    prereqs: list[PrereqSnapshot],
    now_ms: int,
    settings: Settings,
) -> TopicDisplayResult:
    """`mastery` and the prerequisite unlock use decayed mastery; the solid/rusty gate keeps
    the stored `pMastery` so that a once-solid topic whose review is overdue shows `rusty`
    instead of falling back to `in_progress` (Т-§8.6).
    """
    disp = recompute_topic_display(
        skills, dependents, prereqs, as_of_ms=now_ms, params=settings.fold
    )
    raw_skills = [replace(s, p_mastery=raw_p_mastery.get(s.skill_id, s.p_mastery)) for s in skills]
    raw_disp = recompute_topic_display(
        raw_skills, dependents, prereqs, as_of_ms=now_ms, params=settings.fold
    )
    if raw_disp.display_state in ("solid", "rusty"):
        return replace(disp, display_state=raw_disp.display_state)
    return disp


async def get_domain_graph_view(
    resources: AppResources,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    *,
    include_archived: bool = False,
) -> DomainGraphResponse:
    """Read consistent domain knowledge graph slice from Neo4j, recomputing `display_state`
    (`rusty` vs `solid`) as of `now_ms` and excluding `merged` (and `archived` unless
    `include_archived=True`) nodes (ТЗ §2.4, §3; Т-§8.6, Т-§22.2).
    """
    deps = SliceDeps(
        session_factory=resources.session_factory,
        redis=resources.redis,
        applied_seq_reader=lambda lid: read_applied_seq(resources.neo4j, str(lid)),
    )

    async def _loader() -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, int],
    ]:
        async with resources.neo4j.session(default_access_mode=neo4j.READ_ACCESS) as s:
            d_res = await s.run(
                "MATCH (d:Domain {id: $d, learnerId: $l}) RETURN d.id AS id",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            if not await d_res.single():
                raise ApiError("NOT_FOUND", "Domain not found", status_code=404)

            t_res = await s.run(
                "MATCH (d:Domain {id: $d, learnerId: $l})-[:HAS_TOPIC]->(t:Topic) "
                "WHERE t.status <> 'merged' "
                "RETURN t.id AS id, properties(t) AS props",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            topics = [rec.data() async for rec in t_res]

            sk_res = await s.run(
                "MATCH (t:Topic)<-[:PART_OF]-(sk:Skill) "
                "WHERE t.domainId = $d AND t.learnerId = $l AND sk.status <> 'merged' "
                "RETURN sk.id AS id, t.id AS topic_id, properties(sk) AS props",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            skills = [rec.data() async for rec in sk_res]

            p_res = await s.run(
                "MATCH (a:Topic)-[r:PREREQ_OF]->(b:Topic) "
                "WHERE a.domainId = $d AND b.domainId = $d AND a.learnerId = $l "
                "AND a.status <> 'merged' AND b.status <> 'merged' "
                "RETURN a.id AS from_id, b.id AS to_id",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            prereqs = [rec.data() async for rec in p_res]

            dep_res = await s.run(
                "MATCH (a:Skill)-[:DEPENDS_ON]->(b:Skill) "
                "WHERE b.domainId = $d AND b.learnerId = $l AND a.status <> 'merged' "
                "RETURN b.id AS id, count(a) AS cnt",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            dep_counts = {rec["id"]: rec["cnt"] async for rec in dep_res}
            return topics, skills, prereqs, dep_counts

    slice_res = await read_consistent_slice(learner_id, min_seq=None, loader_fn=_loader, deps=deps)
    topics_raw, skills_raw, prereqs_raw, dep_counts = slice_res.data
    now_ms = to_ms(datetime.now(UTC))

    skills_by_topic: dict[str, list[SkillSnapshot]] = {}
    raw_p_mastery: dict[str, float] = {}
    for sk in skills_raw:
        props = sk["props"] or {}
        snap = _skill_snapshot(sk["id"], props, now_ms)
        raw_p_mastery[sk["id"]] = float(props.get("pMastery", 0.0))
        skills_by_topic.setdefault(sk["topic_id"], []).append(snap)

    topic_mastery: dict[str, float] = {}
    for t in topics_raw:
        t_id = t["id"]
        t_skills = skills_by_topic.get(t_id, [])
        res = recompute_topic_display(
            t_skills, dep_counts, [], as_of_ms=now_ms, params=settings.fold
        )
        topic_mastery[t_id] = res.mastery

    visible_topics = []
    for t in topics_raw:
        props = t["props"] or {}
        status = str(props.get("status", "active"))
        if status == "merged":
            continue
        if status == "archived" and not include_archived:
            continue
        visible_topics.append(t)

    topic_node_views: list[TopicNodeView] = []
    for t in visible_topics:
        t_id = t["id"]
        props = t["props"] or {}
        status = str(props.get("status", "active"))
        t_skills = skills_by_topic.get(t_id, [])
        prereq_snaps = [
            PrereqSnapshot(
                topic_id=p["from_id"],
                status=str(
                    next(
                        (
                            top["props"].get("status", "unknown")
                            for top in topics_raw
                            if top["id"] == p["from_id"]
                        ),
                        "unknown",
                    )
                ),
                mastery=topic_mastery.get(p["from_id"], 0.0),
            )
            for p in prereqs_raw
            if p["to_id"] == t_id
        ]
        disp = _recompute_display(
            t_skills, raw_p_mastery, dep_counts, prereq_snaps, now_ms, settings
        )
        topic_node_views.append(
            TopicNodeView(
                topic_id=UUID(t_id),
                domain_id=domain_id,
                name=str(props.get("name", "")),
                description=str(props.get("description", "")),
                status=status,
                mastery=disp.mastery,
                coverage=disp.coverage,
                confidence=disp.confidence,
                hub_score=disp.hub_score,
                nearest_review_at=disp.nearest_review_at,
                display_state=disp.display_state,
                skill_count=disp.skill_count,
            )
        )

    visible_ids = {UUID(t["id"]) for t in visible_topics}
    filtered_prereqs = [
        PrereqEdge(from_topic_id=UUID(p["from_id"]), to_topic_id=UUID(p["to_id"]))
        for p in prereqs_raw
        if UUID(p["from_id"]) in visible_ids and UUID(p["to_id"]) in visible_ids
    ]

    return DomainGraphResponse(
        domain_id=domain_id,
        applied_seq=slice_res.applied_seq,
        lag_degraded=slice_res.lag_degraded,
        topics=topic_node_views,
        prereqs=filtered_prereqs,
    )


async def get_topic_detail_view(
    resources: AppResources,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    topic_id: UUID,
) -> TopicDetailResponse:
    """Read consistent topic card details (topic, skills with `effective_mastery` and
    `is_rusty`, `skill_deps`, `misconceptions`, `artifacts`) as of `now_ms`
    (ТЗ §2.4, §3; Т-§8.6, Т-§22.2).
    """
    deps = SliceDeps(
        session_factory=resources.session_factory,
        redis=resources.redis,
        applied_seq_reader=lambda lid: read_applied_seq(resources.neo4j, str(lid)),
    )

    async def _loader() -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, int],
    ]:
        async with resources.neo4j.session(default_access_mode=neo4j.READ_ACCESS) as s:
            t_res = await s.run(
                "MATCH (d:Domain {id: $d, learnerId: $l})-[:HAS_TOPIC]->(t:Topic {id: $tid}) "
                "WHERE t.status <> 'merged' "
                "RETURN t.id AS id, properties(t) AS props",
                {"d": str(domain_id), "l": str(learner_id), "tid": str(topic_id)},
            )
            top_rec = await t_res.single()
            if not top_rec:
                raise ApiError("NOT_FOUND", "Topic not found", status_code=404)
            topic_dict = top_rec.data()

            sk_res = await s.run(
                "MATCH (t:Topic {id: $tid})<-[:PART_OF]-(sk:Skill) "
                "WHERE t.domainId = $d AND t.learnerId = $l AND sk.status <> 'merged' "
                "AND coalesce(sk.status, 'active') <> 'archived' "
                "RETURN sk.id AS id, properties(sk) AS props",
                {"d": str(domain_id), "l": str(learner_id), "tid": str(topic_id)},
            )
            skills = [rec.data() async for rec in sk_res]

            sd_res = await s.run(
                "MATCH (a:Skill)-[r:DEPENDS_ON]->(b:Skill) "
                "WHERE a.topicId = $tid AND b.topicId = $tid "
                "AND a.domainId = $d AND a.learnerId = $l "
                "AND a.status <> 'merged' AND b.status <> 'merged' "
                "AND coalesce(a.status, 'active') <> 'archived' "
                "AND coalesce(b.status, 'active') <> 'archived' "
                "RETURN a.id AS sid, b.id AS did",
                {"d": str(domain_id), "l": str(learner_id), "tid": str(topic_id)},
            )
            skill_deps = [rec.data() async for rec in sd_res]

            m_res = await s.run(
                "MATCH (m:Misconception)-[:ABOUT_SKILL]->(s:Skill) "
                "WHERE s.topicId = $tid AND s.domainId = $d AND s.learnerId = $l "
                "AND m.status <> 'merged' "
                "RETURN m.id AS id, s.id AS skill_id, properties(m) AS props",
                {"d": str(domain_id), "l": str(learner_id), "tid": str(topic_id)},
            )
            misconceptions = [rec.data() async for rec in m_res]

            a_res = await s.run(
                "MATCH (t:Topic {id: $tid, learnerId: $l})-[:HAS_ARTIFACT]->(a:Artifact) "
                "WHERE a.isDeleted <> true "
                "RETURN a.id AS id, a.kind AS kind, a.title AS title, a.version AS version",
                {"l": str(learner_id), "tid": str(topic_id)},
            )
            artifacts = [rec.data() async for rec in a_res]

            p_res = await s.run(
                "MATCH (p:Topic)-[:PREREQ_OF]->(t:Topic {id: $tid}) "
                "WHERE p.domainId = $d AND p.learnerId = $l AND p.status <> 'merged' "
                "RETURN p.id AS id, properties(p) AS props",
                {"d": str(domain_id), "l": str(learner_id), "tid": str(topic_id)},
            )
            prereqs = [rec.data() async for rec in p_res]

            psk_res = await s.run(
                "MATCH (p:Topic)-[:PREREQ_OF]->(:Topic {id: $tid}) "
                "WHERE p.domainId = $d AND p.learnerId = $l AND p.status <> 'merged' "
                "MATCH (p)<-[:PART_OF]-(sk:Skill) "
                "WHERE sk.status <> 'merged' AND coalesce(sk.status, 'active') <> 'archived' "
                "OPTIONAL MATCH (a:Skill)-[:DEPENDS_ON]->(sk) "
                "WHERE a.status <> 'merged' AND coalesce(a.status, 'active') <> 'archived' "
                "RETURN p.id AS topic_id, sk.id AS id, properties(sk) AS props, count(a) AS cnt",
                {"d": str(domain_id), "l": str(learner_id), "tid": str(topic_id)},
            )
            prereq_skills = [rec.data() async for rec in psk_res]

            dep_res = await s.run(
                "MATCH (a:Skill)-[:DEPENDS_ON]->(b:Skill) "
                "WHERE b.topicId = $tid AND b.domainId = $d AND b.learnerId = $l "
                "AND a.status <> 'merged' AND coalesce(a.status, 'active') <> 'archived' "
                "RETURN b.id AS id, count(a) AS cnt",
                {"d": str(domain_id), "l": str(learner_id), "tid": str(topic_id)},
            )
            dep_counts = {rec["id"]: rec["cnt"] async for rec in dep_res}

            return (
                topic_dict,
                skills,
                skill_deps,
                misconceptions,
                artifacts,
                prereqs,
                prereq_skills,
                dep_counts,
            )

    slice_res = await read_consistent_slice(learner_id, min_seq=None, loader_fn=_loader, deps=deps)
    (
        topic_raw,
        skills_raw,
        skill_deps_raw,
        misc_raw,
        artifacts_raw,
        prereqs_raw,
        prereq_skills_raw,
        dep_counts,
    ) = slice_res.data
    now_ms = to_ms(datetime.now(UTC))

    skill_views: list[SkillNodeView] = []
    skill_snaps: list[SkillSnapshot] = []
    raw_p_mastery: dict[str, float] = {}

    for sk in skills_raw:
        props = sk["props"] or {}
        p_m = float(props.get("pMastery", 0.0))
        stab = float(props.get("stability", 0.0))
        l_rev = int(props.get("lastReviewedAt", 0))
        n_rev = int(props.get("nextReviewAt", 0))
        m_stat = str(props.get("masteryStatus", "unknown"))

        eff_m = effective_mastery(p_m, stab, l_rev, now_ms)
        fold_snapshot = SkillFoldResult(
            p_mastery=p_m,
            confidence=float(props.get("confidence", 0.0)),
            evidence_count=int(props.get("evidenceCount", 0)),
            distinct_families=int(props.get("distinctFamilies", 0)),
            decay_rate=float(props.get("decayRate", 0.0)),
            stability=stab,
            last_practiced_at=int(props.get("lastPracticedAt", 0)),
            last_reviewed_at=l_rev,
            next_review_at=n_rev,
            mastery_status=m_stat,  # type: ignore[arg-type]
            exposure_count=int(props.get("exposureCount", 0)),
            last_exposed_at=int(props.get("lastExposedAt", 0)),
        )
        is_rusty = skill_is_rusty(fold_snapshot, now_ms)

        skill_views.append(
            SkillNodeView(
                skill_id=UUID(sk["id"]),
                topic_id=topic_id,
                name=str(props.get("name", "")),
                description=str(props.get("description", "")),
                status=str(props.get("status", "active")),
                origin=str(props.get("origin", "structure")),
                p_mastery=p_m,
                effective_mastery=eff_m,
                confidence=float(props.get("confidence", 0.0)),
                mastery_status=m_stat,
                is_rusty=is_rusty,
                evidence_count=int(props.get("evidenceCount", 0)),
                distinct_families=int(props.get("distinctFamilies", 0)),
                stability=stab,
                last_practiced_at=int(props.get("lastPracticedAt", 0)),
                last_reviewed_at=l_rev,
                next_review_at=n_rev,
                prior_mastery=float(props.get("priorMastery", 0.0)),
                prior_confidence=float(props.get("priorConfidence", 0.0)),
                prior_source=str(props.get("priorSource", "none")),
            )
        )
        skill_snaps.append(_skill_snapshot(sk["id"], props, now_ms))
        raw_p_mastery[sk["id"]] = p_m

    prereq_skills_by_topic: dict[str, list[SkillSnapshot]] = {}
    prereq_dep_counts: dict[str, int] = {}
    for psk in prereq_skills_raw:
        prereq_skills_by_topic.setdefault(psk["topic_id"], []).append(
            _skill_snapshot(psk["id"], psk["props"] or {}, now_ms)
        )
        prereq_dep_counts[psk["id"]] = int(psk["cnt"])
    prereq_snaps = [
        PrereqSnapshot(
            topic_id=p["id"],
            status=str(p["props"].get("status", "unknown")),
            mastery=recompute_topic_display(
                prereq_skills_by_topic.get(p["id"], []),
                prereq_dep_counts,
                [],
                as_of_ms=now_ms,
                params=settings.fold,
            ).mastery,
        )
        for p in prereqs_raw
    ]
    disp = _recompute_display(
        skill_snaps, raw_p_mastery, dep_counts, prereq_snaps, now_ms, settings
    )
    t_props = topic_raw["props"] or {}
    topic_view = TopicNodeView(
        topic_id=topic_id,
        domain_id=domain_id,
        name=str(t_props.get("name", "")),
        description=str(t_props.get("description", "")),
        status=str(t_props.get("status", "active")),
        mastery=disp.mastery,
        coverage=disp.coverage,
        confidence=disp.confidence,
        hub_score=disp.hub_score,
        nearest_review_at=disp.nearest_review_at,
        display_state=disp.display_state,
        skill_count=disp.skill_count,
    )

    skill_deps = [
        SkillDepEdge(skill_id=UUID(sd["sid"]), depends_on_skill_id=UUID(sd["did"]))
        for sd in skill_deps_raw
    ]

    misconceptions = [
        MisconceptionNodeView(
            misconception_id=UUID(m["id"]),
            skill_id=UUID(m["skill_id"]),
            name=str(m["props"].get("name", "")),
            description=str(m["props"].get("description", "")),
            status=str(m["props"].get("status", "latent")),
            occurrences=int(m["props"].get("occurrences", 0)),
            last_seen_at=int(m["props"].get("lastSeenAt", 0)),
            resist_count=int(m["props"].get("resistCount", 0)),
        )
        for m in misc_raw
    ]

    artifacts = [
        ArtifactRefView(
            artifact_id=UUID(a["id"]),
            kind=str(a.get("kind", "")),
            title=str(a.get("title", "")),
            version=int(a.get("version", 1)),
        )
        for a in artifacts_raw
    ]

    return TopicDetailResponse(
        topic=topic_view,
        skills=skill_views,
        skill_deps=skill_deps,
        misconceptions=misconceptions,
        artifacts=artifacts,
    )
