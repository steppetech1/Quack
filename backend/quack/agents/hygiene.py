"""Graph hygiene & structure writer — the job `graph.hygiene` and `resolve_material_structure`
(ТЗ Ф4 WP16.3; TZ §4.1, §4.2, §4.4, §4.8, §5, Addition #2).

Single background writer under `lock_key = domain:{id}`:
- Addition #2: cleans up empty provisional skills from ended sessions (0 evidence, 0 exposures).
- Mode `Hygiene`: evaluates similar skill and misconception pairs (`skill.merged`,
  `skill.merge_suggested`, `misconception.merged`).
- Mode `Structure`: resolves material topic candidates (creating canonical topics with 1 core
  skill each + `REQUIRES` edges with 4-rule filtering) and reconciles provisional session skills
  (`same`, `keep`, `move`, `core_of_new_topic`, `other_topic_skill`).
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter, deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid5

from neo4j import AsyncDriver

from quack.agents.reconcile import (
    Candidate,
    Subject,
    append_unwritten,
    find_candidates,
    node_id,
    normalize,
)
from quack.agents.schemas import (
    HygieneOutput,
    SkillKind,
    StructureOutput,
    TopicCandidateDecision,
)
from quack.config import Settings
from quack.db import Db
from quack.db.neo4j import cypher, read_session
from quack.events.event_types import (
    EdgeAdded,
    MisconceptionMerged,
    Origin,
    SkillCreated,
    SkillDeleted,
    SkillMerged,
    SkillMergeSuggested,
    SkillPlaced,
    TopicCreated,
    TopicMerged,
    TopicRenamed,
)
from quack.events.log import NewEvent
from quack.graph.ids import QUACK_UUID_NS, deterministic_id
from quack.graph.topic_names import canonical_topic_name, normalized_topic_key
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.llm.labels import LabelMap, UnknownLabelError
from quack.logging import get_logger

log = get_logger(__name__)

BUILDER_ROLE = "builder"
HYGIENE_MODE = "Hygiene"
STRUCTURE_MODE = "Structure"
PROVISIONAL = "provisional"

_DOMAIN = "MATCH (d:Domain {id: $d}) WHERE d.learnerId = $l RETURN d.name AS name"
_SKILLS = (
    "MATCH (s:Skill) WHERE s.learnerId = $l AND s.domainId = $d "
    "AND coalesce(s.status, 'active') IN ['provisional', 'active'] "
    "OPTIONAL MATCH (s)-[:REQUIRES]-(n:Skill) WHERE n.learnerId = $l "
    "OPTIONAL MATCH (t:Topic)-[:COVERS]->(s) WHERE t.learnerId = $l "
    "OPTIONAL MATCH (ev:Evidence)-[:ON]->(s) WHERE ev.learnerId = $l "
    "AND coalesce(ev.status, 'active') = 'active' "
    "OPTIONAL MATCH (ex:Exposure)-[:OF]->(s) WHERE ex.learnerId = $l "
    "RETURN s.id AS id, s.name AS name, coalesce(s.status, 'active') AS status, "
    "coalesce(s.core, false) AS core, "
    "coalesce(s.definition, coalesce(s.summary, '')) AS definition, "
    "s.embedding AS embedding, head(collect(DISTINCT t.id)) AS topic_id, "
    "collect(DISTINCT n.id) + collect(DISTINCT t.id) AS neighbours, "
    "count(DISTINCT ev) AS evidence_count, count(DISTINCT ex) AS exposure_count"
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
    "coalesce(t.description, '') AS description, t.embedding AS embedding, "
    "collect(DISTINCT {id: s.id, name: s.name, core: coalesce(s.core, false)}) AS skills "
    "ORDER BY t.name, t.id"
)
_UNASSIGNED_SKILLS = (
    "MATCH (s:Skill) WHERE s.learnerId = $l AND s.domainId = $d "
    "AND coalesce(s.status, 'active') IN ['provisional', 'active'] "
    "AND NOT (:Topic)-[:COVERS]->(s) "
    "RETURN s.id AS id, s.name AS name, coalesce(s.status, 'provisional') AS status "
    "ORDER BY s.name, s.id"
)
_REQUIRES = (
    "MATCH (a:Skill)-[:REQUIRES]->(b:Skill) "
    "WHERE a.learnerId = $l AND a.domainId = $d AND b.learnerId = $l AND b.domainId = $d "
    "RETURN a.id AS a, b.id AS b"
)

_ENDED_SESSIONS = (
    "SELECT DISTINCT COALESCE((payload->>'session_id')::uuid, session_id) AS sid "
    "FROM core.events WHERE learner_id = %(learner_id)s AND type = 'session.ended'"
)
_SESSION_SKILL_EVENTS = (
    "SELECT seq, type, session_id, payload FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = ANY(%(types)s) ORDER BY seq"
)
_ACTIVITY_EVENT_TYPES = [
    "skill.created",
    "task.created",
    "task.verified",
    "task.key_disputed",
    "task.markup_disputed",
    "turn.committed",
    "observation.extracted",
    "answer.submitted",
    "verdict.recorded",
    "observation.diagnosed",
]

_BASICS_PREFIXES = ("basics of ", "introduction to ", "intro to ")
_BASICS_SUFFIXES = (" basics", " introduction", " intro")


@dataclass(frozen=True)
class SkillNode:
    skill_id: UUID
    name: str
    status: str
    embedding: list[float]
    neighbours: frozenset[str] = frozenset()
    core: bool = False
    topic_id: UUID | None = None
    definition: str = ""
    evidence_count: int = 0
    exposure_count: int = 0


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
    description: str = ""
    core_skill_id: UUID | None = None
    core_skill_name: str = ""
    skill_ids: tuple[UUID, ...] = ()


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
    requires_edges: frozenset[tuple[UUID, UUID]] = frozenset()


@dataclass(frozen=True)
class MaterialBuildsOn:
    concept: str
    quote: str


@dataclass(frozen=True)
class MaterialTopicCandidate:
    name: str
    description: str
    core_skill_name: str
    builds_on: tuple[MaterialBuildsOn, ...] = ()


@dataclass(frozen=True)
class ResolvedStructureTopic:
    topic_id: UUID
    core_skill_id: UUID
    skill_ids: tuple[UUID, ...] = ()


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
                r["status"] or "active",
                list(r["embedding"]) if r["embedding"] is not None else [],
                frozenset(str(n) for n in (r["neighbours"] or ()) if n),
                core=bool(r.get("core")),
                topic_id=UUID(str(r["topic_id"])) if r.get("topic_id") else None,
                definition=str(r.get("definition") or ""),
                evidence_count=int(r.get("evidence_count") or 0),
                exposure_count=int(r.get("exposure_count") or 0),
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
        topics: list[TopicNode] = []
        async for r in result:
            raw_skills = [x for x in (r.get("skills") or ()) if isinstance(x, dict) and x.get("id")]
            if raw_skills:
                skill_names = tuple(str(x["name"]) for x in raw_skills if x.get("name"))
                skill_ids = tuple(UUID(str(x["id"])) for x in raw_skills)
                core_entry = next((x for x in raw_skills if x.get("core")), None)
                core_skill_id = UUID(str(core_entry["id"])) if core_entry else None
                core_skill_name = str(core_entry.get("name") or "") if core_entry else ""
            else:
                skill_names = tuple(str(n) for n in (r.get("skill_names") or ()) if n)
                skill_ids = ()
                core_skill_id = None
                core_skill_name = ""
            topics.append(
                TopicNode(
                    UUID(r["id"]),
                    r["name"] or "",
                    r["status"] or "active",
                    list(r["embedding"]) if r["embedding"] is not None else None,
                    skill_names,
                    description=str(r.get("description") or ""),
                    core_skill_id=core_skill_id,
                    core_skill_name=core_skill_name,
                    skill_ids=skill_ids,
                )
            )
        result = await session.run(cypher(_UNASSIGNED_SKILLS), l=str(learner_id), d=str(domain_id))
        unassigned = [
            UnassignedSkillNode(UUID(r["id"]), r["name"] or "", r["status"] or "provisional")
            async for r in result
        ]
        result = await session.run(cypher(_REQUIRES), l=str(learner_id), d=str(domain_id))
        requires_edges = frozenset(
            [
                (UUID(str(r["a"])), UUID(str(r["b"])))
                async for r in result
                if r.get("a") and r.get("b")
            ]
        )
    return DomainGraph(skills, misconceptions, topics, unassigned, domain_name, requires_edges)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=False)) / norm if norm else 0.0


def skill_pairs(
    skills: Sequence[SkillNode], settings: Settings
) -> list[tuple[SkillNode, SkillNode]]:
    embedded = [s for s in skills if s.embedding]
    scored: list[tuple[float, SkillNode, SkillNode]] = []
    for i, a in enumerate(embedded):
        for b in embedded[i + 1 :]:
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


# --- Structure mutable state & helpers -------------------------------------------------------


@dataclass
class _TopicState:
    topic_id: UUID
    name: str
    status: str
    description: str
    core_skill_id: UUID | None
    core_skill_name: str
    skill_ids: list[UUID] = field(default_factory=list)
    skill_names: dict[UUID, str] = field(default_factory=dict)


@dataclass
class _SkillState:
    skill_id: UUID
    name: str
    status: str
    core: bool
    topic_id: UUID | None
    definition: str = ""
    evidence_count: int = 0
    exposure_count: int = 0


class _StructureState:
    """In-memory view of the domain graph updated after each batch."""

    def __init__(self, graph: DomainGraph) -> None:
        self.domain_name = graph.domain_name
        self.topics: list[_TopicState] = []
        self.topics_by_id: dict[UUID, _TopicState] = {}
        self.topics_by_key: dict[str, _TopicState] = {}
        self.skills_by_id: dict[UUID, _SkillState] = {}
        self.requires_edges: set[tuple[UUID, UUID]] = set(graph.requires_edges)

        for s in graph.skills:
            self.skills_by_id[s.skill_id] = _SkillState(
                skill_id=s.skill_id,
                name=s.name,
                status=s.status,
                core=s.core,
                topic_id=s.topic_id,
                definition=s.definition,
                evidence_count=s.evidence_count,
                exposure_count=s.exposure_count,
            )
        for u in graph.unassigned_skills:
            if u.skill_id not in self.skills_by_id:
                self.skills_by_id[u.skill_id] = _SkillState(
                    skill_id=u.skill_id,
                    name=u.name,
                    status=u.status,
                    core=False,
                    topic_id=None,
                )

        for t in graph.topics:
            skill_ids = list(t.skill_ids)
            skill_names: dict[UUID, str] = {}
            for sid in skill_ids:
                st = self.skills_by_id.get(sid)
                if st is not None:
                    skill_names[sid] = st.name
                    if st.topic_id is None:
                        st.topic_id = t.topic_id
            for s in self.skills_by_id.values():
                if s.topic_id == t.topic_id and s.skill_id not in skill_names:
                    skill_ids.append(s.skill_id)
                    skill_names[s.skill_id] = s.name
            core_id = t.core_skill_id
            core_name = t.core_skill_name
            if core_id is None:
                core_s = next(
                    (
                        self.skills_by_id[sid]
                        for sid in skill_ids
                        if sid in self.skills_by_id and self.skills_by_id[sid].core
                    ),
                    None,
                )
                if core_s is not None:
                    core_id = core_s.skill_id
                    core_name = core_s.name
            elif core_id not in skill_names:
                skill_ids.insert(0, core_id)
                skill_names[core_id] = core_name
            ts = _TopicState(
                topic_id=t.topic_id,
                name=t.name,
                status=t.status,
                description=t.description,
                core_skill_id=core_id,
                core_skill_name=core_name,
                skill_ids=skill_ids,
                skill_names=skill_names,
            )
            self.topics.append(ts)
            self.topics_by_id[t.topic_id] = ts
            self.topics_by_key.setdefault(normalized_topic_key(t.name, graph.domain_name), ts)

    def provisional_skills(self) -> list[_SkillState]:
        return sorted(
            (s for s in self.skills_by_id.values() if s.status == PROVISIONAL and not s.core),
            key=lambda s: (0 if s.topic_id is None else 1, s.name, str(s.skill_id)),
        )

    def register_topic(
        self,
        *,
        topic_id: UUID,
        name: str,
        status: str,
        description: str,
        core_skill_id: UUID,
        core_skill_name: str,
        core_definition: str,
    ) -> _TopicState:
        ts = self.topics_by_id.get(topic_id)
        if ts is None:
            ts = _TopicState(
                topic_id=topic_id,
                name=name,
                status=status,
                description=description,
                core_skill_id=core_skill_id,
                core_skill_name=core_skill_name,
                skill_ids=[core_skill_id],
                skill_names={core_skill_id: core_skill_name},
            )
            self.topics.append(ts)
            self.topics_by_id[topic_id] = ts
            self.topics_by_key.setdefault(normalized_topic_key(name, self.domain_name), ts)
        else:
            if ts.core_skill_id is None:
                ts.core_skill_id = core_skill_id
                ts.core_skill_name = core_skill_name
            if core_skill_id not in ts.skill_names:
                ts.skill_ids.append(core_skill_id)
                ts.skill_names[core_skill_id] = core_skill_name
        self.skills_by_id[core_skill_id] = _SkillState(
            skill_id=core_skill_id,
            name=core_skill_name,
            status="active",
            core=True,
            topic_id=topic_id,
            definition=core_definition,
        )
        return ts

    def promote_skill(
        self,
        skill_id: UUID,
        *,
        topic_id: UUID,
        core: bool = False,
        name: str | None = None,
        definition: str = "",
    ) -> None:
        st = self.skills_by_id.get(skill_id)
        if st is None:
            return
        old_topic_id = st.topic_id
        if old_topic_id is not None and old_topic_id != topic_id:
            old_topic = self.topics_by_id.get(old_topic_id)
            if old_topic is not None:
                old_topic.skill_ids = [sid for sid in old_topic.skill_ids if sid != skill_id]
                old_topic.skill_names.pop(skill_id, None)
        st.status = "active"
        st.topic_id = topic_id
        if core:
            st.core = True
        if name:
            st.name = name
        if definition:
            st.definition = definition
        target_topic = self.topics_by_id.get(topic_id)
        if target_topic is not None:
            if skill_id not in target_topic.skill_names:
                target_topic.skill_ids.append(skill_id)
            target_topic.skill_names[skill_id] = st.name
            if core and target_topic.core_skill_id is None:
                target_topic.core_skill_id = skill_id
                target_topic.core_skill_name = st.name

    def remove_skill(self, skill_id: UUID) -> None:
        st = self.skills_by_id.pop(skill_id, None)
        if st is not None and st.topic_id is not None:
            topic = self.topics_by_id.get(st.topic_id)
            if topic is not None:
                topic.skill_ids = [sid for sid in topic.skill_ids if sid != skill_id]
                topic.skill_names.pop(skill_id, None)

    def remove_topic(self, topic_id: UUID) -> None:
        ts = self.topics_by_id.pop(topic_id, None)
        if ts is None:
            return
        self.topics = [t for t in self.topics if t.topic_id != topic_id]
        key = normalized_topic_key(ts.name, self.domain_name)
        if self.topics_by_key.get(key) is ts:
            self.topics_by_key.pop(key, None)


# --- Addition #2: empty provisional skills after session.ended -------------------------------


def _markup_new_skill_ids(
    learner_id: UUID, seq: int, markup: dict[str, Any]
) -> tuple[set[UUID], set[UUID]]:
    """Returns `(all_skill_ids, new_provisional_skill_ids)` referenced by a task's markup."""
    all_ids: set[UUID] = set()
    new_ids: set[UUID] = set()
    tests: list[Any] = list(markup.get("tests") or ())
    for part in markup.get("parts") or ():
        if isinstance(part, dict):
            tests.extend(part.get("tests") or ())
    for t in tests:
        if not isinstance(t, dict):
            continue
        if t.get("skill_id"):
            all_ids.add(UUID(str(t["skill_id"])))
        elif isinstance(ns := t.get("new_skill"), dict) and ns.get("name"):
            sid = UUID(deterministic_id(learner_id, seq, f"skill:{normalize(str(ns['name']))}"))
            all_ids.add(sid)
            new_ids.add(sid)
    return all_ids, new_ids


