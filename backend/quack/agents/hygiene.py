"""Graph hygiene — the job `graph.hygiene` (ТЗ Ф4 WP16.3; Т-§4.10, Т-§22.10, Т-§9.3; `T-AG4`).
D-B-185.

- The subgraph of the domain is one Cypher read: the skills (`provisional`/`active`) with their
  vectors and neighbours (`REQUIRES`, the covering topics), the misconceptions of those skills.
- A duplicate candidate scores the cosine of the vectors plus `hygiene_neighbour_weight` × the
  Jaccard share of the neighbours; pairs from `hygiene_min_score` up, best first, as many as the
  skill labels allow. Misconceptions pair only within one skill, by the cosine.
- One `builder` call in the Hygiene mode checks the pairs. A duplicate pair with a temporary
  skill is `skill.merged` (the temporary one into the other); two active skills are
  `skill.merge_suggested` — the learner decides (Т-§4.4). Misconception duplicates are
  `misconception.merged`. The merge target of two equals is the smaller id.
- `networkx` is not a dependency yet: the neighbour sets are plain Python; Louvain hubs are
  later (Т-§22.10).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from neo4j import AsyncDriver

from quack.agents.schemas import HygieneOutput
from quack.config import Settings
from quack.db.neo4j import cypher, read_session
from quack.events.event_types import MisconceptionMerged, SkillMerged, SkillMergeSuggested
from quack.events.log import NewEvent, append_events
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.llm.labels import LabelMap, UnknownLabelError

BUILDER_ROLE = "builder"
HYGIENE_MODE = "Hygiene"
PROVISIONAL = "provisional"

_SKILLS = (
    "MATCH (s:Skill) WHERE s.learnerId = $l AND s.domainId = $d "
    "AND s.status IN ['provisional', 'active'] AND s.embedding IS NOT NULL "
    "OPTIONAL MATCH (s)-[:REQUIRES]-(n:Skill) WHERE n.learnerId = $l "
    "OPTIONAL MATCH (t:Topic)-[:COVERS]->(s) WHERE t.learnerId = $l "
    "RETURN s.id AS id, s.name AS name, s.status AS status, s.embedding AS embedding, "
    "collect(DISTINCT n.id) + collect(DISTINCT t.id) AS neighbours"
)
_MISCONCEPTIONS = (
    "MATCH (m:Misconception)-[:ABOUT]->(s:Skill) WHERE s.learnerId = $l AND s.domainId = $d "
    "AND m.embedding IS NOT NULL AND coalesce(m.status, '') <> 'merged' "
    "RETURN m.id AS id, m.statement AS statement, s.id AS skill_id, m.embedding AS embedding"
)


@dataclass(frozen=True)
class SkillNode:
    skill_id: UUID
    name: str
    status: str
    embedding: list[float]
    neighbours: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MisconceptionNode:
    misconception_id: UUID
    statement: str
    skill_id: UUID
    embedding: list[float]


@dataclass(frozen=True)
class DomainGraph:
    skills: list[SkillNode] = field(default_factory=list)
    misconceptions: list[MisconceptionNode] = field(default_factory=list)


async def read_domain_graph(
    driver: AsyncDriver, *, learner_id: UUID, domain_id: UUID
) -> DomainGraph:
    async with read_session(driver) as session:
        result = await session.run(cypher(_SKILLS), l=str(learner_id), d=str(domain_id))
        skills = [
            SkillNode(
                UUID(r["id"]),
                r["name"] or "",
                r["status"],
                list(r["embedding"]),
                frozenset(str(n) for n in r["neighbours"] if n),
            )
            async for r in result
        ]
        result = await session.run(cypher(_MISCONCEPTIONS), l=str(learner_id), d=str(domain_id))
        misconceptions = [
            MisconceptionNode(
                UUID(r["id"]), r["statement"] or "", UUID(r["skill_id"]), list(r["embedding"])
            )
            async for r in result
        ]
    return DomainGraph(skills, misconceptions)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=False)) / norm if norm else 0.0


def skill_pairs(
    skills: Sequence[SkillNode], settings: Settings
) -> list[tuple[SkillNode, SkillNode]]:
    scored: list[tuple[float, SkillNode, SkillNode]] = []
    for i, a in enumerate(skills):
        for b in skills[i + 1 :]:
            union = a.neighbours | b.neighbours
            shared = len(a.neighbours & b.neighbours) / len(union) if union else 0.0
            score = cosine(a.embedding, b.embedding) + settings.hygiene_neighbour_weight * shared
            if score >= settings.hygiene_min_score:
                scored.append((score, a, b))
    scored.sort(key=lambda s: -s[0])
    return [(a, b) for _, a, b in scored]


def misconception_pairs(
    misconceptions: Sequence[MisconceptionNode], settings: Settings
) -> list[tuple[MisconceptionNode, MisconceptionNode]]:
    scored = [
        (cosine(a.embedding, b.embedding), a, b)
        for i, a in enumerate(misconceptions)
        for b in misconceptions[i + 1 :]
        if a.skill_id == b.skill_id
    ]
    scored = [s for s in scored if s[0] >= settings.hygiene_min_score]
    scored.sort(key=lambda s: -s[0])
    return [(a, b) for _, a, b in scored]


def _ordered(first: UUID, second: UUID) -> tuple[UUID, UUID]:
    """`(from, into)` of two equals: into the smaller id."""
    return (second, first) if str(first) < str(second) else (first, second)


async def run_hygiene(
    ctx: JobContext, graph: DomainGraph, *, learner_id: UUID, domain_id: UUID
) -> list[str]:
    """The event types written, in order; nothing when there is no candidate."""
    settings = ctx.settings
    pairs = skill_pairs(graph.skills, settings)
    m_pairs = misconception_pairs(graph.misconceptions, settings)
    skill_ids = list(dict.fromkeys(s.skill_id for pair in pairs for s in pair))
    labels = LabelMap.build(
        settings,
        skills=skill_ids,
        misconceptions=[m.misconception_id for pair in m_pairs for m in pair],
    )
    pairs = [
        (a, b)
        for a, b in pairs
        if labels.label("skill", a.skill_id) and labels.label("skill", b.skill_id)
    ]
    if not pairs and not m_pairs:
        return []
    lines: list[str] = []
    for a, b in pairs:
        lines.append(f"{labels.label('skill', a.skill_id)} | {labels.label('skill', b.skill_id)}")
    for m, n in m_pairs:
        lines.append(
            f"{labels.label('misconception', m.misconception_id)} | "
            f"{labels.label('misconception', n.misconception_id)}"
        )
    candidates = [
        f"{labels.label('skill', s.skill_id)}: {s.name}"
        for s in graph.skills
        if labels.label("skill", s.skill_id)
    ] + [
        f"{labels.label('misconception', m.misconception_id)}: {m.statement}"
        for m in graph.misconceptions
        if labels.label("misconception", m.misconception_id)
    ]
    prompt = settings.llm_roles[BUILDER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'builder' has no prompt")
    result = await ctx.llm.generate_structured(
        BUILDER_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        HygieneOutput,
        {"mode": HYGIENE_MODE, "subjects": "\n".join(lines), "candidates": "\n".join(candidates)},
        learner_id,
        None,
        None,
        f"graph.hygiene:{domain_id}:{':'.join(lines)}",
    )
    status = {s.skill_id: s.status for s in graph.skills}
    events: list[NewEvent] = []
    merged: set[UUID] = set()
    for verdict in result.value.pairs:
        if not verdict.duplicate:
            continue
        kind = "misconception" if verdict.first.startswith("M") else "skill"
        try:
            first = labels.resolve(kind, verdict.first)
            second = labels.resolve(kind, verdict.second)
        except UnknownLabelError:
            continue  # Т-§4.5
        if first == second or first in merged or second in merged:
            continue  # a node merges once per run
        if kind == "misconception":
            from_id, into_id = _ordered(first, second)
            event_type, payload = (
                "misconception.merged",
                MisconceptionMerged(from_id=from_id, into_id=into_id),
            )
        elif PROVISIONAL in (status.get(first), status.get(second)):
            if status.get(first) == status.get(second):
                from_id, into_id = _ordered(first, second)
            else:
                from_id, into_id = (
                    (first, second) if status.get(first) == PROVISIONAL else (second, first)
                )
            event_type, payload = "skill.merged", SkillMerged(from_id=from_id, into_id=into_id)
        else:
            from_id, into_id = _ordered(first, second)
            event_type, payload = (
                "skill.merge_suggested",
                SkillMergeSuggested(from_id=from_id, into_id=into_id),
            )
        if event_type != "skill.merge_suggested":
            merged.add(from_id)
        events.append(
            NewEvent(
                type=event_type,
                payload=payload,
                actor=prompt,
                idempotency_key=f"hygiene:{event_type}:{from_id}:{into_id}",
                domain_id=domain_id,
            )
        )
    if events:
        await append_events(ctx.db, learner_id, events)
    return [e.type for e in events]


@register_job_handler("graph.hygiene")
async def handle_graph_hygiene(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None or ctx.neo4j is None:
        raise ValueError("graph.hygiene needs learner_id and Neo4j")
    domain_id = UUID(str(job.payload["domain_id"]))
    graph = await read_domain_graph(ctx.neo4j, learner_id=job.learner_id, domain_id=domain_id)
    await run_hygiene(ctx, graph, learner_id=job.learner_id, domain_id=domain_id)
