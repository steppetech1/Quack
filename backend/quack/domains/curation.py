"""Background graph curation worker `graph.curate`: active-session presence deferral,
deterministic provisional skill promotion and normName deduplication, and `curator@1`
model-assisted merges (ТЗ §2.5, §4; spec §7; Т-§10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

import neo4j
import orjson
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text

from quack.config import Settings
from quack.db.neo4j import create_driver
from quack.db.redis import create_redis, presence_key
from quack.domains.schemas import CuratorOutput
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.graph.phase_a import normalize_name
from quack.graph.slice import SliceDeps, read_consistent_slice
from quack.jobs.queue import ClaimedJob
from quack.jobs.runner import JobContext, register_job_handler
from quack.llm.gateway import LLMGateway
from quack.llm.provider import HttpLLMProvider, LLMProvider, ProviderRequest, ProviderResponse
from quack.logging import get_logger

log = get_logger(__name__)


# Module-level hook allowing tests to inject a FakeLLMProvider when running via JobRunner.
llm_provider_override: LLMProvider | None = None


def _has_cycle(edges: set[tuple[str, str]]) -> bool:
    adj: dict[str, list[str]] = {}
    nodes: set[str] = set()
    for u, v in edges:
        adj.setdefault(u, []).append(v)
        nodes.add(u)
        nodes.add(v)
    visited: dict[str, int] = {}

    def dfs(node: str) -> bool:
        visited[node] = 1
        for nxt in adj.get(node, []):
            if visited.get(nxt, 0) == 1:
                return True
            if visited.get(nxt, 0) == 0 and dfs(nxt):
                return True
        visited[node] = 2
        return False

    return any(visited.get(node, 0) == 0 and dfs(node) for node in nodes)


@dataclass(frozen=True)
class _DomainGraph:
    domain_name: str
    topics: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    misconceptions: list[dict[str, Any]]
    prereq_edges: list[dict[str, Any]]


def _read_session(driver: AsyncDriver) -> Any:
    try:
        return driver.session(default_access_mode=neo4j.READ_ACCESS)
    except TypeError:  # minimal driver doubles without the `default_access_mode` keyword
        return driver.session()


async def _load_domain_graph(
    driver: AsyncDriver, learner_id: UUID, domain_id: UUID
) -> _DomainGraph:
    params = {"d": str(domain_id), "l": str(learner_id)}
    async with _read_session(driver) as s:
        d_res = await s.run(
            "MATCH (d:Domain {id: $d, learnerId: $l}) RETURN d.name AS name", params
        )
        d_rec = await d_res.single()
        domain_name = str(d_rec["name"]) if d_rec else "Domain"

        t_res = await s.run(
            "MATCH (t:Topic {domainId: $d, learnerId: $l}) "
            "WHERE t.status <> 'merged' "
            "RETURN t.id AS id, properties(t) AS props",
            params,
        )
        topics = [rec.data() async for rec in t_res]

        sk_res = await s.run(
            "MATCH (sk:Skill {domainId: $d, learnerId: $l}) "
            "WHERE sk.status <> 'merged' "
            "RETURN sk.id AS id, properties(sk) AS props",
            params,
        )
        skills = [rec.data() async for rec in sk_res]

        m_res = await s.run(
            "MATCH (m:Misconception {domainId: $d, learnerId: $l}) "
            "WHERE m.status <> 'merged' "
            "RETURN m.id AS id, properties(m) AS props",
            params,
        )
        misconceptions = [rec.data() async for rec in m_res]

        p_res = await s.run(
            "MATCH (a:Topic)-[:PREREQ_OF]->(b:Topic) "
            "WHERE a.domainId = $d AND b.domainId = $d AND a.learnerId = $l "
            "AND a.status <> 'merged' AND b.status <> 'merged' "
            "RETURN a.id AS from_id, b.id AS to_id",
            params,
        )
        prereq_edges = [rec.data() async for rec in p_res]
    return _DomainGraph(domain_name, topics, skills, misconceptions, prereq_edges)


async def _read_domain_graph(
    driver: AsyncDriver,
    redis: Redis | None,
    ctx: JobContext,
    learner_id: UUID,
    domain_id: UUID,
) -> _DomainGraph:
    """Read the domain graph through `read_consistent_slice` (spec §8.2): `min_seq` is the
    learner's last event seq, waiting at most `projector_lag_timeout_ms`; direct read without Redis.
    """

    async def _loader() -> _DomainGraph:
        return await _load_domain_graph(driver, learner_id, domain_id)

    if redis is None:
        return await _loader()

    async def _applied_seq(learner: UUID) -> int:
        async with _read_session(driver) as s:
            res = await s.run(
                "MATCH (l:Learner {id: $id}) RETURN l.appliedSeq AS seq", {"id": str(learner)}
            )
            rec = await res.single()
        seq = rec["seq"] if rec else None
        return seq if isinstance(seq, int) else 0

    deps = SliceDeps(
        session_factory=ctx.session_factory,
        redis=redis,
        applied_seq_reader=_applied_seq,
        snapshot_read_retries=ctx.settings.snapshot_read_retries,
    )
    result = await read_consistent_slice(
        learner_id,
        None,
        _loader,
        ctx.settings.projector_lag_timeout_ms,
        deps=deps,
    )
    return result.data


async def curate_domain_graph(
    job: ClaimedJob,
    ctx: JobContext,
    *,
    driver: AsyncDriver | None = None,
    redis: Redis | None = None,
    llm: LLMGateway | None = None,
) -> None:
    """Execute `graph.curate` for a domain: defer by `curation_defer_min` if `presence:learner:{id}`
    is active in Redis; otherwise run deterministic promotion/deduplication and `curator@1`
    merges, emitting `skill.promoted`, `skill.merged`, `topic.merged`, `misconception.merged`
    (ТЗ §2.5, §4; Т-§10).
    """
    learner_id = job.learner_id
    if learner_id is None:
        return

    domain_id_raw = job.payload.get("domain_id") or job.payload.get("domainId")
    if not domain_id_raw:
        return
    domain_id = UUID(str(domain_id_raw))

    own_redis = False
    if redis is None:
        redis = create_redis(ctx.settings)
        own_redis = True

    own_driver = False
    if driver is None:
        driver = create_driver(ctx.settings)
        own_driver = True

    try:
        is_active = bool(await redis.exists(presence_key(learner_id)))
        if is_active:
            run_after = datetime.now(UTC) + timedelta(minutes=ctx.settings.curation_defer_min)
            async with ctx.session_factory() as session, session.begin():
                if job.coalesce_key is not None:
                    existing_queued = await session.execute(
                        text(
                            "UPDATE ops.jobs SET run_after = GREATEST(run_after, :run_after), "
                            "updated_at = now() "
                            "WHERE coalesce_key = :ck AND status = 'queued' AND id <> :id "
                            "RETURNING id"
                        ),
                        {"ck": job.coalesce_key, "run_after": run_after, "id": job.id},
                    )
                    if existing_queued.first() is not None:
                        return
                await session.execute(
                    text(
                        "UPDATE ops.jobs SET status = 'queued', locked_by = NULL, "
                        "heartbeat_at = NULL, run_after = :run_after, updated_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": job.id, "run_after": run_after},
                )
            return

        graph = await _read_domain_graph(driver, redis, ctx, learner_id, domain_id)
        domain_name = graph.domain_name
        topics = graph.topics
        skills = graph.skills
        misconceptions = graph.misconceptions
        prereq_edges = graph.prereq_edges

        active_skills: dict[str, dict] = {
            s["id"]: s["props"] for s in skills if s["props"].get("status") == "active"
        }
        provisional_skills: dict[str, dict] = {
            s["id"]: s["props"] for s in skills if s["props"].get("status") == "provisional"
        }

        candidates_hash = sha256(
            orjson.dumps({"prov": sorted(provisional_skills), "act": sorted(active_skills)})
        ).hexdigest()[:16]

        handled_skill_ids: set[str] = set()
        events_to_append: list[NewEvent] = []

        active_by_norm: dict[str, str] = {}
        for aid, aprops in active_skills.items():
            norm = aprops.get("normName") or normalize_name(str(aprops.get("name", "")))
            if norm:
                active_by_norm[norm] = aid

        # 1. Deterministic deduplication by normName
        for pid, pprops in provisional_skills.items():
            pnorm = pprops.get("normName") or normalize_name(str(pprops.get("name", "")))
            if pnorm in active_by_norm:
                target_id = active_by_norm[pnorm]
                events_to_append.append(
                    NewEvent(
                        type="skill.merged",
                        actor="code",
                        domain_id=domain_id,
                        idempotency_key=f"curate:smerge:{pid}:{target_id}",
                        payload={
                            "domain_id": str(domain_id),
                            "source_skill_id": pid,
                            "target_skill_id": target_id,
                            "reason": f"Deterministic deduplication by normName '{pnorm}'",
                        },
                    )
                )
                handled_skill_ids.add(pid)

        # 2. Deterministic promotion for evidenceCount >= curation_min_evidence_for_promotion
        for pid, pprops in provisional_skills.items():
            if pid in handled_skill_ids:
                continue
            ev_count = int(pprops.get("evidenceCount", 0))
            if ev_count >= ctx.settings.curation_min_evidence_for_promotion:
                events_to_append.append(
                    NewEvent(
                        type="skill.promoted",
                        actor="code",
                        domain_id=domain_id,
                        idempotency_key=f"curate:spromote:{pid}",
                        payload={
                            "domain_id": str(domain_id),
                            "skill_id": pid,
                            "topic_id": str(pprops.get("topicId")),
                            "name": str(pprops.get("name", "Skill")),
                            "description": str(pprops.get("description", "")),
                        },
                    )
                )
                handled_skill_ids.add(pid)

        # 2b. Deterministic misconception deduplication by normName
        handled_misc_ids: set[str] = set()
        canonical_misc_by_norm: dict[str, str] = {}
        sorted_misconceptions = sorted(
            misconceptions,
            key=lambda m: (0 if m["props"].get("origin") == "structure" else 1, m["id"]),
        )
        for m in sorted_misconceptions:
            mid = m["id"]
            mprops = m["props"]
            mnorm = mprops.get("normName") or normalize_name(str(mprops.get("name", "")))
            if not mnorm:
                continue
            if mnorm not in canonical_misc_by_norm:
                canonical_misc_by_norm[mnorm] = mid
            else:
                target_mid = canonical_misc_by_norm[mnorm]
                events_to_append.append(
                    NewEvent(
                        type="misconception.merged",
                        actor="code",
                        domain_id=domain_id,
                        idempotency_key=f"curate:mmerge:{mid}:{target_mid}",
                        payload={
                            "domain_id": str(domain_id),
                            "source_misconception_id": mid,
                            "target_misconception_id": target_mid,
                            "reason": f"Deterministic deduplication by normName '{mnorm}'",
                        },
                    )
                )
                handled_misc_ids.add(mid)

        # 3. Model pass (curator@1)
        remaining_provisional = [pid for pid in provisional_skills if pid not in handled_skill_ids]
        if remaining_provisional:
            if llm is None:
                provider = llm_provider_override or HttpLLMProvider(
                    ctx.settings.llm_api_base_url,
                    ctx.settings.llm_api_key.get_secret_value(),
                )
                llm = LLMGateway(
                    session_factory=ctx.session_factory,
                    provider=provider,
                    settings=ctx.settings,
                )

            topics_json = orjson.dumps(
                [
                    {
                        "id": t["id"],
                        "name": str(t["props"].get("name", "")),
                        "description": str(t["props"].get("description", "")),
                    }
                    for t in topics
                ]
            ).decode()
            skills_json = orjson.dumps(
                [
                    {
                        "id": s["id"],
                        "name": str(s["props"].get("name", "")),
                        "topic_id": str(s["props"].get("topicId", "")),
                        "status": str(s["props"].get("status", "")),
                    }
                    for s in skills
                ]
            ).decode()
            misc_json = orjson.dumps(
                [
                    {
                        "id": m["id"],
                        "name": str(m["props"].get("name", "")),
                        "skill_id": str(m["props"].get("skillId", "")),
                    }
                    for m in misconceptions
                ]
            ).decode()

            res = await llm.generate_structured(
                role="curator",
                prompt_version=1,
                response_model=CuratorOutput,
                variables={
                    "domain_name": domain_name,
                    "topics_json": topics_json,
                    "skills_json": skills_json,
                    "misconceptions_json": misc_json,
                },
                learner_id=learner_id,
                session_id=None,
                turn_id=None,
                idempotency_key=f"curator:{domain_id}:{candidates_hash}",
            )
            cur_out = res.value

            all_skill_ids = {s["id"] for s in skills}
            all_topic_ids = {t["id"] for t in topics}
            all_misc_ids = {m["id"] for m in misconceptions}

            for prom in cur_out.promotions:
                sid = str(prom.skill_id)
                if sid in provisional_skills and sid not in handled_skill_ids:
                    events_to_append.append(
                        NewEvent(
                            type="skill.promoted",
                            actor="curator@1",
                            domain_id=domain_id,
                            idempotency_key=f"curate:spromote:{sid}",
                            payload={
                                "domain_id": str(domain_id),
                                "skill_id": sid,
                                "topic_id": str(prom.topic_id),
                                "name": prom.name,
                                "description": prom.description,
                            },
                        )
                    )
                    handled_skill_ids.add(sid)

            for sm in cur_out.skill_merges:
                sid = str(sm.source_id)
                tid = str(sm.target_id)
                if (
                    sid != tid
                    and sid in all_skill_ids
                    and tid in all_skill_ids
                    and sid not in handled_skill_ids
                ):
                    events_to_append.append(
                        NewEvent(
                            type="skill.merged",
                            actor="curator@1",
                            domain_id=domain_id,
                            idempotency_key=f"curate:smerge:{sid}:{tid}",
                            payload={
                                "domain_id": str(domain_id),
                                "source_skill_id": sid,
                                "target_skill_id": tid,
                                "reason": sm.reason,
                            },
                        )
                    )
                    handled_skill_ids.add(sid)

            current_edges = {(p["from_id"], p["to_id"]) for p in prereq_edges}
            for tm in cur_out.topic_merges:
                sid = str(tm.source_id)
                tid = str(tm.target_id)
                if sid != tid and sid in all_topic_ids and tid in all_topic_ids:
                    sim_edges: set[tuple[str, str]] = set()
                    for u, v in current_edges:
                        u_sim = tid if u == sid else u
                        v_sim = tid if v == sid else v
                        if u_sim != v_sim:
                            sim_edges.add((u_sim, v_sim))

                    if not _has_cycle(sim_edges):
                        events_to_append.append(
                            NewEvent(
                                type="topic.merged",
                                actor="curator@1",
                                domain_id=domain_id,
                                idempotency_key=f"curate:tmerge:{sid}:{tid}",
                                payload={
                                    "domain_id": str(domain_id),
                                    "source_topic_id": sid,
                                    "target_topic_id": tid,
                                    "reason": tm.reason,
                                },
                            )
                        )
                        current_edges = sim_edges
                    else:
                        log.warning(
                            "curator_topic_merge_rejected_due_to_cycle",
                            source_id=sid,
                            target_id=tid,
                        )

            for mm in cur_out.misconception_merges:
                sid = str(mm.source_id)
                tid = str(mm.target_id)
                if (
                    sid != tid
                    and sid in all_misc_ids
                    and tid in all_misc_ids
                    and sid not in handled_misc_ids
                ):
                    events_to_append.append(
                        NewEvent(
                            type="misconception.merged",
                            actor="curator@1",
                            domain_id=domain_id,
                            idempotency_key=f"curate:mmerge:{sid}:{tid}",
                            payload={
                                "domain_id": str(domain_id),
                                "source_misconception_id": sid,
                                "target_misconception_id": tid,
                                "reason": mm.reason,
                            },
                        )
                    )

        if events_to_append:
            async with ctx.session_factory() as session, session.begin():
                await append_events(
                    session,
                    learner_id,
                    events_to_append,
                    publish_wakeup=True,
                    redis=redis,
                )

    finally:
        if own_driver:
            await driver.close()
        if own_redis:
            await redis.aclose()


class _ProviderProxy:
    """Resolves the provider per call so that `llm_provider_override` set later is honoured by
    the cached `LLMGateway`.
    """

    def __init__(self, http: LLMProvider) -> None:
        self._http = http

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        provider = llm_provider_override or self._http
        return await provider.complete_structured(request)


@dataclass(frozen=True)
class _CuratorResources:
    settings: Settings  # strong reference keeps `id(settings)` unique for the cache lifetime
    driver: AsyncDriver
    redis: Redis
    llm: LLMGateway


# Long-lived clients shared by all `graph.curate` jobs of a worker, keyed by `id(ctx.settings)`.
_RESOURCES: dict[int, _CuratorResources] = {}


def _curator_resources(ctx: JobContext) -> _CuratorResources:
    key = id(ctx.settings)
    cached = _RESOURCES.get(key)
    if cached is None or cached.settings is not ctx.settings:
        cached = _CuratorResources(
            settings=ctx.settings,
            driver=create_driver(ctx.settings),
            redis=create_redis(ctx.settings),
            llm=LLMGateway(
                session_factory=ctx.session_factory,
                provider=_ProviderProxy(
                    HttpLLMProvider(
                        ctx.settings.llm_api_base_url,
                        ctx.settings.llm_api_key.get_secret_value(),
                    )
                ),
                settings=ctx.settings,
            ),
        )
        _RESOURCES[key] = cached
    return cached


@register_job_handler("graph.curate")
async def handle_graph_curate(job: ClaimedJob, ctx: JobContext) -> None:
    """Registered `ops.jobs` handler for `graph.curate` delegating to `curate_domain_graph`
    with the driver, Redis client and `LLMGateway` cached per settings object, not per job
    (ТЗ §2.5, spec §7, Т-§10).
    """
    res = _curator_resources(ctx)
    await curate_domain_graph(job, ctx, driver=res.driver, redis=res.redis, llm=res.llm)
