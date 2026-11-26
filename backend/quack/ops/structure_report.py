"""CLI `ops structure-report`: subject structure, skill outcomes, edges, hubs, and LLM cost."""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from neo4j import AsyncDriver

from quack.config import Settings
from quack.db import Db
from quack.db.neo4j import cypher, read_session
from quack.graph.ids import deterministic_id
from quack.llm.usage import call_cost_usd

EVIDENCE_WINDOW_DAYS = 14
SOURCES: tuple[str, ...] = ("teacher", "generated", "seminar", "extractor")
EDGE_ORIGINS: tuple[str, ...] = ("model", "material", "learner")
HUB_KINDS: tuple[str, ...] = ("material", "request", "domain_all")

_TOPICS_QUERY = """
MATCH (t:Topic)
WHERE t.learnerId = $l AND t.domainId = $d
  AND coalesce(t.status, 'active') IN ['active', 'proposed']
OPTIONAL MATCH (t)-[:COVERS]->(s:Skill)
WHERE s.learnerId = $l
  AND coalesce(s.status, 'active') IN ['provisional', 'active']
RETURN t.id AS topic_id, t.name AS topic_name, coalesce(t.status, 'active') AS status,
       collect(DISTINCT CASE
           WHEN s IS NULL THEN null
           ELSE {id: s.id, core: coalesce(s.core, false), status: coalesce(s.status, 'active')}
       END) AS skills
ORDER BY t.name, t.id
"""

_UNASSIGNED_QUERY = """
MATCH (s:Skill)
WHERE s.learnerId = $l AND s.domainId = $d
  AND coalesce(s.status, 'active') IN ['provisional', 'active']
  AND NOT (:Topic)-[:COVERS]->(s)
RETURN s.id AS skill_id
"""

_REQUIRES_QUERY = """
MATCH (a:Skill)-[r:REQUIRES]->(b:Skill)
WHERE a.learnerId = $l AND b.learnerId = $l
  AND a.domainId = $d AND b.domainId = $d
RETURN r.origin AS origin, r.origins AS origins, coalesce(r.confidence, 1.0) AS confidence
"""

_HUBS_QUERY = """
MATCH (d:Domain {id: $d})-[:HAS_HUB]->(h:Hub)
WHERE d.learnerId = $l AND h.learnerId = $l
  AND coalesce(h.status, 'active') = 'active'
RETURN coalesce(h.kind, 'unknown') AS kind, count(*) AS n, collect(h.id) AS ids
"""

_EVENTS_QUERY = """
SELECT seq, type, occurred_at, idempotency_key, payload
FROM core.events
WHERE learner_id = %(learner_id)s
  AND (
    domain_id = %(domain_id)s
    OR (
      domain_id IS NULL
      AND type IN (
        'skill.merged', 'skill.deleted', 'skill.placed',
        'verdict.recorded', 'answer.submitted', 'observation.diagnosed', 'turn.committed'
      )
    )
  )
ORDER BY seq
"""

_USAGE_QUERY = """
SELECT role, model,
       COUNT(*)::bigint AS calls,
       COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
       COALESCE(SUM(cached_input_tokens), 0)::bigint AS cached_input_tokens,
       COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
       COALESCE(SUM(reasoning_tokens), 0)::bigint AS reasoning_tokens,
       COALESCE(SUM(cost_usd), 0)::float8 AS recorded_cost_usd
FROM ops.llm_usage
WHERE learner_id = %(learner_id)s
  AND created_at >= %(since)s
GROUP BY role, model
ORDER BY role, model
"""


@dataclass(frozen=True)
class TopicSkillRow:
    skill_id: str
    core: bool = False
    status: str = "active"


@dataclass(frozen=True)
class TopicGraphRow:
    topic_id: str
    name: str
    status: str = "active"
    skills: tuple[TopicSkillRow, ...] = ()


@dataclass(frozen=True)
class RequiresEdgeRow:
    origin: str | None = None
    origins: tuple[str, ...] = ()
    confidence: float = 1.0


@dataclass(frozen=True)
class StructureEventRow:
    seq: int
    type: str
    occurred_at: datetime
    idempotency_key: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UsageSummaryRow:
    role: str
    model: str
    calls: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    recorded_cost_usd: float = 0.0