async def _find_ended_empty_births(
    db: Db, learner_id: UUID, *, session_id: UUID | None
) -> tuple[dict[UUID, UUID], set[UUID], set[UUID]]:
    """From `core.events`, compute `(birth_session_by_skill, ended_sessions, non_empty_skills)`."""
    ended_rows = await db.fetch_all(_ENDED_SESSIONS, {"learner_id": learner_id})
    ended_sessions = {UUID(str(r["sid"])) for r in ended_rows if r.get("sid")}
    if session_id is not None:
        ended_sessions.add(session_id)
    if not ended_sessions:
        return {}, set(), set()

    rows = await db.fetch_all(
        _SESSION_SKILL_EVENTS, {"learner_id": learner_id, "types": _ACTIVITY_EVENT_TYPES}
    )
    birth_session: dict[UUID, UUID] = {}
    non_empty: set[UUID] = set()
    task_skills: dict[UUID, set[UUID]] = {}
    answer_task: dict[int, UUID] = {}

    for row in rows:
        etype = str(row["type"])
        seq = int(row["seq"])
        sid = UUID(str(row["session_id"])) if row.get("session_id") else None
        payload = row["payload"]
        if not isinstance(payload, dict):
            continue
        if etype == "skill.created":
            if payload.get("status") == PROVISIONAL and sid is not None and payload.get("skill_id"):
                birth_session.setdefault(UUID(str(payload["skill_id"])), sid)
        elif etype == "task.created":
            task_id = UUID(str(payload["task_id"]))
            markup = payload.get("markup") or {}
            all_ids, new_ids = _markup_new_skill_ids(learner_id, seq, markup)
            task_skills[task_id] = all_ids
            if sid is not None:
                for nid in new_ids:
                    birth_session.setdefault(nid, sid)
        elif etype in ("task.verified", "task.key_disputed", "task.markup_disputed"):
            corrected = payload.get("corrected_markup")
            if isinstance(corrected, dict):
                task_id = UUID(str(payload["task_id"]))
                all_ids, new_ids = _markup_new_skill_ids(learner_id, seq, corrected)
                task_skills[task_id] = all_ids
                if sid is not None:
                    for nid in new_ids:
                        birth_session.setdefault(nid, sid)
        elif etype == "turn.committed":
            markup = payload.get("markup") or {}
            for exp in markup.get("exposures") or ():
                if isinstance(exp, dict) and exp.get("skill_id"):
                    non_empty.add(UUID(str(exp["skill_id"])))
            for t in markup.get("tasks") or ():
                if isinstance(t, dict) and t.get("task_id"):
                    all_ids, new_ids = _markup_new_skill_ids(learner_id, seq, t)
                    task_skills.setdefault(UUID(str(t["task_id"])), all_ids)
                    if sid is not None:
                        for nid in new_ids:
                            birth_session.setdefault(nid, sid)
        elif etype == "observation.extracted":
            for el in payload.get("elements") or ():
                if not isinstance(el, dict):
                    continue
                skill_uuid: UUID | None = None
                if el.get("skill_id"):
                    skill_uuid = UUID(str(el["skill_id"]))
                elif isinstance(ns := el.get("new_skill"), dict) and ns.get("name"):
                    skill_uuid = UUID(
                        deterministic_id(learner_id, seq, f"skill:{normalize(str(ns['name']))}")
                    )
                    if sid is not None:
                        birth_session.setdefault(skill_uuid, sid)
                if skill_uuid is not None and el.get("kind") == "evidence":
                    non_empty.add(skill_uuid)
        elif etype == "answer.submitted":
            if payload.get("task_id"):
                tid = UUID(str(payload["task_id"]))
                answer_task[seq] = tid
                if payload.get("source") == "card" and payload.get("self_assessment") is not None:
                    non_empty.update(task_skills.get(tid, ()))
        elif etype == "verdict.recorded":
            if payload.get("outcome") != "unclear" and payload.get("answer_seq") is not None:
                ans_tid = answer_task.get(int(payload["answer_seq"]))
                if ans_tid is not None:
                    non_empty.update(task_skills.get(ans_tid, ()))
        elif etype == "observation.diagnosed" and payload.get("task_id"):
            non_empty.update(task_skills.get(UUID(str(payload["task_id"])), ()))

    return birth_session, ended_sessions, non_empty


