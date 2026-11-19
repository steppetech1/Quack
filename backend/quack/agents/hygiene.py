"""Graph hygiene & topic placement — the job `graph.hygiene` (ТЗ Ф4 WP16.3; Т-§4.10, Т-§22.10,
Т-§9.3; `T-AG4`). D-B-185.

Single background writer for topic placement & duplicate merging under `lock_key = domain:{id}`:
- Mode `Hygiene`: evaluates similar skill and misconception pairs (`skill.merged`,
  `skill.merge_suggested`, `misconception.merged`).
- Mode `Topics`: sees ALL existing non-hidden topics of the domain and all unassigned skills;
  places unassigned skills into existing or newly created canonical topics (`topic.created`,
  `skill.placed`) and merges duplicate topics (`topic.merged`, `topic.renamed`).
- Logs builder call count, `input_tokens`, `output_tokens`, and `cached_tokens` for cost visibility.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from neo4j import AsyncDriver

from quack.agents.reconcile import node_id, normalize
from quack.agents.schemas import HygieneOutput, TopicsOutput
from quack.config import Settings
from quack.db import Db
from quack.db.neo4j import cypher, read_session
from quack.events.event_types import (
    MisconceptionMerged,
    SkillMerged,
    SkillMergeSuggested,
    SkillPlaced,
    TopicCreated,
    TopicMerged,
    TopicRenamed,
)
from quack.events.log import NewEvent, append_events
from quack.graph.topic_names import canonical_topic_name, normalized_topic_key
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.llm.labels import LabelMap, UnknownLabelError
from quack.logging import get_logger

log = get_logger(__name__)

BUILDER_ROLE = "builder"
HYGIENE_MODE = "Hygiene"
TOPICS_MODE = "Topics"
PROVISIONAL = "provisional"

_DOMAIN = "MATCH (d:Domain {id: $d}) WHERE d.learnerId = $l RETURN d.name AS name"
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
_TOPICS = (
    "MATCH (t:Topic) WHERE t.learnerId = $l AND t.domainId = $d "
    "AND coalesce(t.status, 'active') IN ['active', 'proposed'] "
    "OPTIONAL MATCH (t)-[:COVERS]->(s:Skill) WHERE s.learnerId = $l "
    "AND coalesce(s.status, 'active') IN ['provisional', 'active'] "
    "RETURN t.id AS id, t.name AS name, coalesce(t.status, 'active') AS status, "
    "t.embedding AS embedding, collect(DISTINCT s.name) AS skill_names "
    "ORDER BY t.name, t.id"
)
_UNASSIGNED_SKILLS = (
    "MATCH (s:Skill) WHERE s.learnerId = $l AND s.domainId = $d "
    "AND coalesce(s.status, 'active') IN ['provisional', 'active'] "
    "AND NOT (:Topic)-[:COVERS]->(s) "
    "RETURN s.id AS id, s.name AS name, s.status AS status "
    "ORDER BY s.name, s.id"
)

_BASICS_PREFIXES = ("basics of ", "introduction to ", "intro to ")
_BASICS_SUFFIXES = (" basics", " introduction", " intro")


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
class TopicNode:
    topic_id: UUID
    name: str
    status: str
    embedding: list[float] | None = None
    skill_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnassignedSkillNode:
    skill_id: UUID
    name: str
    status: str


@dataclass(frozen=True)
class DomainGraph:
    skills: list[SkillNode] = field(default_factory=list)
    misconceptions: list[MisconceptionNode] = field(default_factory=list)
    topics: list[TopicNode] = field(default_factory=list)
    unassigned_skills: list[UnassignedSkillNode] = field(default_factory=list)
    domain_name: str = ""


async def read_domain_graph(
    driver: AsyncDriver, *, learner_id: UUID, domain_id: UUID
) -> DomainGraph:
    async with read_session(driver) as session:
        dom_res = await session.run(cypher(_DOMAIN), l=str(learner_id), d=str(domain_id))
        dom_row = await dom_res.single()
        domain_name = str(dom_row["name"] or "") if dom_row is not None else ""

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
        result = await session.run(cypher(_TOPICS), l=str(learner_id), d=str(domain_id))
        topics = [
            TopicNode(
                UUID(r["id"]),
                r["name"] or "",
                r["status"] or "active",
                list(r["embedding"]) if r["embedding"] is not None else None,
                tuple(str(n) for n in (r["skill_names"] or ()) if n),
            )
            async for r in result
        ]
        result = await session.run(cypher(_UNASSIGNED_SKILLS), l=str(learner_id), d=str(domain_id))
        unassigned = [
            UnassignedSkillNode(UUID(r["id"]), r["name"] or "", r["status"] or "provisional")
            async for r in result
        ]
    return DomainGraph(skills, misconceptions, topics, unassigned, domain_name)


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


def _strip_basics_qualifier(key: str) -> str:
    for prefix in _BASICS_PREFIXES:
        if key.startswith(prefix) and len(key) > len(prefix):
            return key[len(prefix) :].strip()
    for suffix in _BASICS_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix):
            return key[: -len(suffix)].strip()
    return key


def _canonical_rank(topic: TopicNode, domain_name: str) -> tuple[int, int, int, str]:
    """Lower tuple wins as the surviving canonical topic."""
    clean = canonical_topic_name(topic.name, domain_name)
    is_clean = 0 if clean == " ".join(topic.name.split()) else 1
    is_active = 0 if topic.status == "active" else 1
    no_basics = 0 if _strip_basics_qualifier(normalize(clean)) == normalize(clean) else 1
    return (is_active, is_clean, no_basics, str(topic.topic_id))


def deterministic_topic_cleanup_plan(
    topics: Sequence[TopicNode], *, domain_name: str = ""
) -> tuple[list[tuple[UUID, UUID]], list[tuple[UUID, str]]]:
    """Deterministic safety-net merges (`(from_id, into_id)`) and renames (`(topic_id, name)`):
    1. Exact canonical matches (including stripping `<DomainName>: `).
    2. `<topic> basics` / `intro` / `introduction` when `<topic>` also exists in the domain.
    3. Renaming any surviving topic that still has a redundant `<DomainName>: ` prefix.
    """
    if not topics:
        return [], []
    by_exact: dict[str, list[TopicNode]] = {}
    for t in topics:
        by_exact.setdefault(normalized_topic_key(t.name, domain_name), []).append(t)

    merges: list[tuple[UUID, UUID]] = []
    merged_from: set[UUID] = set()
    survivors: dict[str, TopicNode] = {}
    for key, group in sorted(by_exact.items()):
        ordered = sorted(group, key=lambda t: _canonical_rank(t, domain_name))
        winner = ordered[0]
        survivors[key] = winner
        for loser in ordered[1:]:
            merges.append((loser.topic_id, winner.topic_id))
            merged_from.add(loser.topic_id)

    for key, topic in sorted(list(survivors.items())):
        if topic.topic_id in merged_from:
            continue
        base = _strip_basics_qualifier(key)
        if base != key and base in survivors and survivors[base].topic_id != topic.topic_id:
            target = survivors[base]
            merges.append((topic.topic_id, target.topic_id))
            merged_from.add(topic.topic_id)

    renames: list[tuple[UUID, str]] = []
    for t in sorted(topics, key=lambda item: str(item.topic_id)):
        if t.topic_id in merged_from:
            continue
        clean = canonical_topic_name(t.name, domain_name)
        if clean and clean != t.name:
            renames.append((t.topic_id, clean))
    return merges, renames


def _resolve_chain(topic_id: UUID, merge_map: dict[UUID, UUID]) -> UUID:
    seen: set[UUID] = {topic_id}
    cur = topic_id
    while cur in merge_map and merge_map[cur] not in seen:
        cur = merge_map[cur]
        seen.add(cur)
    return cur


@dataclass
class _UsageTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    def add(self, usage: object) -> None:
        self.calls += 1
        if usage is None:
            return
        self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        self.cached_tokens += int(getattr(usage, "cached_tokens", 0) or 0)


async def _run_entity_hygiene(
    ctx: JobContext,
    graph: DomainGraph,
    *,
    learner_id: UUID,
    domain_id: UUID,
    prompt: str,
    totals: _UsageTotals,
) -> list[NewEvent]:
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
    totals.add(result.usage)
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
    return events


def _topics_problem(output: TopicsOutput, labels: LabelMap) -> str | None:
    bad: list[str] = []
    seen_skills: set[UUID] = set()
    for p in output.placements:
        try:
            sid = labels.resolve("skill", p.skill)
            seen_skills.add(sid)
        except UnknownLabelError:
            bad.append(p.skill)
        if p.topic is not None:
            try:
                labels.resolve("topic", p.topic)
            except UnknownLabelError:
                bad.append(p.topic)
        has_topic = p.topic is not None
        has_new = bool(p.new_topic and p.new_topic.strip())
        if has_topic == has_new:
            return f"placement for {p.skill} must set exactly one of topic or new_topic"
    for m in output.merges:
        for lbl in (m.from_topic, m.into_topic):
            try:
                labels.resolve("topic", lbl)
            except UnknownLabelError:
                bad.append(lbl)
    if bad:
        return "unknown labels: " + ", ".join(dict.fromkeys(bad)) + "; use only the given labels"
    expected = set(labels.labels("skill").values())
    if seen_skills != expected:
        return "provide a placement for every unassigned skill listed in the input"
    return None


def _render_topics_input(
    graph: DomainGraph, labels: LabelMap
) -> tuple[str, str]:
    dom = f"Domain: {graph.domain_name}\n" if graph.domain_name else ""
    skill_lines = [
        f"{lbl}: {s.name}"
        for s in graph.unassigned_skills
        if (lbl := labels.label("skill", s.skill_id))
    ]
    subjects = dom + (
        "Unassigned skills:\n" + "\n".join(skill_lines)
        if skill_lines
        else "Unassigned skills: (none)"
    )
    topic_lines: list[str] = []
    for t in graph.topics:
        lbl = labels.label("topic", t.topic_id)
        if not lbl:
            continue
        skills = ", ".join(t.skill_names) if t.skill_names else "none"
        topic_lines.append(f"{lbl}: {t.name} [{t.status}] (skills: {skills})")
    candidates = "\n".join(topic_lines) or "(none)"
    return subjects, candidates


async def _run_topics_placement(
    ctx: JobContext,
    graph: DomainGraph,
    *,
    learner_id: UUID,
    domain_id: UUID,
    prompt: str,
    totals: _UsageTotals,
) -> list[NewEvent]:
    if not graph.unassigned_skills and len(graph.topics) < 2:
        if len(graph.topics) == 1:
            t = graph.topics[0]
            clean = canonical_topic_name(t.name, graph.domain_name)
            if clean and clean != t.name:
                return [
                    NewEvent(
                        type="topic.renamed",
                        payload=TopicRenamed(topic_id=t.topic_id, name=clean),
                        actor=prompt,
                        idempotency_key=f"hygiene:topic.renamed:{t.topic_id}:{normalize(clean)}",
                        domain_id=domain_id,
                    )
                ]
        return []

    settings = ctx.settings
    labels = LabelMap.build(
        settings,
        skills=[s.skill_id for s in graph.unassigned_skills],
        topics=[t.topic_id for t in graph.topics],
        skills_limit=max(len(graph.unassigned_skills), settings.topic_split_skills_k),
        topics_limit=max(len(graph.topics), settings.label_max_topics),
    )
    subjects_text, candidates_text = _render_topics_input(graph, labels)
    digest = hashlib.sha256(f"{subjects_text}\n{candidates_text}".encode()).hexdigest()[:16]
    result = await ctx.llm.generate_structured(
        BUILDER_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        TopicsOutput,
        {"mode": TOPICS_MODE, "subjects": subjects_text, "candidates": candidates_text},
        learner_id,
        None,
        None,
        f"graph.hygiene.topics:{domain_id}:{digest}",
        check=lambda out: _topics_problem(out, labels),
    )
    totals.add(result.usage)

    events: list[NewEvent] = []
    topic_by_id = {t.topic_id: t for t in graph.topics}
    merge_map: dict[UUID, UUID] = {}

    for m in result.value.merges:
        try:
            from_id = labels.resolve("topic", m.from_topic)
            into_id = labels.resolve("topic", m.into_topic)
        except UnknownLabelError:
            continue
        into_id = _resolve_chain(into_id, merge_map)
        if from_id == into_id or from_id in merge_map:
            continue
        from_topic, into_topic = topic_by_id.get(from_id), topic_by_id.get(into_id)
        if (
            from_topic is not None
            and into_topic is not None
            and _canonical_rank(from_topic, graph.domain_name)
            < _canonical_rank(into_topic, graph.domain_name)
            and (
                (from_topic.status == "active" and into_topic.status == "proposed")
                or (
                    from_topic.status == into_topic.status
                    and canonical_topic_name(from_topic.name, graph.domain_name)
                    == " ".join(from_topic.name.split())
                    and canonical_topic_name(into_topic.name, graph.domain_name)
                    != " ".join(into_topic.name.split())
                )
            )
        ):
            from_id, into_id = into_id, from_id
        merge_map[from_id] = into_id
        events.append(
            NewEvent(
                type="topic.merged",
                payload=TopicMerged(from_id=from_id, into_id=into_id),
                actor=prompt,
                idempotency_key=f"hygiene:topic.merged:{from_id}:{into_id}",
                domain_id=domain_id,
            )
        )

    # Deterministic safety net on surviving topics: catch any exact canonical or basics duplicates
    # and strip redundant `<DomainName>: ` prefixes.
    surviving_nodes = [t for t in graph.topics if t.topic_id not in merge_map]
    extra_merges, renames = deterministic_topic_cleanup_plan(
        surviving_nodes, domain_name=graph.domain_name
    )
    for from_id, into_id in extra_merges:
        into_id = _resolve_chain(into_id, merge_map)
        if from_id == into_id or from_id in merge_map:
            continue
        merge_map[from_id] = into_id
        events.append(
            NewEvent(
                type="topic.merged",
                payload=TopicMerged(from_id=from_id, into_id=into_id),
                actor=prompt,
                idempotency_key=f"hygiene:topic.merged:{from_id}:{into_id}",
                domain_id=domain_id,
            )
        )
    for topic_id, clean_name in renames:
        if topic_id in merge_map:
            continue
        events.append(
            NewEvent(
                type="topic.renamed",
                payload=TopicRenamed(topic_id=topic_id, name=clean_name),
                actor=prompt,
                idempotency_key=f"hygiene:topic.renamed:{topic_id}:{normalize(clean_name)}",
                domain_id=domain_id,
            )
        )

    surviving_by_key: dict[str, UUID] = {}
    for t in graph.topics:
        if t.topic_id not in merge_map:
            surviving_by_key.setdefault(
                normalized_topic_key(t.name, graph.domain_name), t.topic_id
            )
    created_topics: set[UUID] = set()
    placed_skills: set[UUID] = set()

    for p in result.value.placements:
        try:
            skill_id = labels.resolve("skill", p.skill)
        except UnknownLabelError:
            continue
        if skill_id in placed_skills:
            continue
        target_topic: UUID | None = None
        if p.topic is not None:
            try:
                target_topic = _resolve_chain(labels.resolve("topic", p.topic), merge_map)
            except UnknownLabelError:
                target_topic = None
        if target_topic is None and p.new_topic and p.new_topic.strip():
            clean_name = canonical_topic_name(p.new_topic, graph.domain_name)
            key = normalized_topic_key(clean_name, graph.domain_name)
            if key in surviving_by_key:
                target_topic = surviving_by_key[key]
            elif clean_name:
                target_topic = node_id(learner_id, domain_id, "topic", clean_name)
                surviving_by_key[key] = target_topic
                if target_topic not in created_topics:
                    created_topics.add(target_topic)
                    events.append(
                        NewEvent(
                            type="topic.created",
                            payload=TopicCreated(
                                topic_id=target_topic,
                                domain_id=domain_id,
                                name=clean_name,
                                origin="session",
                            ),
                            actor=prompt,
                            idempotency_key=f"hygiene:topic.created:{target_topic}",
                            domain_id=domain_id,
                        )
                    )
        if target_topic is None:
            continue
        placed_skills.add(skill_id)
        events.append(
            NewEvent(
                type="skill.placed",
                payload=SkillPlaced(skill_id=skill_id, topic_id=target_topic, domain_id=domain_id),
                actor=prompt,
                idempotency_key=f"hygiene:skill.placed:{skill_id}:{target_topic}",
                domain_id=domain_id,
            )
        )
    return events


async def run_hygiene(
    ctx: JobContext, graph: DomainGraph, *, learner_id: UUID, domain_id: UUID
) -> list[str]:
    """The event types written, in order; nothing when there is no candidate."""
    prompt = ctx.settings.llm_roles[BUILDER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'builder' has no prompt")
    totals = _UsageTotals()
    events: list[NewEvent] = []
    events.extend(
        await _run_entity_hygiene(
            ctx, graph, learner_id=learner_id, domain_id=domain_id, prompt=prompt, totals=totals
        )
    )
    events.extend(
        await _run_topics_placement(
            ctx, graph, learner_id=learner_id, domain_id=domain_id, prompt=prompt, totals=totals
        )
    )
    if events:
        await append_events(ctx.db, learner_id, events)
    if totals.calls > 0 or events:
        log.info(
            "builder_hygiene_completed",
            role=BUILDER_ROLE,
            learner_id=str(learner_id),
            domain_id=str(domain_id),
            calls=totals.calls,
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            cached_tokens=totals.cached_tokens,
            events=[e.type for e in events],
        )
    return [e.type for e in events]


async def cleanup_duplicate_topics(
    db: Db,
    neo4j: AsyncDriver,
    *,
    learner_id: UUID,
    domain_id: UUID,
    actor: str = "system",
) -> list[tuple[UUID, UUID]]:
    """Deterministic duplicate-topic cleanup using `topic.merged` (and `topic.renamed` if
    needed) via `append_events`. Returns the list of `(from_id, into_id)` merges written."""
    graph = await read_domain_graph(neo4j, learner_id=learner_id, domain_id=domain_id)
    merges, renames = deterministic_topic_cleanup_plan(
        graph.topics, domain_name=graph.domain_name
    )
    events: list[NewEvent] = [
        NewEvent(
            type="topic.merged",
            payload=TopicMerged(from_id=from_id, into_id=into_id),
            actor=actor,
            idempotency_key=f"cleanup:topic.merged:{from_id}:{into_id}",
            domain_id=domain_id,
        )
        for from_id, into_id in merges
    ]
    events.extend(
        NewEvent(
            type="topic.renamed",
            payload=TopicRenamed(topic_id=topic_id, name=name),
            actor=actor,
            idempotency_key=f"cleanup:topic.renamed:{topic_id}:{normalize(name)}",
            domain_id=domain_id,
        )
        for topic_id, name in renames
    )
    if events:
        await append_events(db, learner_id, events)
        log.info(
            "duplicate_topics_cleaned",
            learner_id=str(learner_id),
            domain_id=str(domain_id),
            merges=len(merges),
            renames=len(renames),
        )
    return merges


@register_job_handler("graph.hygiene")
async def handle_graph_hygiene(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None or ctx.neo4j is None:
        raise ValueError("graph.hygiene needs learner_id and Neo4j")
    domain_id = UUID(str(job.payload["domain_id"]))
    graph = await read_domain_graph(ctx.neo4j, learner_id=job.learner_id, domain_id=domain_id)
    await run_hygiene(ctx, graph, learner_id=job.learner_id, domain_id=domain_id)