@dataclass(frozen=True)
class GraphSnapshot:
    topics: tuple[TopicGraphRow, ...] = ()
    unassigned_skill_ids: frozenset[str] = frozenset()
    requires_edges: tuple[RequiresEdgeRow, ...] = ()
    hubs_by_kind: Mapping[str, int] = field(default_factory=dict)
    material_hub_ids: frozenset[str] = frozenset()


@dataclass
class _NewSkillBirth:
    skill_id: str
    name: str
    source: str
    born_at: datetime
    outcome: str | None = None
    empty_birth: bool = False


def _norm(name: str) -> str:
    return " ".join(name.casefold().split())


def _all_task_tests(markup: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    tests: list[tuple[str, Mapping[str, Any]]] = [
        ("main", t) for t in (markup.get("tests") or ()) if isinstance(t, Mapping)
    ]
    for part in markup.get("parts") or ():
        if not isinstance(part, Mapping):
            continue
        pname = str(part.get("part") or "main")
        for t in part.get("tests") or ():
            if isinstance(t, Mapping):
                tests.append((pname, t))
    return tests


def _task_skill_ids(
    learner_id: UUID, seq: int, markup: Mapping[str, Any]
) -> tuple[dict[str, set[str]], list[tuple[str, str]]]:
    """Return `(by_part -> set[skill_id], [(skill_id, name)])` for one `task.created`."""
    by_part: dict[str, set[str]] = {}
    new_skills: list[tuple[str, str]] = []
    for part, test in _all_task_tests(markup):
        if test.get("skill_id") is not None:
            sid = str(test["skill_id"])
            by_part.setdefault(part, set()).add(sid)
        elif isinstance(test.get("new_skill"), Mapping):
            name = str(test["new_skill"].get("name") or "").strip()
            if name:
                sid = deterministic_id(learner_id, seq, f"skill:{_norm(name)}")
                by_part.setdefault(part, set()).add(sid)
                new_skills.append((sid, name))
    return by_part, new_skills


def _row_cost_usd(settings: Settings, row: UsageSummaryRow) -> float:
    if row.model in settings.llm_prices:
        return call_cost_usd(
            settings,
            row.model,
            input_tokens=row.input_tokens,
            cached_tokens=row.cached_input_tokens,
            output_tokens=row.output_tokens,
        )
    return float(row.recorded_cost_usd)


def compute_structure_report(
    *,
    learner_id: UUID,
    domain_id: UUID,
    graph: GraphSnapshot,
    events: Sequence[StructureEventRow],
    usage_rows: Sequence[UsageSummaryRow],
    settings: Settings,
    days: int = 14,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute the full structure report purely from graph, event log, and usage summaries."""
    ref_now = now or datetime.now(UTC)
    since = ref_now - timedelta(days=days)

    # 1. Topics & skills per topic
    skill_counts = sorted(len(t.skills) for t in graph.topics)
    empty_topics = sum(1 for t in graph.topics if len(t.skills) == 0)
    missing_core_topics = sum(1 for t in graph.topics if sum(1 for s in t.skills if s.core) == 0)
    histogram = {str(k): v for k, v in sorted(Counter(skill_counts).items())}
    distribution: dict[str, Any] = {
        "min": skill_counts[0] if skill_counts else 0,
        "median": float(statistics.median(skill_counts)) if skill_counts else 0.0,
        "avg": sum(skill_counts) / len(skill_counts) if skill_counts else 0.0,
        "max": skill_counts[-1] if skill_counts else 0,
        "histogram": histogram,
    }
    topic_skill_ids = {s.skill_id for t in graph.topics for s in t.skills}

    # 2. New skills, outcomes, empty births, and 14-day evidence accumulation
    births: dict[str, _NewSkillBirth] = {}
    task_skills: dict[str, dict[str, set[str]]] = {}
    answer_tasks: dict[int, str] = {}
    evidence_times: dict[str, list[datetime]] = {}
    exposure_counts: Counter[str] = Counter()
    merged_to: dict[str, tuple[str, bool]] = {}
    deleted_skills: dict[str, bool] = {}
    placed_skills: set[str] = set()
    tasks_in_window = 0
    tasks_with_new_skill = 0
    new_skill_reasons: Counter[str] = Counter()
    syllabus_hub_ids: set[str] = set()

    for ev in sorted(events, key=lambda e: e.seq):
        p = ev.payload
        if ev.type == "task.created":
            tid = str(p.get("task_id") or "")
            markup = p.get("markup")
            if isinstance(markup, Mapping):
                by_part, new_skills = _task_skill_ids(learner_id, ev.seq, markup)
                if tid:
                    task_skills[tid] = by_part
                source = str(p.get("source") or "")
                if ev.occurred_at >= since:
                    tasks_in_window += 1
                    reasons = [
                        str(t["new_skill"].get("reason") or "none")
                        for _, t in _all_task_tests(markup)
                        if isinstance(t.get("new_skill"), Mapping)
                    ]
                    if reasons:
                        tasks_with_new_skill += 1
                        new_skill_reasons.update(reasons)
                if source in ("teacher", "generated", "seminar") and ev.occurred_at >= since:
                    for sid, name in new_skills:
                        births.setdefault(
                            sid,
                            _NewSkillBirth(
                                skill_id=sid, name=name, source=source, born_at=ev.occurred_at
                            ),
                        )
        elif ev.type == "answer.submitted":
            tid = str(p.get("task_id") or "")
            if tid:
                answer_tasks[ev.seq] = tid
                if p.get("source") == "card" and p.get("self_assessment") is not None:
                    part = str(p.get("part") or "main")
                    for sid in task_skills.get(tid, {}).get(part, ()):
                        evidence_times.setdefault(sid, []).append(ev.occurred_at)
        elif ev.type == "verdict.recorded":
            if p.get("outcome") == "unclear":
                continue
            ans_seq = p.get("answer_seq")
            if isinstance(ans_seq, int) and ans_seq in answer_tasks:
                tid = answer_tasks[ans_seq]
                part = str(p.get("part") or "main")
                parts_map = task_skills.get(tid, {})
                targets = parts_map.get(part) or {
                    sid for sids in parts_map.values() for sid in sids
                }
                for sid in targets:
                    evidence_times.setdefault(sid, []).append(ev.occurred_at)
        elif ev.type == "observation.extracted":
            for el in p.get("elements") or ():
                if not isinstance(el, Mapping):
                    continue
                sid: str | None = None
                if el.get("skill_id") is not None:
                    sid = str(el["skill_id"])
                elif isinstance(el.get("new_skill"), Mapping):
                    name = str(el["new_skill"].get("name") or "").strip()
                    if name:
                        sid = deterministic_id(learner_id, ev.seq, f"skill:{_norm(name)}")
                        if ev.occurred_at >= since:
                            births.setdefault(
                                sid,
                                _NewSkillBirth(
                                    skill_id=sid,
                                    name=name,
                                    source="extractor",
                                    born_at=ev.occurred_at,
                                ),
                            )
                if sid is not None and el.get("kind") == "evidence":
                    evidence_times.setdefault(sid, []).append(ev.occurred_at)
        elif ev.type == "turn.committed":
            markup = p.get("markup")
            if isinstance(markup, Mapping):
                for exp in markup.get("exposures") or ():
                    if isinstance(exp, Mapping) and exp.get("skill_id") is not None:
                        exposure_counts[str(exp["skill_id"])] += 1
        elif ev.type == "skill.merged":
            from_id = str(p.get("from_id") or "")
            into_id = str(p.get("into_id") or "")
            if from_id:
                is_empty = ev.idempotency_key.startswith("hygiene:empty_birth:")
                merged_to[from_id] = (into_id, is_empty)
        elif ev.type == "skill.deleted":
            sid = str(p.get("skill_id") or "")
            if sid:
                is_empty = (
                    ev.idempotency_key.startswith("hygiene:empty_birth:")
                    or p.get("reason") == "empty_session_birth"
                )
                deleted_skills[sid] = is_empty
        elif ev.type == "skill.placed":
            sid = str(p.get("skill_id") or "")
            if sid:
                placed_skills.add(sid)
        elif (ev.type == "hub.created" and ":syllabus:" in ev.idempotency_key) or (
            ev.type == "hub.deadline_set" and p.get("source") == "syllabus"
        ):
            syllabus_hub_ids.add(str(p.get("hub_id") or ""))

    by_source: dict[str, dict[str, Any]] = {
        src: {
            "total": 0,
            "outcomes": {"merged": 0, "kept": 0, "moved": 0, "deleted": 0, "unassigned": 0},
            "empty_births": 0,
        }
        for src in SOURCES
    }
    reached_3_evidence = 0
    window = timedelta(days=EVIDENCE_WINDOW_DAYS)

    for sid, birth in births.items():
        if sid in merged_to:
            outcome = "merged"
            is_empty = merged_to[sid][1]
        elif sid in deleted_skills:
            outcome = "deleted"
            is_empty = deleted_skills[sid]
        elif sid in placed_skills:
            outcome = "moved"
            is_empty = False
        elif sid in topic_skill_ids:
            outcome = "kept"
            is_empty = False
        elif sid in graph.unassigned_skill_ids:
            outcome = "unassigned"
            is_empty = False
        else:
            outcome = "unassigned"
            is_empty = False

        if is_empty and not evidence_times.get(sid) and exposure_counts.get(sid, 0) == 0:
            birth.empty_birth = True

        birth.outcome = outcome
        bucket = by_source[birth.source]
        bucket["total"] += 1
        bucket["outcomes"][outcome] += 1
        if birth.empty_birth:
            bucket["empty_births"] += 1

        ev_count = sum(
            1 for ts in evidence_times.get(sid, ()) if birth.born_at <= ts <= birth.born_at + window
        )
        if ev_count >= 3:
            reached_3_evidence += 1

    total_new = len(births)
    # The same skill born again under one name: the writer did not find the first birth.
    names = Counter(_norm(b.name) for b in births.values())
    repeated = {n: c for n, c in names.items() if c > 1}
    repeat_summary: dict[str, Any] = {
        "names": len(repeated),
        "extra_births": sum(c - 1 for c in repeated.values()),
        "top": dict(sorted(repeated.items(), key=lambda kv: (-kv[1], kv[0]))[:10]),
    }
    task_new_skill_summary: dict[str, Any] = {
        "tasks": tasks_in_window,
        "with_new_skill": tasks_with_new_skill,
        "share": (tasks_with_new_skill / tasks_in_window) if tasks_in_window else None,
        "reasons": dict(sorted(new_skill_reasons.items())),
    }
    evidence_summary: dict[str, Any] = {
        "window_days": EVIDENCE_WINDOW_DAYS,
        "total_new_skills": total_new,
        "with_at_least_3_evidence": reached_3_evidence,
        "share": (reached_3_evidence / total_new) if total_new else None,
    }

    # 3. REQUIRES edges
    edges_by_origin: dict[str, int] = dict.fromkeys(EDGE_ORIGINS, 0)
    conf_counts: Counter[str] = Counter()
    confidences: list[float] = []
    for edge in graph.requires_edges:
        if edge.origins:
            origins = [o for o in edge.origins if o]
        elif edge.origin:
            origins = [edge.origin]
        else:
            origins = []
        for orig in origins:
            edges_by_origin[orig] = edges_by_origin.get(orig, 0) + 1
        conf = float(edge.confidence)
        confidences.append(conf)
        conf_counts[f"{conf:.2f}"] += 1

    requires_summary: dict[str, Any] = {
        "total": len(graph.requires_edges),
        "by_origin": edges_by_origin,
        "confidence_distribution": dict(sorted(conf_counts.items())),
        "confidence_min": min(confidences) if confidences else None,
        "confidence_avg": (sum(confidences) / len(confidences)) if confidences else None,
        "confidence_max": max(confidences) if confidences else None,
    }

    # 4. Hubs by kind
    hubs: dict[str, int] = dict.fromkeys(HUB_KINDS, 0)
    for kind, count in graph.hubs_by_kind.items():
        hubs[kind] = hubs.get(kind, 0) + int(count)
    # A syllabus hub is `kind: material` too (a control of the course); told apart by its events.
    syllabus_hubs = len(graph.material_hub_ids & syllabus_hub_ids)
    material_split = {
        "syllabus": syllabus_hubs,
        "other": len(graph.material_hub_ids) - syllabus_hubs,
    }

    # 5. Cost by LLM role
    by_role: dict[str, dict[str, Any]] = {}
    for row in usage_rows:
        entry = by_role.setdefault(
            row.role,
            {
                "calls": 0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
            },
        )
        entry["calls"] += int(row.calls)
        entry["input_tokens"] += int(row.input_tokens)
        entry["cached_input_tokens"] += int(row.cached_input_tokens)
        entry["output_tokens"] += int(row.output_tokens)
        entry["reasoning_tokens"] += int(row.reasoning_tokens)
        entry["total_tokens"] += int(row.input_tokens) + int(row.output_tokens)
        entry["cost_usd"] += _row_cost_usd(settings, row)

    by_role = dict(sorted(by_role.items()))
    total_cost = sum(v["cost_usd"] for v in by_role.values())
    total_tokens = sum(v["total_tokens"] for v in by_role.values())

    return {
        "learner_id": str(learner_id),
        "domain_id": str(domain_id),
        "window_days": days,
        "since": since.astimezone(UTC).isoformat(),
        "generated_at": ref_now.astimezone(UTC).isoformat(),
        "topics": {
            "total": len(graph.topics),
            "empty": empty_topics,
            "missing_core": missing_core_topics,
        },
        "skills_per_topic": distribution,
        "new_skills": {
            "total": total_new,
            "by_source": by_source,
            "empty_births_total": sum(v["empty_births"] for v in by_source.values()),
            "repeat_births": repeat_summary,
            "evidence_3_within_14d": evidence_summary,
        },
        "tasks_new_skill": task_new_skill_summary,
        "requires_edges": requires_summary,
        "hubs_by_kind": hubs,
        "material_hubs": material_split,
        "llm_cost": {
            "scope": "learner_window",
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost,
            "by_role": by_role,
        },
    }


async def read_graph_snapshot(
    neo4j: AsyncDriver, *, learner_id: UUID, domain_id: UUID
) -> GraphSnapshot:
    l_id, d_id = str(learner_id), str(domain_id)
    async with read_session(neo4j) as session:
        t_res = await session.run(cypher(_TOPICS_QUERY), l=l_id, d=d_id)
        topics = tuple(
            [
                TopicGraphRow(
                    topic_id=str(r["topic_id"]),
                    name=str(r["topic_name"] or ""),
                    status=str(r["status"] or "active"),
                    skills=tuple(
                        TopicSkillRow(
                            skill_id=str(s["id"]),
                            core=bool(s.get("core")),
                            status=str(s.get("status") or "active"),
                        )
                        for s in (r["skills"] or ())
                        if isinstance(s, Mapping) and s.get("id") is not None
                    ),
                )
                async for r in t_res
            ]
        )
        u_res = await session.run(cypher(_UNASSIGNED_QUERY), l=l_id, d=d_id)
        unassigned = frozenset([str(r["skill_id"]) async for r in u_res if r["skill_id"]])

        e_res = await session.run(cypher(_REQUIRES_QUERY), l=l_id, d=d_id)
        edges = tuple(
            [
                RequiresEdgeRow(
                    origin=str(r["origin"]) if r["origin"] is not None else None,
                    origins=tuple(str(o) for o in (r["origins"] or ()) if o),
                    confidence=float(r["confidence"]) if r["confidence"] is not None else 1.0,
                )
                async for r in e_res
            ]
        )

        h_res = await session.run(cypher(_HUBS_QUERY), l=l_id, d=d_id)
        hubs: dict[str, int] = {}
        material_hubs: set[str] = set()
        async for r in h_res:
            hubs[str(r["kind"])] = int(r["n"])
            if r["kind"] == "material":
                material_hubs |= {str(i) for i in (r["ids"] or ()) if i}
    return GraphSnapshot(
        topics=topics,
        unassigned_skill_ids=unassigned,
        requires_edges=edges,
        hubs_by_kind=hubs,
        material_hub_ids=frozenset(material_hubs),
    )


async def build_structure_report(
    db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    *,
    learner_id: UUID,
    domain_id: UUID,
    days: int = 14,
    now: datetime | None = None,
) -> dict[str, Any]:
    ref_now = now or datetime.now(UTC)
    since = ref_now - timedelta(days=days)
    graph = await read_graph_snapshot(neo4j, learner_id=learner_id, domain_id=domain_id)
    ev_rows = await db.fetch_all(_EVENTS_QUERY, {"learner_id": learner_id, "domain_id": domain_id})
    events = [
        StructureEventRow(
            seq=int(r["seq"]),
            type=str(r["type"]),
            occurred_at=r["occurred_at"],
            idempotency_key=str(r["idempotency_key"]),
            payload=dict(r["payload"] or {}),
        )
        for r in ev_rows
    ]
    u_rows = await db.fetch_all(_USAGE_QUERY, {"learner_id": learner_id, "since": since})
    usage = [
        UsageSummaryRow(
            role=str(r["role"]),
            model=str(r["model"]),
            calls=int(r["calls"]),
            input_tokens=int(r["input_tokens"]),
            cached_input_tokens=int(r["cached_input_tokens"]),
            output_tokens=int(r["output_tokens"]),
            reasoning_tokens=int(r["reasoning_tokens"]),
            recorded_cost_usd=float(r["recorded_cost_usd"]),
        )
        for r in u_rows
    ]
    return compute_structure_report(
        learner_id=learner_id,
        domain_id=domain_id,
        graph=graph,
        events=events,
        usage_rows=usage,
        settings=settings,
        days=days,
        now=ref_now,
    )


def format_structure_report(report: Mapping[str, Any]) -> str:
    """Format the structure report as readable plain text."""
    topics = report["topics"]
    dist = report["skills_per_topic"]
    new_skills = report["new_skills"]
    ev14 = new_skills["evidence_3_within_14d"]
    share_str = f"{ev14['share']:.1%}" if ev14["share"] is not None else "n/a"
    req = report["requires_edges"]
    hubs = report["hubs_by_kind"]
    material_hubs = report.get("material_hubs", {})
    repeats = new_skills.get("repeat_births", {})
    task_ns = report.get("tasks_new_skill", {})
    task_share = f"{task_ns['share']:.1%}" if task_ns.get("share") is not None else "n/a"
    cost = report["llm_cost"]

    lines = [
        (
            f"Structure report for learner={report['learner_id']} "
            f"domain={report['domain_id']} (last {report['window_days']}d)"
        ),
        "",
        "Topics:",
        f"  total={topics['total']} empty={topics['empty']} missing_core={topics['missing_core']}",
        (
            f"  skills_per_topic: min={dist['min']} median={dist['median']:.2f} "
            f"avg={dist['avg']:.2f} max={dist['max']} histogram={dist['histogram']}"
        ),
        "",
        (
            f"New skills (total={new_skills['total']}, "
            f"empty_births={new_skills['empty_births_total']}):"
        ),
    ]
    for src, stats in new_skills["by_source"].items():
        o = stats["outcomes"]
        lines.append(
            f"  {src}: total={stats['total']} merged={o['merged']} kept={o['kept']} "
            f"moved={o['moved']} deleted={o['deleted']} unassigned={o['unassigned']} "
            f"empty_births={stats['empty_births']}"
        )
    lines.extend(
        [
            (
                f"  evidence>=3 within 14d: {ev14['with_at_least_3_evidence']}/"
                f"{ev14['total_new_skills']} ({share_str})"
            ),
            (
                f"  repeat births: names={repeats.get('names', 0)} "
                f"extra={repeats.get('extra_births', 0)} top={repeats.get('top', {})}"
            ),
            (
                f"  tasks with new_skill: {task_ns.get('with_new_skill', 0)}/"
                f"{task_ns.get('tasks', 0)} ({task_share}) reasons={task_ns.get('reasons', {})}"
            ),
            "",
            f"REQUIRES edges (total={req['total']}):",
            (
                f"  by_origin: model={req['by_origin'].get('model', 0)} "
                f"material={req['by_origin'].get('material', 0)} "
                f"learner={req['by_origin'].get('learner', 0)}"
            ),
            f"  confidence_distribution: {req['confidence_distribution']}",
            "",
            "Hubs by kind:",
            "  " + " ".join(f"{k}={v}" for k, v in hubs.items()),
            (
                f"  material: syllabus={material_hubs.get('syllabus', 0)} "
                f"other={material_hubs.get('other', 0)}"
            ),
            "",
            (
                f"LLM cost (scope={cost['scope']}, total_tokens={cost['total_tokens']}, "
                f"total_usd=${cost['total_cost_usd']:.4f}):"
            ),
        ]
    )
    if not cost["by_role"]:
        lines.append("  (none)")
    else:
        for role, stats in cost["by_role"].items():
            lines.append(
                f"  {role}: calls={stats['calls']} tokens={stats['total_tokens']} "
                f"(in={stats['input_tokens']}, cached={stats['cached_input_tokens']}, "
                f"out={stats['output_tokens']}) cost=${stats['cost_usd']:.4f}"
            )
    return "\n".join(lines)