async def _cleanup_empty_provisional_skills(
    ctx: JobContext,
    state: _StructureState,
    *,
    learner_id: UUID,
    domain_id: UUID,
    prompt: str,
    session_id: UUID | None = None,
) -> list[NewEvent]:
    """Addition #2: clean up empty provisional skills (0 evidence, 0 exposures) from ended
    sessions — merge into their topic's core skill if in a topic, else `skill.deleted`."""
    candidates = state.provisional_skills()
    if not candidates:
        return []
    birth_session, ended_sessions, non_empty = await _find_ended_empty_births(
        ctx.db, learner_id, session_id=session_id
    )
    if not ended_sessions:
        return []

    events: list[NewEvent] = []
    for sk in candidates:
        if sk.evidence_count > 0 or sk.exposure_count > 0 or sk.skill_id in non_empty:
            continue
        born_in = birth_session.get(sk.skill_id)
        if born_in is None or born_in not in ended_sessions:
            continue
        topic = state.topics_by_id.get(sk.topic_id) if sk.topic_id is not None else None
        if (
            topic is not None
            and topic.core_skill_id is not None
            and topic.core_skill_id != sk.skill_id
        ):
            core_id = topic.core_skill_id
            events.append(
                NewEvent(
                    type="skill.merged",
                    payload=SkillMerged(from_id=sk.skill_id, into_id=core_id),
                    actor=prompt,
                    idempotency_key=f"hygiene:empty_birth:skill.merged:{sk.skill_id}:{core_id}",
                    domain_id=domain_id,
                )
            )
            state.remove_skill(sk.skill_id)
        else:
            events.append(
                NewEvent(
                    type="skill.deleted",
                    payload=SkillDeleted(skill_id=sk.skill_id, reason="empty_session_birth"),
                    actor=prompt,
                    idempotency_key=f"hygiene:empty_birth:skill.deleted:{sk.skill_id}",
                    domain_id=domain_id,
                )
            )
            state.remove_skill(sk.skill_id)

    if events:
        written = await append_unwritten(ctx.db, learner_id, events)
        return written
    return []


# --- Mode Hygiene ----------------------------------------------------------------------------


async def _run_entity_hygiene(
    ctx: JobContext,
    graph: DomainGraph,
    state: _StructureState,
    *,
    learner_id: UUID,
    domain_id: UUID,
    prompt: str,
    totals: _UsageTotals,
) -> list[NewEvent]:
    settings = ctx.settings
    live_skills = [s for s in graph.skills if s.skill_id in state.skills_by_id]
    pairs = skill_pairs(live_skills, settings)
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
        for s in live_skills
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
    status = {s.skill_id: s.status for s in live_skills}
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
        if event_type == "skill.merged":
            state.remove_skill(from_id)
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
        return await append_unwritten(ctx.db, learner_id, events)
    return []


# --- Mode Structure --------------------------------------------------------------------------


