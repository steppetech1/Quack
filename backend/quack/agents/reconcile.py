"""Reconciliation of the graph — one `builder` call (ТЗ Ф4 WP16.3; Т-§4.3, Т-§4.4, Т-§4.6; `T-AG3`).
D-B-205, D-B-206.

The caller (material parse, WP20.2, the structure step) gives the subjects found in the text:
topics and skills with their words. Code prepares the candidates, the model only chooses:

- Candidates of a subject — at most `candidate_k`: the nearest topics and skills of the domain by
  `topic_emb` / `skill_emb`, graph neighbours of the nearest skills (`REQUIRES`, the same topic)
  and, for a skill, the nearest skills of the other domains (the candidates of `same_idea`).
  They get short labels (`S…`, `T…`, Т-§4.5); an unknown label goes back to the model once.
- One `builder` call in the Reconcile mode answers for every subject: `link(label)`, `create`,
  `suggest_merge(label)`, and for a new skill the same idea of another domain with a category.
- Code writes `topic.created`, `skill.created`, `edge.added` (`requires`, `same_idea`) and
  `skill.merge_suggested`. Ids of created nodes are `uuid5` of the normalized name, so a repeat
  writes the same events under the same keys and nothing twice (Т-§4.4: one name — one node).
  Never both `A → B` and `B → A` (Т-§4.6): the graph and the batch are checked.

The lock `domain:{id}` is the `lock_key` of the job that calls `reconcile_domain`; the function
is called only in the step «сверка и запись», after the heavy part of the parse (Т-§4.3). The
temporary entities of a session are reconciled by `graph.hygiene` (`session.ended`, D-B-185).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid5

from neo4j import AsyncDriver

from quack.agents.schemas import BuilderOutput, ReconciledItem
from quack.config import Settings
from quack.db.neo4j import cypher, read_session
from quack.events.event_types import (
    EdgeAdded,
    Origin,
    SkillCreated,
    SkillMergeSuggested,
    TopicCreated,
)
from quack.events.log import NewEvent, append_events
from quack.graph.ids import QUACK_UUID_NS
from quack.jobs.runner import JobContext
from quack.llm.labels import LabelKind, LabelMap, UnknownLabelError

BUILDER_ROLE = "builder"
RECONCILE_MODE = "Reconcile"

type Kind = Literal["topic", "skill"]
type Outcome = Literal["link", "create", "suggest_merge", "skipped"]

_TOPIC_LABEL = re.compile(r"^T\d+$")


@dataclass(frozen=True)
class Subject:
    """One thing found in the text. `text` is the words it is found in (a chunk, an episode);
    `topic` — the caller's name of the topic of a new skill, used when the model gives no hint;
    `requires` — names of skills of the same call this skill needs first (an explicit order of
    the material), written as `REQUIRES`."""

    name: str
    kind: Kind
    text: str = ""
    topic: str | None = None
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    kind: Kind
    node_id: UUID
    name: str
    domain_id: UUID
    domain_name: str | None = None
    topic_id: UUID | None = None


@dataclass(frozen=True)
class Resolution:
    """What became of a subject: `node_id` is the topic or skill it stands for (`None` when
    skipped — an unknown label, no topic for a new skill)."""

    subject: Subject
    outcome: Outcome
    node_id: UUID | None = None


@dataclass
class ReconcileResult:
    resolutions: list[Resolution] = field(default_factory=list)
    events: list[str] = field(default_factory=list)  # types written, in order


def normalize(name: str) -> str:
    return " ".join(name.casefold().split())


def node_id(learner_id: UUID, domain_id: UUID, kind: Kind, name: str) -> UUID:
    """Id of a node created by reconciliation: one normalized name — one node of the domain."""
    return uuid5(QUACK_UUID_NS, f"reconcile:{learner_id}:{domain_id}:{kind}:{normalize(name)}")


# --- candidates (code, Т-§4.3 step 1) -----------------------------------------------------

_TOPICS_NEAR = (
    "CYPHER 25 MATCH (t:Topic) SEARCH t IN (VECTOR INDEX topic_emb FOR $vector "
    "WHERE t.learnerId = $l AND t.domainId = $d LIMIT {reserve}) SCORE AS score "
    "RETURN t.id AS id, t.name AS name ORDER BY score DESC, t.id LIMIT $k"
)
_SKILLS_NEAR = (
    "CYPHER 25 MATCH (s:Skill) SEARCH s IN (VECTOR INDEX skill_emb FOR $vector "
    "WHERE s.learnerId = $l AND s.domainId = $d LIMIT {reserve}) SCORE AS score "
    "WHERE s.status IN ['provisional', 'active'] "
    "OPTIONAL MATCH (t:Topic)-[:COVERS]->(s) WHERE t.learnerId = $l "
    "WITH s, score, head(collect(t.id)) AS topic "
    "RETURN s.id AS id, s.name AS name, topic ORDER BY score DESC, s.id LIMIT $k"
)
_SKILLS_OTHER = (
    "CYPHER 25 MATCH (s:Skill) SEARCH s IN (VECTOR INDEX skill_emb FOR $vector "
    "WHERE s.learnerId = $l LIMIT {reserve}) SCORE AS score "
    "WHERE s.domainId <> $d AND s.status IN ['provisional', 'active'] "
    "OPTIONAL MATCH (dom:Domain) WHERE dom.id = s.domainId "
    "WITH s, score, head(collect(dom.name)) AS dname "
    "RETURN s.id AS id, s.name AS name, s.domainId AS domain, dname "
    "ORDER BY score DESC, s.id LIMIT $k"
)
_NEIGHBOURS = (
    "MATCH (s:Skill) WHERE s.learnerId = $l AND s.id IN $seeds "
    "OPTIONAL MATCH (s)-[:REQUIRES]-(n:Skill) WHERE n.learnerId = $l AND n.domainId = $d "
    "OPTIONAL MATCH (t:Topic)-[:COVERS]->(s) WHERE t.learnerId = $l "
    "OPTIONAL MATCH (t)-[:COVERS]->(m:Skill) WHERE m.learnerId = $l AND m.id <> s.id "
    "WITH collect(DISTINCT n) + collect(DISTINCT m) AS found UNWIND found AS x "
    "WITH DISTINCT x WHERE x IS NOT NULL AND x.status IN ['provisional', 'active'] "
    "OPTIONAL MATCH (t2:Topic)-[:COVERS]->(x) WHERE t2.learnerId = $l "
    "RETURN x.id AS id, x.name AS name, head(collect(t2.id)) AS topic ORDER BY x.id"
)
_REQUIRES = (
    "MATCH (a:Skill)-[:REQUIRES]->(b:Skill) WHERE a.learnerId = $l AND b.learnerId = $l "
    "AND a.id IN $ids AND b.id IN $ids RETURN a.id AS a, b.id AS b"
)

_NEAREST_SEEDS = 3


def _uuid(value: object) -> UUID | None:
    return UUID(str(value)) if value else None


async def _near(
    driver: AsyncDriver, query: str, settings: Settings, **params: Any
) -> list[Mapping[str, Any]]:
    reserve = max(int(settings.vector_search_reserve), settings.candidate_k)
    async with read_session(driver) as session:
        result = await session.run(cypher(query.format(reserve=reserve)), **params)
        return [dict(r) async for r in result]


async def find_candidates(
    ctx: JobContext, *, learner_id: UUID, domain_id: UUID, subjects: Sequence[Subject]
) -> list[list[Candidate]]:
    """Per subject, in the order of the subjects: at most `candidate_k` candidates. A third of
    the places is kept for the other kind and, for a skill, for the skills of other domains;
    the nearest skills bring their graph neighbours. `EmbeddingsUnavailable` goes up: the job
    waits (Т-§20.7) — without candidates the model would only create duplicates."""
    vectors = await asyncio.gather(
        *(ctx.embeddings.embed_query(f"{s.name}\n{s.text}".strip()) for s in subjects)
    )
    return [
        await _candidates_of(ctx, learner_id, domain_id, subject, vector)
        for subject, vector in zip(subjects, vectors, strict=True)
    ]


async def _candidates_of(
    ctx: JobContext, learner_id: UUID, domain_id: UUID, subject: Subject, vector: list[float]
) -> list[Candidate]:
    settings, driver = ctx.settings, ctx.neo4j
    k = settings.candidate_k
    base: dict[str, Any] = {"vector": vector, "l": str(learner_id), "d": str(domain_id), "k": k}
    topics = [
        Candidate("topic", UUID(str(r["id"])), r["name"] or "", domain_id)
        for r in await _near(driver, _TOPICS_NEAR, settings, **base)
    ]
    skill_rows = await _near(driver, _SKILLS_NEAR, settings, **base)
    skills = [
        Candidate("skill", UUID(str(r["id"])), r["name"] or "", domain_id, None, _uuid(r["topic"]))
        for r in skill_rows
    ]
    neighbours: list[Candidate] = []
    if skills:
        seeds = [str(c.node_id) for c in skills[:_NEAREST_SEEDS]]
        neighbours = [
            Candidate(
                "skill", UUID(str(r["id"])), r["name"] or "", domain_id, None, _uuid(r["topic"])
            )
            for r in await _near(driver, _NEIGHBOURS, settings, **{**base, "seeds": seeds})
        ]
    others: list[Candidate] = []
    if subject.kind == "skill":
        others = [
            Candidate(
                "skill", UUID(str(r["id"])), r["name"] or "", UUID(str(r["domain"])), r["dname"]
            )
            for r in await _near(driver, _SKILLS_OTHER, settings, **base)
        ]
    third = max(1, k // 3)
    own, other_kind = (skills, topics) if subject.kind == "skill" else (topics, skills)
    chosen: list[Candidate] = []
    seen: set[UUID] = set()

    def take(items: Sequence[Candidate], limit: int) -> None:
        taken = 0
        for item in items:
            if taken >= limit or len(chosen) >= k:
                return
            if item.node_id not in seen:
                seen.add(item.node_id)
                chosen.append(item)
                taken += 1

    take(own, k - 2 * third)
    take(other_kind, third)
    take(others, third)
    take(neighbours, k)
    take(own, k)
    take(other_kind, k)
    return chosen


async def read_requires(
    driver: AsyncDriver, *, learner_id: UUID, ids: Sequence[UUID]
) -> frozenset[tuple[UUID, UUID]]:
    """The `REQUIRES` pairs `(from, to)` that already stand between `ids` (Т-§4.6)."""
    if not ids:
        return frozenset()
    async with read_session(driver) as session:
        result = await session.run(cypher(_REQUIRES), l=str(learner_id), ids=[str(i) for i in ids])
        return frozenset([(UUID(str(r["a"])), UUID(str(r["b"]))) async for r in result])


# --- labels and the call -------------------------------------------------------------------


def _round_robin(lists: Sequence[Sequence[Candidate]], kind: Kind) -> list[UUID]:
    """Ids of `kind` in the order of importance: the first of every subject, then the second…"""
    per = [[c.node_id for c in cands if c.kind == kind] for cands in lists]
    depth = max((len(p) for p in per), default=0)
    return [p[i] for i in range(depth) for p in per if i < len(p)]


def build_labels(settings: Settings, candidates: Sequence[Sequence[Candidate]]) -> LabelMap:
    return LabelMap.build(
        settings,
        skills=_round_robin(candidates, "skill"),
        topics=_round_robin(candidates, "topic"),
    )


def _clean(text: str) -> str:
    return " ".join(text.split())


def render_input(
    domain_id: UUID,
    subjects: Sequence[Subject],
    candidates: Sequence[Sequence[Candidate]],
    labels: LabelMap,
) -> tuple[str, str]:
    """`(subjects, candidates)` blocks of the prompt: every candidate once, the subject line
    names the labels of its own candidates."""
    lines: list[str] = []
    table: dict[UUID, str] = {}
    for subject, cands in zip(subjects, candidates, strict=True):
        own: list[str] = []
        for c in cands:
            label = labels.label(c.kind, c.node_id)
            if label is None:
                continue
            own.append(label)
            if c.node_id not in table:
                where = ""
                if c.kind == "skill" and c.topic_id and (t := labels.label("topic", c.topic_id)):
                    where = f" (topic {t})"
                if c.domain_id != domain_id:
                    where = f" (domain: {c.domain_name or c.domain_id}; another domain)"
                table[c.node_id] = f"{label}: {_clean(c.name)}{where}"
        text = f" — {_clean(subject.text)}" if subject.text.strip() else ""
        lines.append(
            f"- {_clean(subject.name)} ({subject.kind}){text} — candidates: {', '.join(own) or '-'}"
        )
    return "\n".join(lines), "\n".join(table.values())


def _label_errors(output: BuilderOutput, labels: LabelMap) -> str | None:
    """What the schema cannot see: a label the table does not have (Т-§4.5)."""
    bad: list[str] = []
    for item in output.items:
        checks: list[tuple[str | None, LabelKind]] = [
            (item.target, item.kind),
            (item.same_idea, "skill"),
        ]
        for label, which in checks:
            if label is None:
                continue
            try:
                labels.resolve(which, label)
            except UnknownLabelError:
                bad.append(label)
        if item.topic_hint and _TOPIC_LABEL.match(item.topic_hint):
            try:
                labels.resolve("topic", item.topic_hint)
            except UnknownLabelError:
                bad.append(item.topic_hint)
    if bad:
        return "unknown labels: " + ", ".join(dict.fromkeys(bad)) + "; use only the given labels"
    return None


# --- the decision of the model into events (Т-§4.3 step 3) ----------------------------------


@dataclass
class _Plan:
    topics: dict[UUID, TopicCreated] = field(default_factory=dict)
    skills: dict[UUID, SkillCreated] = field(default_factory=dict)
    same_idea: list[tuple[UUID, UUID, str]] = field(default_factory=list)
    suggestions: list[tuple[UUID, UUID]] = field(default_factory=list)
    requires: list[tuple[UUID, UUID]] = field(default_factory=list)
    resolutions: list[Resolution] = field(default_factory=list)


def _decide(
    *,
    learner_id: UUID,
    domain_id: UUID,
    origin: Origin,
    subjects: Sequence[Subject],
    candidates: Sequence[Sequence[Candidate]],
    labels: LabelMap,
    output: BuilderOutput,
) -> _Plan:
    plan = _Plan()
    answers: dict[tuple[Kind, str], ReconciledItem] = {}
    for item in output.items:
        answers.setdefault((item.kind, normalize(item.name)), item)
    known: dict[UUID, Candidate] = {c.node_id: c for cands in candidates for c in cands}
    by_name: dict[tuple[Kind, str], UUID] = {
        (c.kind, normalize(c.name)): c.node_id for c in known.values() if c.domain_id == domain_id
    }
    topic_names: dict[str, UUID] = {n: i for (k, n), i in by_name.items() if k == "topic"}
    resolved: dict[tuple[Kind, str], UUID] = {}

    def topic_by_name(name: str) -> UUID:
        key = normalize(name)
        if key not in topic_names:
            tid = node_id(learner_id, domain_id, "topic", name)
            topic_names[key] = tid
            plan.topics.setdefault(
                tid,
                TopicCreated(
                    topic_id=tid, domain_id=domain_id, name=" ".join(name.split()), origin=origin
                ),
            )
        return topic_names[key]

    def local(kind: Kind, label: str | None) -> UUID | None:
        """An id of this domain by label; another domain or unknown — `None`."""
        if label is None:
            return None
        try:
            found = labels.resolve(kind, label)
        except UnknownLabelError:
            return None
        cand = known.get(found)
        return found if cand is not None and cand.domain_id == domain_id else None

    # topics first: their ids serve the hints of the skills
    for subject in subjects:
        if subject.kind != "topic":
            continue
        item = answers.get(("topic", normalize(subject.name)))
        if item is None:
            plan.resolutions.append(Resolution(subject, "skipped"))
            continue
        target = local("topic", item.target) if item.decision == "link" else None
        if item.decision == "link":
            outcome: Outcome = "link" if target is not None else "skipped"
            tid = target
        else:  # `suggest_merge` of topics has no event: the topic is created (D-B-205)
            exists = normalize(subject.name) in topic_names
            tid = topic_by_name(subject.name)
            outcome = "link" if exists else "create"
        if tid is not None:
            resolved[("topic", normalize(subject.name))] = tid
            topic_names.setdefault(normalize(subject.name), tid)
        plan.resolutions.append(Resolution(subject, outcome, tid))

    for subject in subjects:
        if subject.kind != "skill":
            continue
        key = ("skill", normalize(subject.name))
        item = answers.get(key)
        if item is None:
            plan.resolutions.append(Resolution(subject, "skipped"))
            continue
        if item.decision == "link":
            target = local("skill", item.target)
            if target is not None:
                resolved[key] = target
            plan.resolutions.append(
                Resolution(subject, "link" if target is not None else "skipped", target)
            )
            continue
        same_name = by_name.get(key)
        if same_name is not None:  # one normalized name — one skill of the domain (Т-§4.4)
            resolved[key] = same_name
            plan.resolutions.append(Resolution(subject, "link", same_name))
            continue
        hint = item.topic_hint
        topic_id: UUID | None = None
        if hint and _TOPIC_LABEL.match(hint):
            topic_id = local("topic", hint)
        elif hint:
            topic_id = topic_by_name(hint)
        if topic_id is None and subject.topic:
            topic_id = topic_by_name(subject.topic)
        if topic_id is None:
            plan.resolutions.append(Resolution(subject, "skipped"))
            continue
        skill_id = node_id(learner_id, domain_id, "skill", subject.name)
        plan.skills.setdefault(
            skill_id,
            SkillCreated(
                skill_id=skill_id,
                topic_id=topic_id,
                domain_id=domain_id,
                name=" ".join(subject.name.split()),
                summary=_clean(subject.text)[:300],
                status="active",
                origin=origin,
            ),
        )
        resolved[key] = skill_id
        outcome = "create"
        if item.decision == "suggest_merge":
            target = local("skill", item.target)
            if target is not None and target != skill_id:
                plan.suggestions.append((skill_id, target))
                outcome = "suggest_merge"
        elif item.same_idea is not None and item.same_idea_confidence is not None:
            try:
                other = labels.resolve("skill", item.same_idea)
            except UnknownLabelError:
                other = None
            cand = known.get(other) if other is not None else None
            if other is not None and cand is not None and cand.domain_id != domain_id:
                plan.same_idea.append((skill_id, other, item.same_idea_confidence))
        plan.resolutions.append(Resolution(subject, outcome, skill_id))

    for subject in subjects:
        own = resolved.get(("skill", normalize(subject.name)))
        if subject.kind != "skill" or own is None:
            continue
        for needed in subject.requires:
            other_id = resolved.get(("skill", normalize(needed)))
            if other_id is not None:
                plan.requires.append((own, other_id))
    return plan


def accepted_requires(
    pairs: Sequence[tuple[UUID, UUID]], existing: frozenset[tuple[UUID, UUID]]
) -> list[tuple[UUID, UUID]]:
    """The pairs without a loop, a repeat or a counter edge to a stored or earlier one (Т-§4.6)."""
    taken = set(existing)
    accepted: list[tuple[UUID, UUID]] = []
    for a, b in pairs:
        if a == b or (a, b) in taken or (b, a) in taken:
            continue
        taken.add((a, b))
        accepted.append((a, b))
    return accepted


def _events(
    plan: _Plan,
    *,
    settings: Settings,
    actor: str,
    domain_id: UUID,
    origin: Origin,
    requires: Sequence[tuple[UUID, UUID]],
    source_refs: list[dict[str, Any]],
) -> list[NewEvent]:
    def new(event_type: str, payload: Any, key: str) -> NewEvent:
        return NewEvent(
            type=event_type,
            payload=payload,
            actor=actor,
            idempotency_key=f"reconcile:{event_type}:{key}",
            domain_id=domain_id,
            source_refs=source_refs,
        )

    events = [new("topic.created", p, str(p.topic_id)) for p in plan.topics.values()]
    events += [new("skill.created", p, str(p.skill_id)) for p in plan.skills.values()]
    edge_origin: Literal["material", "model"] = "material" if origin == "material" else "model"
    for a, b in requires:
        confidence = settings.reconcile_requires_confidence[edge_origin]
        payload = EdgeAdded(
            kind="requires", from_id=a, to_id=b, origin=edge_origin, confidence=confidence
        )
        events.append(new("edge.added", payload, f"requires:{a}:{b}"))
    seen: set[frozenset[UUID]] = set()
    for a, b, category in plan.same_idea:
        if frozenset((a, b)) in seen:
            continue
        seen.add(frozenset((a, b)))
        payload = EdgeAdded(
            kind="same_idea",
            from_id=a,
            to_id=b,
            origin="model",
            confidence=settings.same_idea_confidence[category],
        )
        events.append(new("edge.added", payload, f"same_idea:{a}:{b}"))
    for a, b in plan.suggestions:
        events.append(
            new("skill.merge_suggested", SkillMergeSuggested(from_id=a, into_id=b), f"{a}:{b}")
        )
    return events


async def reconcile_with_candidates(
    ctx: JobContext,
    *,
    learner_id: UUID,
    domain_id: UUID,
    subjects: Sequence[Subject],
    candidates: Sequence[Sequence[Candidate]],
    origin: Origin = "material",
    existing_requires: frozenset[tuple[UUID, UUID]] | None = None,
    source_refs: Sequence[dict[str, Any]] = (),
) -> ReconcileResult:
    """The call and the writes for ready candidates. `existing_requires` — the stored `REQUIRES`
    pairs between the nodes involved; `None` reads them from the graph."""
    result = ReconcileResult()
    if not subjects:
        return result
    settings = ctx.settings
    labels = build_labels(settings, candidates)
    subject_block, candidate_block = render_input(domain_id, subjects, candidates, labels)
    prompt = settings.llm_roles[BUILDER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'builder' has no prompt")
    digest = hashlib.sha256(f"{subject_block}\n{candidate_block}".encode()).hexdigest()[:16]
    answer = await ctx.llm.generate_structured(
        BUILDER_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        BuilderOutput,
        {"mode": RECONCILE_MODE, "subjects": subject_block, "candidates": candidate_block},
        learner_id,
        None,
        None,
        f"graph.reconcile:{domain_id}:{digest}",
        check=lambda out: _label_errors(out, labels),
    )
    plan = _decide(
        learner_id=learner_id,
        domain_id=domain_id,
        origin=origin,
        subjects=subjects,
        candidates=candidates,
        labels=labels,
        output=answer.value,
    )
    result.resolutions = plan.resolutions
    if existing_requires is None:
        involved = list(dict.fromkeys(i for pair in plan.requires for i in pair))
        existing_requires = await read_requires(ctx.neo4j, learner_id=learner_id, ids=involved)
    events = _events(
        plan,
        settings=settings,
        actor=prompt,
        domain_id=domain_id,
        origin=origin,
        requires=accepted_requires(plan.requires, existing_requires),
        source_refs=list(source_refs),
    )
    if events:
        await append_events(ctx.db, learner_id, events)
    result.events = [e.type for e in events]
    return result


async def reconcile_domain(
    ctx: JobContext,
    *,
    learner_id: UUID,
    domain_id: UUID,
    subjects: Sequence[Subject],
    origin: Origin = "material",
    source_refs: Sequence[dict[str, Any]] = (),
) -> ReconcileResult:
    """Reconcile `subjects` with the graph of the domain: candidates by code, one `builder` call,
    events. Call it from the job that holds `lock_key = domain:{id}` (the step «сверка и запись»
    only); the heavy part of the parse stays outside the lock (Т-§4.3). One call serves up to
    about `candidate_k` subjects well — split a large material into batches, one call each."""
    if not subjects:
        return ReconcileResult()
    candidates = await find_candidates(
        ctx, learner_id=learner_id, domain_id=domain_id, subjects=subjects
    )
    return await reconcile_with_candidates(
        ctx,
        learner_id=learner_id,
        domain_id=domain_id,
        subjects=subjects,
        candidates=candidates,
        origin=origin,
        source_refs=source_refs,
    )