def _reaches(adj: dict[UUID, set[UUID]], start: UUID, target: UUID, *, min_hops: int = 1) -> bool:
    """BFS reachability in directed graph `adj` requiring at least `min_hops` edges."""
    if min_hops == 0 and start == target:
        return True
    if min_hops == 1:
        visited: set[UUID] = {start}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            for nxt in adj.get(cur, ()):
                if nxt == target:
                    return True
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        return False
    # min_hops >= 2: start from 2-hop neighbours (first hop must not directly end at target)
    visited = set()
    queue = deque()
    for mid in adj.get(start, ()):
        if mid != target and mid not in visited:
            visited.add(mid)
            queue.append(mid)
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, ()):
            if nxt == target:
                return True
            if nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)
    return False


@dataclass(frozen=True)
class _RawRequires:
    from_id: UUID
    to_id: UUID
    score: float
    origin: Literal["material", "model"]


def filter_requires_edges(
    claims: Sequence[_RawRequires],
    existing_edges: set[tuple[UUID, UUID]],
    *,
    min_score: float,
) -> tuple[list[_RawRequires], Counter[str]]:
    """5-step edge filter on skill `REQUIRES` edges:
    1. Drop if `score < min_score` (`score`).
    2. If both `(A->B)` and `(B->A)` arrive in the batch with `score >= min_score`, drop BOTH
       (`both_directions`).
    3. Drop if opposite `(B->A)` (or path `B->...->A`) exists in graph or accepted batch
       (`reverse`).
    4. Drop if transitively implied by existing graph + accepted batch (`A->...->C` exists with
       >= 2 hops -> drop `A->C`, `transitive`).
    """
    dropped: Counter[str] = Counter()
    above_score: list[_RawRequires] = []
    for c in claims:
        if c.from_id == c.to_id:
            continue
        if c.score < min_score:
            dropped["score"] += 1
            continue
        above_score.append(c)

    # Deduplicate exact `(from_id, to_id, origin)` keeping highest score.
    by_key: dict[tuple[UUID, UUID, str], _RawRequires] = {}
    for c in above_score:
        key = (c.from_id, c.to_id, c.origin)
        prev = by_key.get(key)
        if prev is None or c.score > prev.score:
            by_key[key] = c
    deduped = list(by_key.values())

    pairs_in_batch = {(c.from_id, c.to_id) for c in deduped}
    bidirectional = {(a, b) for (a, b) in pairs_in_batch if (b, a) in pairs_in_batch}
    remaining: list[_RawRequires] = []
    for c in deduped:
        if (c.from_id, c.to_id) in bidirectional:
            dropped["both_directions"] += 1
            continue
        remaining.append(c)

    # Order edges so direct edges (not transitively implied by graph + other remaining edges)
    # are evaluated before shortcuts in the same batch.
    combined_adj: dict[UUID, set[UUID]] = {}
    for a, b in existing_edges:
        combined_adj.setdefault(a, set()).add(b)
    for c in remaining:
        combined_adj.setdefault(c.from_id, set()).add(c.to_id)

    remaining.sort(
        key=lambda c: (
            1 if _reaches(combined_adj, c.from_id, c.to_id, min_hops=2) else 0,
            str(c.from_id),
            str(c.to_id),
        )
    )

    adj: dict[UUID, set[UUID]] = {}
    for a, b in existing_edges:
        adj.setdefault(a, set()).add(b)

    accepted: list[_RawRequires] = []
    for c in remaining:
        if _reaches(adj, c.to_id, c.from_id, min_hops=1):
            dropped["reverse"] += 1
            continue
        if _reaches(adj, c.from_id, c.to_id, min_hops=2):
            dropped["transitive"] += 1
            continue
        adj.setdefault(c.from_id, set()).add(c.to_id)
        existing_edges.add((c.from_id, c.to_id))
        accepted.append(c)

    return accepted, dropped


def _structure_problem(
    output: StructureOutput,
    labels: LabelMap,
    candidate_count: int,
    expected_prov_ids: set[UUID],
    prov_has_topic: dict[UUID, bool],
) -> str | None:
    bad: list[str] = []
    seen_cands: set[int] = set()
    for tc in output.topic_candidates:
        seen_cands.add(tc.candidate_index)
        if tc.target_topic is not None:
            try:
                labels.resolve("topic", tc.target_topic)
            except UnknownLabelError:
                bad.append(tc.target_topic)
        for req in tc.requires:
            try:
                labels.resolve("skill", req.target_core_skill_label)
            except UnknownLabelError:
                bad.append(req.target_core_skill_label)

    seen_provs: set[UUID] = set()
    for ps in output.provisional_skills:
        try:
            pid = labels.resolve("skill", ps.skill)
            seen_provs.add(pid)
        except UnknownLabelError:
            bad.append(ps.skill)
        if ps.target_skill is not None:
            try:
                labels.resolve("skill", ps.target_skill)
            except UnknownLabelError:
                bad.append(ps.target_skill)
        if ps.target_topic is not None:
            try:
                labels.resolve("topic", ps.target_topic)
            except UnknownLabelError:
                bad.append(ps.target_topic)

    if bad:
        return "unknown labels: " + ", ".join(dict.fromkeys(bad)) + "; use only the given labels"

    if seen_cands != set(range(candidate_count)):
        return f"provide exactly one topic_candidates entry for indices 0..{candidate_count - 1}"
    if seen_provs != expected_prov_ids:
        return "provide exactly one provisional_skills entry for every provisional skill listed"

    for tc in output.topic_candidates:
        if tc.action == "link" and tc.target_topic is None:
            return f"candidate {tc.candidate_index}: action 'link' requires target_topic"
        if tc.action == "create":
            if not (tc.name and tc.name.strip()):
                return f"candidate {tc.candidate_index}: action 'create' requires name"
            if tc.core_skill is None or not tc.core_skill.name.strip():
                return f"candidate {tc.candidate_index}: action 'create' requires core_skill"

    for ps in output.provisional_skills:
        pid = labels.resolve("skill", ps.skill)
        if ps.action in ("same", "other_topic_skill"):
            if ps.target_skill is None:
                return f"{ps.skill}: action '{ps.action}' requires target_skill"
            if labels.resolve("skill", ps.target_skill) == pid:
                return f"{ps.skill}: target_skill must differ from {ps.skill}"
        elif ps.action == "move" and ps.target_topic is None:
            return f"{ps.skill}: action 'move' requires target_topic"
        elif ps.action == "keep" and not prov_has_topic.get(pid, False):
            return (
                f"{ps.skill}: unassigned skill cannot use action 'keep'; "
                "use move/same/core_of_new_topic/other_topic_skill"
            )
        elif ps.action == "core_of_new_topic" and not (ps.topic_name and ps.topic_name.strip()):
            return f"{ps.skill}: action 'core_of_new_topic' requires topic_name"

    return None


def _select_topics_for_prompt(
    state: _StructureState,
    *,
    limit: int,
    priority_topic_ids: set[UUID],
) -> list[_TopicState]:
    if len(state.topics) <= limit:
        return list(state.topics)
    log.warning("structure_topics_truncated", total=len(state.topics), limit=limit)
    prioritized = [t for t in state.topics if t.topic_id in priority_topic_ids]
    others = [t for t in state.topics if t.topic_id not in priority_topic_ids]
    return (prioritized + others)[:limit]


def _render_structure_input(
    state: _StructureState,
    *,
    candidates_batch: Sequence[MaterialTopicCandidate],
    provs_batch: Sequence[_SkillState],
    shown_topics: Sequence[_TopicState],
    similar_by_cand: Sequence[Sequence[Candidate]],
    similar_by_prov: Sequence[Sequence[Candidate]],
    labels: LabelMap,
) -> tuple[str, str]:
    dom = f"Domain: {state.domain_name}\n" if state.domain_name else ""
    subject_parts: list[str] = []

    if candidates_batch:
        cand_lines: list[str] = ["Topic candidates:"]
        for idx, (cand, sims) in enumerate(zip(candidates_batch, similar_by_cand, strict=True)):
            builds = (
                "; ".join(f'{b.concept}: "{b.quote}"' for b in cand.builds_on)
                if cand.builds_on
                else "none"
            )
            sim_labels = [
                lbl for c in sims if (lbl := labels.label("skill", c.node_id)) is not None
            ]
            cand_lines.append(
                f"[{idx}] {cand.name}\n"
                f"  description: {cand.description}\n"
                f"  core_skill_name: {cand.core_skill_name}\n"
                f"  builds_on: {builds}\n"
                f"  similar_skills: {', '.join(sim_labels) if sim_labels else 'none'}"
            )
        subject_parts.append("\n".join(cand_lines))

    if provs_batch:
        prov_lines: list[str] = ["Provisional skills:"]
        for sk, sims in zip(provs_batch, similar_by_prov, strict=True):
            slbl = labels.label("skill", sk.skill_id) or str(sk.skill_id)
            cur_topic = state.topics_by_id.get(sk.topic_id) if sk.topic_id else None
            if cur_topic is not None:
                tlbl = labels.label("topic", cur_topic.topic_id)
                topic_desc = f"{tlbl} ({cur_topic.name})" if tlbl else cur_topic.name
                other_skills = [
                    f"{olbl} ({cur_topic.skill_names.get(oid, '')})"
                    for oid in cur_topic.skill_ids
                    if oid != sk.skill_id and (olbl := labels.label("skill", oid)) is not None
                ]
                other_str = ", ".join(other_skills) if other_skills else "none"
            else:
                topic_desc = "none (unassigned)"
                other_str = "none"
            sim_items = []
            for c in sims:
                if c.node_id == sk.skill_id or (
                    sk.topic_id is not None and c.topic_id == sk.topic_id
                ):
                    continue
                if (clbl := labels.label("skill", c.node_id)) is not None:
                    tl = labels.label("topic", c.topic_id) if c.topic_id else None
                    where = f", topic {tl}" if tl else ""
                    sim_items.append(f"{clbl} ({c.name}{where})")
            prov_lines.append(
                f"- {slbl}: {sk.name} | current_topic: {topic_desc} | "
                f"other_topic_skills: {other_str} | "
                f"similar_other_topic_skills: {', '.join(sim_items) if sim_items else 'none'}"
            )
        subject_parts.append("\n".join(prov_lines))

    subjects_text = dom + ("\n\n".join(subject_parts) if subject_parts else "(none)")

    topic_lines: list[str] = []
    listed_skills: set[UUID] = set()
    for t in shown_topics:
        tlbl = labels.label("topic", t.topic_id)
        if not tlbl:
            continue
        core_part = "none"
        if t.core_skill_id is not None and (clbl := labels.label("skill", t.core_skill_id)):
            core_part = f"{clbl}: {t.core_skill_name}"
            listed_skills.add(t.core_skill_id)
        desc = t.description or "none"
        topic_lines.append(
            f"{tlbl}: {t.name} [{t.status}] | core_skill: {core_part} | description: {desc}"
        )

    extra_skill_lines: list[str] = []
    for slbl, sid in labels.labels("skill").items():
        if sid in listed_skills:
            continue
        sk_state = state.skills_by_id.get(sid)
        if sk_state is None:
            continue
        tlbl = labels.label("topic", sk_state.topic_id) if sk_state.topic_id else None
        where = f" (topic {tlbl})" if tlbl else ""
        extra_skill_lines.append(f"{slbl}: {sk_state.name}{where}")

    cand_sections = []
    if topic_lines:
        cand_sections.append("Existing topics:\n" + "\n".join(topic_lines))
    else:
        cand_sections.append("Existing topics: (none)")
    if extra_skill_lines:
        cand_sections.append("Referenced skills:\n" + "\n".join(extra_skill_lines))
    candidates_text = "\n\n".join(cand_sections)
    return subjects_text, candidates_text


def _allocate_core_skill_id(
    state: _StructureState,
    learner_id: UUID,
    domain_id: UUID,
    topic_id: UUID,
    core_name: str,
) -> UUID:
    sid = node_id(learner_id, domain_id, "skill", core_name)
    existing = state.skills_by_id.get(sid)
    if existing is not None and existing.topic_id != topic_id:
        return uuid5(
            QUACK_UUID_NS,
            f"reconcile:{learner_id}:{domain_id}:skill:{normalize(core_name)}:{topic_id}",
        )
    return sid


async def _find_similar_skills(
    ctx: JobContext,
    *,
    learner_id: UUID,
    domain_id: UUID,
    subjects: Sequence[Subject],
) -> list[list[Candidate]]:
    if not subjects or ctx.neo4j is None or ctx.embeddings is None:
        return [[] for _ in subjects]
    raw = await find_candidates(ctx, learner_id=learner_id, domain_id=domain_id, subjects=subjects)
    return [[c for c in cands if c.domain_id == domain_id and c.kind == "skill"] for cands in raw]


async def _run_structure(
    ctx: JobContext,
    state: _StructureState,
    *,
    learner_id: UUID,
    domain_id: UUID,
    prompt: str,
    totals: _UsageTotals,
    candidates: Sequence[MaterialTopicCandidate] = (),
    include_provisional: bool = True,
    source_refs: Sequence[dict[str, Any]] = (),
    origin: Origin = "session",
) -> tuple[list[NewEvent], list[ResolvedStructureTopic]]:
    settings = ctx.settings
    provisional_list = state.provisional_skills() if include_provisional else []
    topics_in = len(state.topics)

    items: list[tuple[Literal["candidate", "provisional"], int, Any]] = [
        ("candidate", idx, cand) for idx, cand in enumerate(candidates)
    ]
    items.extend(("provisional", idx, sk) for idx, sk in enumerate(provisional_list))

    if not items:
        return [], []

    batch_size = max(1, settings.structure_batch_size)
    outcome_counts: Counter[str] = Counter(
        {
            "link": 0,
            "create": 0,
            "same": 0,
            "keep": 0,
            "move": 0,
            "core_of_new_topic": 0,
            "other_topic_skill": 0,
        }
    )
    edges_written: Counter[str] = Counter({"material": 0, "model": 0})
    edges_dropped: Counter[str] = Counter(
        {"score": 0, "reverse": 0, "both_directions": 0, "transitive": 0}
    )
    all_written: list[NewEvent] = []
    resolved_topics: dict[int, tuple[UUID, UUID]] = {}
    refs = list(source_refs)

    def make_event(event_type: str, payload: Any, key: str) -> NewEvent:
        return NewEvent(
            type=event_type,
            payload=payload,
            actor=prompt,
            idempotency_key=key,
            domain_id=domain_id,
            source_refs=refs,
        )

    for start in range(0, len(items), batch_size):
        chunk = items[start : start + batch_size]
        c_entries = [(idx, obj) for kind, idx, obj in chunk if kind == "candidate"]
        p_entries = [obj for kind, _, obj in chunk if kind == "provisional"]
        c_batch = [obj for _, obj in c_entries]

        subjects_for_sim = [
            Subject(
                name=cand.core_skill_name or cand.name,
                kind="skill",
                text=cand.description,
            )
            for cand in c_batch
        ] + [Subject(name=sk.name, kind="skill", text=sk.definition) for sk in p_entries]
        sim_results = await _find_similar_skills(
            ctx, learner_id=learner_id, domain_id=domain_id, subjects=subjects_for_sim
        )
        sim_cands = sim_results[: len(c_batch)]
        sim_provs = sim_results[len(c_batch) :]

        # Ensure skills discovered via vector search exist in state.skills_by_id for rendering.
        for sims in sim_results:
            for c in sims:
                if c.node_id not in state.skills_by_id:
                    state.skills_by_id[c.node_id] = _SkillState(
                        skill_id=c.node_id,
                        name=c.name,
                        status="active",
                        core=False,
                        topic_id=c.topic_id,
                    )

        priority_topics: set[UUID] = {sk.topic_id for sk in p_entries if sk.topic_id is not None}
        for sims in sim_results:
            for c in sims:
                if c.topic_id is not None:
                    priority_topics.add(c.topic_id)

        shown_topics = _select_topics_for_prompt(
            state,
            limit=settings.structure_full_list_max_topics,
            priority_topic_ids=priority_topics,
        )

        skill_ids_order: list[UUID] = []
        for t in shown_topics:
            if t.core_skill_id is not None:
                skill_ids_order.append(t.core_skill_id)
        for sk in p_entries:
            skill_ids_order.append(sk.skill_id)
            if sk.topic_id is not None and (ts := state.topics_by_id.get(sk.topic_id)) is not None:
                skill_ids_order.extend(ts.skill_ids)
        for sims in sim_results:
            skill_ids_order.extend(c.node_id for c in sims)
        unique_skills = list(dict.fromkeys(skill_ids_order))

        labels = LabelMap.build(
            settings,
            skills=unique_skills,
            topics=[t.topic_id for t in shown_topics],
            skills_limit=max(len(unique_skills), 1),
            topics_limit=max(len(shown_topics), settings.structure_full_list_max_topics),
        )

        subjects_text, candidates_text = _render_structure_input(
            state,
            candidates_batch=c_batch,
            provs_batch=p_entries,
            shown_topics=shown_topics,
            similar_by_cand=sim_cands,
            similar_by_prov=sim_provs,
            labels=labels,
        )
        digest = hashlib.sha256(f"{subjects_text}\n{candidates_text}".encode()).hexdigest()[:16]
        expected_prov_ids = {sk.skill_id for sk in p_entries}
        prov_has_topic = {sk.skill_id: sk.topic_id is not None for sk in p_entries}
        n_cands = len(c_batch)

        result = await ctx.llm.generate_structured(
            BUILDER_ROLE,
            int(prompt.rsplit("@", 1)[1]),
            StructureOutput,
            {"mode": STRUCTURE_MODE, "subjects": subjects_text, "candidates": candidates_text},
            learner_id,
            None,
            None,
            f"graph.structure:{domain_id}:{digest}",
            check=lambda out,
            lbls=labels,
            n_c=n_cands,
            exp_provs=expected_prov_ids,
            has_topic=prov_has_topic: _structure_problem(
                out, lbls, n_c, exp_provs, has_topic
            ),
        )
        totals.add(result.usage)

        batch_events: list[NewEvent] = []
        raw_requires: list[_RawRequires] = []

        # 1. Process topic_candidates
        tc_by_idx: dict[int, TopicCandidateDecision] = {}
        for tc in result.value.topic_candidates:
            tc_by_idx.setdefault(tc.candidate_index, tc)

        for local_idx, (global_idx, cand) in enumerate(c_entries):
            tc = tc_by_idx.get(local_idx)
            target_topic_id: UUID | None = None
            resolved_core_id: UUID | None = None
            used_action: str = "create"

            if tc is not None and tc.action == "link" and tc.target_topic is not None:
                try:
                    tid = labels.resolve("topic", tc.target_topic)
                    if tid in state.topics_by_id:
                        target_topic_id = tid
                        used_action = "link"
                except UnknownLabelError:
                    target_topic_id = None

            if target_topic_id is not None:
                ts = state.topics_by_id[target_topic_id]
                if ts.core_skill_id is not None:
                    resolved_core_id = ts.core_skill_id
                else:
                    # Legacy topic without core skill yet: create its single core=True skill.
                    spec = tc.core_skill if tc is not None else None
                    c_name = (spec.name if spec else cand.core_skill_name).strip() or ts.name
                    c_def = (spec.definition if spec else cand.description).strip()
                    c_ex = (spec.exemplar if spec else "").strip()
                    c_kind: SkillKind | None = spec.kind if spec else "procedure"
                    resolved_core_id = _allocate_core_skill_id(
                        state, learner_id, domain_id, target_topic_id, c_name
                    )
                    batch_events.append(
                        make_event(
                            "skill.created",
                            SkillCreated(
                                skill_id=resolved_core_id,
                                topic_id=target_topic_id,
                                domain_id=domain_id,
                                name=c_name,
                                summary=c_def or c_name,
                                status="active",
                                origin=origin,
                                core=True,
                                definition=c_def,
                                exemplar=c_ex,
                                kind=c_kind,
                            ),
                            f"structure:skill.created:{resolved_core_id}",
                        )
                    )
                    state.register_topic(
                        topic_id=target_topic_id,
                        name=ts.name,
                        status=ts.status,
                        description=ts.description,
                        core_skill_id=resolved_core_id,
                        core_skill_name=c_name,
                        core_definition=c_def,
                    )
            else:
                raw_name = (tc.name if tc and tc.name else cand.name).strip()
                clean_name = canonical_topic_name(raw_name, state.domain_name) or raw_name
                key = normalized_topic_key(clean_name, state.domain_name)
                existing_topic = state.topics_by_key.get(key)
                if existing_topic is not None:
                    target_topic_id = existing_topic.topic_id
                    used_action = "link"
                    if existing_topic.core_skill_id is not None:
                        resolved_core_id = existing_topic.core_skill_id
                    else:
                        spec = tc.core_skill if tc is not None else None
                        c_name = (spec.name if spec else cand.core_skill_name).strip() or clean_name
                        c_def = (spec.definition if spec else cand.description).strip()
                        c_ex = (spec.exemplar if spec else "").strip()
                        c_kind = spec.kind if spec else "procedure"
                        resolved_core_id = _allocate_core_skill_id(
                            state, learner_id, domain_id, target_topic_id, c_name
                        )
                        batch_events.append(
                            make_event(
                                "skill.created",
                                SkillCreated(
                                    skill_id=resolved_core_id,
                                    topic_id=target_topic_id,
                                    domain_id=domain_id,
                                    name=c_name,
                                    summary=c_def or c_name,
                                    status="active",
                                    origin=origin,
                                    core=True,
                                    definition=c_def,
                                    exemplar=c_ex,
                                    kind=c_kind,
                                ),
                                f"structure:skill.created:{resolved_core_id}",
                            )
                        )
                        state.register_topic(
                            topic_id=target_topic_id,
                            name=existing_topic.name,
                            status=existing_topic.status,
                            description=existing_topic.description,
                            core_skill_id=resolved_core_id,
                            core_skill_name=c_name,
                            core_definition=c_def,
                        )
                else:
                    used_action = "create"
                    target_topic_id = node_id(learner_id, domain_id, "topic", clean_name)
                    t_desc = (
                        tc.description if tc and tc.description is not None else cand.description
                    ).strip()
                    spec = tc.core_skill if tc is not None else None
                    c_name = (
                        spec.name if spec and spec.name.strip() else cand.core_skill_name
                    ).strip() or clean_name
                    c_def = (spec.definition if spec else cand.description).strip()
                    c_ex = (spec.exemplar if spec else "").strip()
                    c_kind = spec.kind if spec else "procedure"
                    resolved_core_id = _allocate_core_skill_id(
                        state, learner_id, domain_id, target_topic_id, c_name
                    )
                    batch_events.append(
                        make_event(
                            "topic.created",
                            TopicCreated(
                                topic_id=target_topic_id,
                                domain_id=domain_id,
                                name=clean_name,
                                origin=origin,
                                description=t_desc,
                            ),
                            f"structure:topic.created:{target_topic_id}",
                        )
                    )
                    batch_events.append(
                        make_event(
                            "skill.created",
                            SkillCreated(
                                skill_id=resolved_core_id,
                                topic_id=target_topic_id,
                                domain_id=domain_id,
                                name=c_name,
                                summary=c_def or c_name,
                                status="active",
                                origin=origin,
                                core=True,
                                definition=c_def,
                                exemplar=c_ex,
                                kind=c_kind,
                            ),
                            f"structure:skill.created:{resolved_core_id}",
                        )
                    )
                    state.register_topic(
                        topic_id=target_topic_id,
                        name=clean_name,
                        status="active",
                        description=t_desc,
                        core_skill_id=resolved_core_id,
                        core_skill_name=c_name,
                        core_definition=c_def,
                    )

            outcome_counts[used_action] += 1
            resolved_topics[global_idx] = (target_topic_id, resolved_core_id)

            if tc is not None:
                for req in tc.requires:
                    try:
                        to_skill_id = labels.resolve("skill", req.target_core_skill_label)
                    except UnknownLabelError:
                        continue
                    req_origin: Literal["material", "model"] = (
                        req.origin if cand.builds_on else "model"
                    )
                    raw_requires.append(
                        _RawRequires(
                            from_id=resolved_core_id,
                            to_id=to_skill_id,
                            score=req.score,
                            origin=req_origin,
                        )
                    )

        # 2. Process provisional_skills
        handled_provs: set[UUID] = set()
        for ps in result.value.provisional_skills:
            try:
                skill_id = labels.resolve("skill", ps.skill)
            except UnknownLabelError:
                continue
            if skill_id not in expected_prov_ids or skill_id in handled_provs:
                continue
            sk_state = state.skills_by_id.get(skill_id)
            if sk_state is None:
                continue
            handled_provs.add(skill_id)

            if ps.action == "same":
                if ps.target_skill is None:
                    continue
                try:
                    target_skill_id = labels.resolve("skill", ps.target_skill)
                except UnknownLabelError:
                    continue
                if target_skill_id == skill_id:
                    continue
                outcome_counts["same"] += 1
                batch_events.append(
                    make_event(
                        "skill.merged",
                        SkillMerged(from_id=skill_id, into_id=target_skill_id),
                        f"hygiene:skill.merged:{skill_id}:{target_skill_id}",
                    )
                )
                state.remove_skill(skill_id)

            elif ps.action == "other_topic_skill":
                if ps.target_skill is None:
                    continue
                try:
                    target_skill_id = labels.resolve("skill", ps.target_skill)
                except UnknownLabelError:
                    continue
                if target_skill_id == skill_id:
                    continue
                outcome_counts["other_topic_skill"] += 1
                old_topic = (
                    state.topics_by_id.get(sk_state.topic_id)
                    if sk_state.topic_id is not None
                    else None
                )
                target_sk = state.skills_by_id.get(target_skill_id)
                target_topic = (
                    state.topics_by_id.get(target_sk.topic_id)
                    if target_sk is not None and target_sk.topic_id is not None
                    else None
                )
                batch_events.append(
                    make_event(
                        "skill.merged",
                        SkillMerged(from_id=skill_id, into_id=target_skill_id),
                        f"hygiene:skill.merged:{skill_id}:{target_skill_id}",
                    )
                )
                state.remove_skill(skill_id)
                if old_topic is not None and old_topic.core_skill_id is not None:
                    dest_id = (
                        target_topic.core_skill_id
                        if target_topic is not None and target_topic.core_skill_id is not None
                        else target_skill_id
                    )
                    if old_topic.core_skill_id != dest_id:
                        raw_requires.append(
                            _RawRequires(
                                from_id=old_topic.core_skill_id,
                                to_id=dest_id,
                                score=1.0,
                                origin="model",
                            )
                        )

            elif ps.action == "keep":
                topic_id = sk_state.topic_id
                if topic_id is None and ps.target_topic is not None:
                    try:
                        topic_id = labels.resolve("topic", ps.target_topic)
                    except UnknownLabelError:
                        topic_id = None
                if topic_id is None:
                    continue
                outcome_counts["keep"] += 1
                s_def = (ps.definition or "").strip() or sk_state.definition
                s_ex = (ps.exemplar or "").strip()
                batch_events.append(
                    make_event(
                        "skill.created",
                        SkillCreated(
                            skill_id=skill_id,
                            topic_id=topic_id,
                            domain_id=domain_id,
                            name=sk_state.name,
                            summary=s_def or sk_state.name,
                            status="active",
                            origin="session",
                            core=False,
                            definition=s_def,
                            exemplar=s_ex,
                            kind=ps.kind,
                        ),
                        f"structure:skill.created:{skill_id}",
                    )
                )
                state.promote_skill(skill_id, topic_id=topic_id, core=False, definition=s_def)

            elif ps.action == "move":
                if ps.target_topic is None:
                    continue
                try:
                    target_topic_id = labels.resolve("topic", ps.target_topic)
                except UnknownLabelError:
                    continue
                if target_topic_id not in state.topics_by_id:
                    continue
                outcome_counts["move"] += 1
                s_def = (ps.definition or "").strip() or sk_state.definition
                s_ex = (ps.exemplar or "").strip()
                batch_events.append(
                    make_event(
                        "skill.placed",
                        SkillPlaced(
                            skill_id=skill_id, topic_id=target_topic_id, domain_id=domain_id
                        ),
                        f"structure:skill.placed:{skill_id}:{target_topic_id}",
                    )
                )
                batch_events.append(
                    make_event(
                        "skill.created",
                        SkillCreated(
                            skill_id=skill_id,
                            topic_id=target_topic_id,
                            domain_id=domain_id,
                            name=sk_state.name,
                            summary=s_def or sk_state.name,
                            status="active",
                            origin="session",
                            core=False,
                            definition=s_def,
                            exemplar=s_ex,
                            kind=ps.kind,
                        ),
                        f"structure:skill.created:{skill_id}",
                    )
                )
                state.promote_skill(
                    skill_id, topic_id=target_topic_id, core=False, definition=s_def
                )

            elif ps.action == "core_of_new_topic":
                raw_tname = (ps.topic_name or "").strip()
                clean_tname = canonical_topic_name(raw_tname, state.domain_name) or raw_tname
                if not clean_tname:
                    continue
                key = normalized_topic_key(clean_tname, state.domain_name)
                existing_topic = state.topics_by_key.get(key)
                spec = ps.core_skill
                c_name = (spec.name if spec and spec.name.strip() else sk_state.name).strip()
                c_def = (
                    spec.definition if spec else (ps.definition or "")
                ).strip() or sk_state.definition
                c_ex = (spec.exemplar if spec else (ps.exemplar or "")).strip()
                c_kind = spec.kind if spec else ps.kind

                if existing_topic is not None:
                    # Never create a second core=True skill for an existing topic.
                    target_topic_id = existing_topic.topic_id
                    is_core = existing_topic.core_skill_id is None
                    outcome_counts["move"] += 1
                    batch_events.append(
                        make_event(
                            "skill.placed",
                            SkillPlaced(
                                skill_id=skill_id, topic_id=target_topic_id, domain_id=domain_id
                            ),
                            f"structure:skill.placed:{skill_id}:{target_topic_id}",
                        )
                    )
                    batch_events.append(
                        make_event(
                            "skill.created",
                            SkillCreated(
                                skill_id=skill_id,
                                topic_id=target_topic_id,
                                domain_id=domain_id,
                                name=c_name,
                                summary=c_def or c_name,
                                status="active",
                                origin="session",
                                core=is_core,
                                definition=c_def,
                                exemplar=c_ex,
                                kind=c_kind,
                            ),
                            f"structure:skill.created:{skill_id}",
                        )
                    )
                    state.promote_skill(
                        skill_id,
                        topic_id=target_topic_id,
                        core=is_core,
                        name=c_name,
                        definition=c_def,
                    )
                else:
                    outcome_counts["core_of_new_topic"] += 1
                    new_topic_id = node_id(learner_id, domain_id, "topic", clean_tname)
                    t_desc = (ps.topic_description or c_def or "").strip()
                    batch_events.append(
                        make_event(
                            "topic.created",
                            TopicCreated(
                                topic_id=new_topic_id,
                                domain_id=domain_id,
                                name=clean_tname,
                                origin="session",
                                description=t_desc,
                            ),
                            f"structure:topic.created:{new_topic_id}",
                        )
                    )
                    batch_events.append(
                        make_event(
                            "skill.placed",
                            SkillPlaced(
                                skill_id=skill_id, topic_id=new_topic_id, domain_id=domain_id
                            ),
                            f"structure:skill.placed:{skill_id}:{new_topic_id}",
                        )
                    )
                    batch_events.append(
                        make_event(
                            "skill.created",
                            SkillCreated(
                                skill_id=skill_id,
                                topic_id=new_topic_id,
                                domain_id=domain_id,
                                name=c_name,
                                summary=c_def or c_name,
                                status="active",
                                origin="session",
                                core=True,
                                definition=c_def,
                                exemplar=c_ex,
                                kind=c_kind,
                            ),
                            f"structure:skill.created:{skill_id}",
                        )
                    )
                    state.register_topic(
                        topic_id=new_topic_id,
                        name=clean_tname,
                        status="active",
                        description=t_desc,
                        core_skill_id=skill_id,
                        core_skill_name=c_name,
                        core_definition=c_def,
                    )
                    state.promote_skill(
                        skill_id, topic_id=new_topic_id, core=True, name=c_name, definition=c_def
                    )

        # 3. Filter and emit REQUIRES edges
        accepted, batch_dropped = filter_requires_edges(
            raw_requires,
            state.requires_edges,
            min_score=settings.structure_requires_min_score,
        )
        edges_dropped.update(batch_dropped)
        for edge in accepted:
            edges_written[edge.origin] += 1
            confidence = settings.reconcile_requires_confidence[edge.origin]
            batch_events.append(
                make_event(
                    "edge.added",
                    EdgeAdded(
                        kind="requires",
                        from_id=edge.from_id,
                        to_id=edge.to_id,
                        origin=edge.origin,
                        confidence=confidence,
                    ),
                    f"requires:{edge.from_id}:{edge.to_id}:{edge.origin}",
                )
            )

        if batch_events:
            written = await append_unwritten(ctx.db, learner_id, batch_events)
            all_written.extend(written)

    resolved_list: list[ResolvedStructureTopic] = []
    for idx in range(len(candidates)):
        tid, cid = resolved_topics[idx]
        ts = state.topics_by_id.get(tid)
        skills_in_topic = tuple(dict.fromkeys([cid, *(ts.skill_ids if ts is not None else ())]))
        resolved_list.append(
            ResolvedStructureTopic(topic_id=tid, core_skill_id=cid, skill_ids=skills_in_topic)
        )

    log.info(
        "structure_writer_completed",
        learner_id=str(learner_id),
        domain_id=str(domain_id),
        topics_in=topics_in,
        candidates=len(candidates),
        provisional_skills=len(provisional_list),
        outcomes=dict(outcome_counts),
        edges_written=dict(edges_written),
        edges_dropped=dict(edges_dropped),
        calls=totals.calls,
        input_tokens=totals.input_tokens,
        output_tokens=totals.output_tokens,
        cached_tokens=totals.cached_tokens,
    )
    return all_written, resolved_list


# --- Public entry points ---------------------------------------------------------------------


async def resolve_material_structure(
    ctx: JobContext,
    *,
    learner_id: UUID,
    domain_id: UUID,
    candidates: Sequence[MaterialTopicCandidate],
    source_refs: Sequence[dict[str, Any]] = (),
    graph: DomainGraph | None = None,
) -> list[ResolvedStructureTopic]:
    """Resolve material topic candidates in Mode `Structure` (`builder`).
    Returns one `ResolvedStructureTopic` per candidate in the exact same order."""
    if not candidates:
        return []
    prompt = ctx.settings.llm_roles[BUILDER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'builder' has no prompt")
    if graph is None:
        graph = (
            await read_domain_graph(ctx.neo4j, learner_id=learner_id, domain_id=domain_id)
            if ctx.neo4j is not None
            else DomainGraph()
        )
    state = _StructureState(graph)
    totals = _UsageTotals()
    _, resolved = await _run_structure(
        ctx,
        state,
        learner_id=learner_id,
        domain_id=domain_id,
        prompt=prompt,
        totals=totals,
        candidates=candidates,
        include_provisional=False,
        source_refs=source_refs,
        origin="material",
    )
    return resolved


async def _run_deterministic_topic_cleanup(
    ctx: JobContext,
    state: _StructureState,
    *,
    learner_id: UUID,
    domain_id: UUID,
    prompt: str,
) -> list[NewEvent]:
    snapshot = [
        TopicNode(
            topic_id=t.topic_id,
            name=t.name,
            status=t.status,
            core_skill_id=t.core_skill_id,
            core_skill_name=t.core_skill_name,
            skill_ids=tuple(t.skill_ids),
        )
        for t in state.topics
    ]
    merges, renames = deterministic_topic_cleanup_plan(snapshot, domain_name=state.domain_name)
    events: list[NewEvent] = []
    for from_id, into_id in merges:
        events.append(
            NewEvent(
                type="topic.merged",
                payload=TopicMerged(from_id=from_id, into_id=into_id),
                actor=prompt,
                idempotency_key=f"hygiene:topic.merged:{from_id}:{into_id}",
                domain_id=domain_id,
            )
        )
        old_ts = state.topics_by_id.get(from_id)
        into_ts = state.topics_by_id.get(into_id)
        if old_ts is not None and into_ts is not None:
            for sid in old_ts.skill_ids:
                st = state.skills_by_id.get(sid)
                if st is not None:
                    if st.core and into_ts.core_skill_id is not None:
                        state.remove_skill(sid)
                    else:
                        state.promote_skill(sid, topic_id=into_id, core=st.core)
        state.remove_topic(from_id)
    for topic_id, clean_name in renames:
        if topic_id not in state.topics_by_id:
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
        state.topics_by_id[topic_id].name = clean_name
    if events:
        return await append_unwritten(ctx.db, learner_id, events)
    return []


async def run_hygiene(
    ctx: JobContext,
    graph: DomainGraph,
    *,
    learner_id: UUID,
    domain_id: UUID,
    session_id: UUID | None = None,
) -> list[str]:
    """The event types written, in order; nothing when there is no candidate."""
    prompt = ctx.settings.llm_roles[BUILDER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'builder' has no prompt")
    totals = _UsageTotals()
    state = _StructureState(graph)
    events: list[NewEvent] = []

    events.extend(
        await _cleanup_empty_provisional_skills(
            ctx,
            state,
            learner_id=learner_id,
            domain_id=domain_id,
            prompt=prompt,
            session_id=session_id,
        )
    )
    events.extend(
        await _run_deterministic_topic_cleanup(
            ctx, state, learner_id=learner_id, domain_id=domain_id, prompt=prompt
        )
    )
    events.extend(
        await _run_entity_hygiene(
            ctx,
            graph,
            state,
            learner_id=learner_id,
            domain_id=domain_id,
            prompt=prompt,
            totals=totals,
        )
    )
    structure_events, _ = await _run_structure(
        ctx,
        state,
        learner_id=learner_id,
        domain_id=domain_id,
        prompt=prompt,
        totals=totals,
        candidates=(),
        include_provisional=True,
    )
    events.extend(structure_events)

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
    needed) via `append_unwritten`. Returns the list of `(from_id, into_id)` merges written."""
    graph = await read_domain_graph(neo4j, learner_id=learner_id, domain_id=domain_id)
    merges, renames = deterministic_topic_cleanup_plan(graph.topics, domain_name=graph.domain_name)
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
        await append_unwritten(db, learner_id, events)
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
    raw_session = job.payload.get("session_id")
    session_id = UUID(str(raw_session)) if raw_session else None
    graph = await read_domain_graph(ctx.neo4j, learner_id=job.learner_id, domain_id=domain_id)
    await run_hygiene(
        ctx, graph, learner_id=job.learner_id, domain_id=domain_id, session_id=session_id
    )
