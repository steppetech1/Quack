"""Neo4j part of `quack.graph.read` (ТЗ Ф4 WP6.4, WP17.1, WP17.4, WP8.3, WP15 п.2, WP16.2).

`ExtraReads` is a mixin: the class that inherits it provides `_neo4j` (a driver) and
`_settings`, and may set `_embedder` (free-text `similar_mistakes`) and `_db` (the event log
for `state_at`). Every query carries the learner (law 1). Reads that take only an
anchor id (`skill_history`, `prerequisites`, `prep_context`, `misconception_detail`) first read
the owner of the anchor and bind every other node to that `learnerId`. Reads of a learner's slice
use the watermark check of Т-§2.6 (`Learner.projectedSeq` before and after, up to
`snapshot_read_retries` repeats).
Nothing here writes to Neo4j or calls a model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from neo4j import AsyncDriver, Record

from quack.config import Settings
from quack.db import Db
from quack.db.neo4j import cypher
from quack.embeddings.client import EmbeddingClient, EmbeddingsUnavailable
from quack.events.log import StoredEvent as LogEvent
from quack.events.values import Level
from quack.graph.folds import MS_PER_DAY, retrievability
from quack.graph.memory import InMemoryGraph
from quack.graph.read.models import (
    CandidateSkill,
    EvidenceFact,
    ExposureFact,
    ExtractionCandidates,
    ManifestationFact,
    MisconceptionDetail,
    MisconceptionFact,
    PastSolution,
    PrepContext,
    PrerequisiteFact,
    RequiresLink,
    ReviewCandidate,
    ReviewReason,
    ReviewScope,
    SimilarMistake,
    StateAt,
)
from quack.graph.read.skill_state import (
    SKILL_PROJECTION,
    EvidenceRow,
    level_facts,
    skill_fact,
    source_of_tier,
)

_PROPER_STATUSES = ("suspected", "confirmed", "in_work", "watching")
_DETAIL_STATUSES = (*_PROPER_STATUSES, "unknown")
_OUTCOMES = frozenset({"correct", "incorrect", "partial", "unclear"})
_ASSISTANCE = frozenset({"none", "hint", "shown_solution"})
_EVENTS_UP_TO = (
    "SELECT learner_id, seq, id, type, schema_version, actor, domain_id, session_id, "
    "causation_seq, correlation_id, idempotency_key, payload_hash, occurred_at, payload, "
    "source_refs FROM core.events WHERE learner_id = %(learner_id)s AND seq <= %(seq)s "
    "ORDER BY seq"
)


def _uuid(value: object) -> UUID | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _float(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _to_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


@dataclass
class _SkillRow:
    props: dict[str, Any]
    topic_id: str | None
    in_hubs: set[str] = field(default_factory=set)


def _evidence_fact(row: Record | dict[str, Any]) -> EvidenceFact | None:
    evidence_id, skill_id = _uuid(row["id"]), _uuid(row["skill_id"])
    level = _int(row["level"])
    if evidence_id is None or skill_id is None or not 1 <= level <= 6:
        return None
    outcome = row["outcome"]
    assistance = row["assistance"]
    hint = _int(row["hint_step"])
    return EvidenceFact(
        evidence_id=evidence_id,
        skill_id=skill_id,
        level=level,  # type: ignore[arg-type]
        outcome=outcome if outcome in _OUTCOMES else "unclear",
        source=source_of_tier(_int(row["tier"])),
        assistance=assistance if assistance in _ASSISTANCE else "none",
        hint_step=min(3, hint) if hint > 0 else None,
        quote=_text(row["quote"]),
        task_id=_uuid(row["task_id"]),
        session_id=_uuid(row["session_id"]),
        seq=_int(row["seq"]),
        occurred_at_ms=_int(row["at"]),
    )


class ExtraReads:
    """`review_candidates`, `prep_context`, `skill_history`, `prerequisites`,
    `similar_mistakes`, `past_solution`, `state_at`, `extraction_candidates`,
    `misconception_detail` of `GraphReader`."""

    _neo4j: AsyncDriver
    _settings: Settings
    # Optional resources; the class that inherits sets them when it has them.
    _embedder: EmbeddingClient | None = None
    _db: Db | None = None  # the log of Phase 4: `state_at` replays it with `rules@3` (WP8.3)
    _bound: UUID | None = None  # the learner of a bound reader (D-A-144)

    # --- plumbing -----------------------------------------------------------------------

    async def _xrun(self, query: str, **params: Any) -> list[Record]:
        result = await self._neo4j.execute_query(cypher(query), parameters_=params, routing_="r")
        return list(result.records)

    async def _watermark(self, learner_id: str) -> int:
        rows = await self._xrun(
            "MATCH (l:Learner {id: $l}) RETURN l.projectedSeq AS seq", l=learner_id
        )
        return _int(rows[0]["seq"]) if rows else 0

    async def _xstable[T](self, learner_id: str, read: Callable[[], Awaitable[T]]) -> T:
        """Т-§2.6: watermark, slice, watermark; equal — consistent, otherwise again. When the
        retries run out the last read is returned (law 9: the answer never waits)."""
        attempts = 1 + max(0, self._settings.snapshot_read_retries)
        while True:
            attempts -= 1
            before = await self._watermark(learner_id)
            result = await read()
            if attempts <= 0 or before == await self._watermark(learner_id):
                return result

    async def _xowner(self, label: str, node_id: UUID) -> str | None:
        """`learnerId` of an anchor node; `label` comes from code constants only."""
        rows = await self._xrun(
            f"MATCH (n:{label} {{id: $id}}) RETURN n.learnerId AS learner", id=str(node_id)
        )
        return _text(rows[0]["learner"]) if rows else None

    async def _xevidence_rows(
        self, learner_id: str, skill_ids: Sequence[str]
    ) -> dict[str, list[EvidenceRow]]:
        rows = await self._xrun(
            "MATCH (e:Evidence)-[:ON]->(s:Skill) WHERE s.learnerId = $l AND e.learnerId = $l "
            "AND s.id IN $skills AND e.status = 'active' AND e.level >= 1 "
            "RETURN s.id AS skill, e.level AS level, e.tier AS tier, e.outcome AS outcome, "
            "e.atMs AS at, e.sourceSeq AS seq",
            l=learner_id,
            skills=list(skill_ids),
        )
        found: dict[str, list[EvidenceRow]] = {}
        for r in rows:
            found.setdefault(r["skill"], []).append(
                EvidenceRow(
                    _int(r["level"]),
                    _int(r["tier"]),
                    str(r["outcome"]),
                    _int(r["at"]),
                    _int(r["seq"]),
                )
            )
        return found

    # --- skill_history, prerequisites (Т-§10.4) -----------------------------------------

    async def skill_history(
        self, skill_id: UUID, level: Level | None = None, limit: int = 10
    ) -> list[EvidenceFact]:
        learner = await self._xowner("Skill", skill_id)
        if learner is None:
            return []

        async def read() -> list[EvidenceFact]:
            rows = await self._xrun(
                "MATCH (s:Skill {id: $skill}) WHERE s.learnerId = $l "
                "MATCH (e:Evidence)-[:ON]->(s) WHERE e.learnerId = $l AND e.status = 'active' "
                "AND e.level >= 1 AND ($level IS NULL OR e.level = $level) "
                "OPTIONAL MATCH (e)-[:ON_TASK]->(t:Task) WHERE t.learnerId = $l "
                "RETURN e.id AS id, s.id AS skill_id, e.level AS level, e.outcome AS outcome, "
                "e.tier AS tier, e.assistance AS assistance, e.hintStep AS hint_step, "
                "e.quote AS quote, t.id AS task_id, e.sourceSessionId AS session_id, "
                "e.sourceSeq AS seq, e.atMs AS at "
                "ORDER BY e.atMs DESC, e.sourceSeq DESC, e.id LIMIT $limit",
                skill=str(skill_id),
                l=learner,
                level=level,
                limit=limit,
            )
            facts = (_evidence_fact(r) for r in rows)
            return [f for f in facts if f is not None]

        return await self._xstable(learner, read)

    async def prerequisites(self, skill_id: UUID, limit: int = 10) -> list[PrerequisiteFact]:
        learner = await self._xowner("Skill", skill_id)
        if learner is None:
            return []

        async def read() -> list[PrerequisiteFact]:
            rows = await self._xrun(
                "MATCH (s:Skill {id: $skill}) WHERE s.learnerId = $l "
                "MATCH (s)-[r:REQUIRES]->(p:Skill) WHERE p.learnerId = $l "
                f"RETURN p {SKILL_PROJECTION} AS p, coalesce(r.confidence, 1.0) AS confidence "
                "ORDER BY confidence DESC, p.name, p.id LIMIT $limit",
                skill=str(skill_id),
                l=learner,
                limit=limit,
            )
            evidence = await self._xevidence_rows(learner, [r["p"]["id"] for r in rows])
            return [
                PrerequisiteFact(
                    skill_id=UUID(r["p"]["id"]),
                    confidence=min(1.0, max(0.0, float(r["confidence"]))),
                    levels=level_facts(r["p"], evidence.get(r["p"]["id"], [])),
                )
                for r in rows
            ]

        return await self._xstable(learner, read)

    # --- similar_mistakes, past_solution (Т-§10.4, Т-§3.4.6) ----------------------------

    async def similar_mistakes(
        self, learner_id: UUID, evidence_id: UUID | None = None, text: str | None = None
    ) -> list[SimilarMistake]:
        """Past mistakes nearest by `evidence_emb`, other domains included. A text needs the
        query embedding: without `_embedder` it raises `EmbeddingsUnavailable` (Т-§11.9)."""
        if (evidence_id is None) == (text is None):
            raise ValueError("give exactly one of evidence_id and text")
        lid = str(learner_id)
        vector: list[float] | None
        if evidence_id is not None:
            rows = await self._xrun(
                "MATCH (e:Evidence {id: $id}) WHERE e.learnerId = $l RETURN e.embedding AS v",
                id=str(evidence_id),
                l=lid,
            )
            vector = list(rows[0]["v"]) if rows and rows[0]["v"] is not None else None
        else:
            if self._embedder is None:
                raise EmbeddingsUnavailable("no embedding client")
            vector = await self._embedder.embed_query(text or "")
        if not vector:
            return []
        reserve = max(1, int(self._settings.vector_search_reserve))
        k = self._settings.memory_tool_limits.get("similar_mistakes", 5)
        query = (
            "CYPHER 25 MATCH (e:Evidence) "
            "SEARCH e IN (VECTOR INDEX evidence_emb FOR $vector "
            f"WHERE e.learnerId = $l AND e.searchable = true LIMIT {reserve}) SCORE AS score "
            "WHERE e.outcome <> 'correct' AND ($exclude IS NULL OR e.id <> $exclude) "
            "MATCH (e)-[:ON]->(s:Skill) WHERE s.learnerId = $l "
            "RETURN e.id AS id, s.id AS skill, e.domainId AS domain, e.quote AS quote, score "
            "ORDER BY score DESC, e.id LIMIT $k"
        )

        async def read() -> list[SimilarMistake]:
            found = await self._xrun(
                query,
                vector=vector,
                l=lid,
                exclude=str(evidence_id) if evidence_id is not None else None,
                k=k,
            )
            result: list[SimilarMistake] = []
            for r in found:
                evidence, skill = _uuid(r["id"]), _uuid(r["skill"])
                if evidence is None or skill is None:
                    continue
                result.append(
                    SimilarMistake(
                        evidence_id=evidence,
                        skill_id=skill,
                        domain_id=_uuid(r["domain"]),
                        quote=_text(r["quote"]),
                    )
                )
            return result

        return await self._xstable(lid, read)

    async def past_solution(
        self, learner_id: UUID, task_id: UUID | None = None, skill_id: UUID | None = None
    ) -> list[PastSolution]:
        """The learner's own words on a task: quotes of its active evidence, per task and
        skill, latest first. Canvas lines live in Postgres, not in the graph (Т-§3.4.1)."""
        lid = str(learner_id)
        limit = self._settings.memory_tool_limits.get("past_solution", 2)

        async def read() -> list[PastSolution]:
            rows = await self._xrun(
                "MATCH (e:Evidence)-[:ON]->(s:Skill) WHERE e.learnerId = $l AND s.learnerId = $l "
                "AND e.status = 'active' AND e.quote IS NOT NULL AND e.quote <> '' "
                "AND ($skill IS NULL OR s.id = $skill) "
                "OPTIONAL MATCH (e)-[:ON_TASK]->(t:Task) WHERE t.learnerId = $l "
                "WITH e, s, t WHERE $task IS NULL OR t.id = $task "
                "RETURN t.id AS task, s.id AS skill, e.quote AS quote, e.atMs AS at, "
                "e.sourceSeq AS seq ORDER BY e.atMs, e.sourceSeq, e.id",
                l=lid,
                task=str(task_id) if task_id is not None else None,
                skill=str(skill_id) if skill_id is not None else None,
            )
            groups: dict[tuple[str | None, str], list[Record]] = {}
            for r in rows:
                groups.setdefault((r["task"], r["skill"]), []).append(r)
            ordered = sorted(
                groups.items(),
                key=lambda item: (-max(_int(r["at"]) for r in item[1]), str(item[0])),
            )
            solutions: list[PastSolution] = []
            for (task, skill), items in ordered[:limit]:
                skill_uuid = _uuid(skill)
                if skill_uuid is None:
                    continue
                solutions.append(
                    PastSolution(
                        task_id=_uuid(task),
                        skill_id=skill_uuid,
                        lines=[str(r["quote"]) for r in items],
                        occurred_at_ms=max(_int(r["at"]) for r in items),
                    )
                )
            return solutions

        return await self._xstable(lid, read)

    # --- misconceptions (Т-§9.3) --------------------------------------------------------

    async def _misconception_facts(
        self, learner_id: str, skill_ids: Sequence[str]
    ) -> list[MisconceptionFact]:
        if not skill_ids:
            return []
        rows = await self._xrun(
            "MATCH (m:Misconception)-[:ABOUT]->(s:Skill) WHERE m.learnerId = $l "
            "AND s.learnerId = $l AND s.id IN $skills AND m.status IN $statuses "
            "OPTIONAL MATCH (m)-[:CONFUSED_WITH]->(c:Skill) WHERE c.learnerId = $l "
            "OPTIONAL MATCH (e:Evidence)-[:MANIFESTS]->(m) "
            "WHERE e.learnerId = $l AND e.status = 'active' "
            "WITH m, s, c, e ORDER BY e.atMs DESC, e.sourceSeq DESC "
            "WITH m, s, c, collect(e) AS es "
            "RETURN m.id AS id, m.status AS status, m.statement AS statement, "
            "m.triggers AS triggers, s.id AS skill, c.id AS confused, size(es) AS n, "
            "[x IN es WHERE coalesce(x.quote, '') <> '' | x.quote][..2] AS quotes "
            "ORDER BY m.id",
            l=learner_id,
            skills=list(skill_ids),
            statuses=list(_PROPER_STATUSES),
        )
        facts: list[MisconceptionFact] = []
        for r in rows:
            misconception, skill = _uuid(r["id"]), _uuid(r["skill"])
            if misconception is None or skill is None:
                continue
            facts.append(
                MisconceptionFact(
                    misconception_id=misconception,
                    skill_id=skill,
                    status=r["status"],
                    statement=str(r["statement"] or ""),
                    triggers=[str(t) for t in (r["triggers"] or [])],
                    confused_with_skill_id=_uuid(r["confused"]),
                    manifestation_count=_int(r["n"]),
                    quotes=[str(q) for q in r["quotes"]],
                )
            )
        return facts

    async def misconception_detail(self, misconception_id: UUID) -> MisconceptionDetail | None:
        """The id goes through `Alias`; a misconception that is not live (`dismissed`, `fixed`,
        `disputed`) is `None`, like a missing one: nothing to describe."""
        anchor = await self._xrun(
            "MATCH (m:Misconception {id: $id}) RETURN m.id AS mid, m.learnerId AS learner "
            "UNION MATCH (:Alias {id: $id})-[:ALIAS_OF*1..5]->(m:Misconception) "
            "RETURN m.id AS mid, m.learnerId AS learner",
            id=str(misconception_id),
        )
        if not anchor:
            return None
        mid, learner = str(anchor[0]["mid"]), str(anchor[0]["learner"])
        if self._bound is not None and learner != str(self._bound):
            return None  # D-A-144: a reader bound to a learner does not read another's nodes

        async def read() -> MisconceptionDetail | None:
            head = await self._xrun(
                "MATCH (m:Misconception {id: $mid}) WHERE m.learnerId = $l "
                "OPTIONAL MATCH (m)-[:ABOUT]->(s:Skill) WHERE s.learnerId = $l "
                "RETURN m.status AS status, m.statement AS statement, m.triggers AS triggers, "
                "s.id AS skill, s.name AS skill_name",
                mid=mid,
                l=learner,
            )
            if not head or head[0]["status"] not in _DETAIL_STATUSES:
                return None
            skill = _uuid(head[0]["skill"])
            if skill is None:
                return None
            rows = await self._xrun(
                "MATCH (e:Evidence)-[:MANIFESTS]->(m:Misconception {id: $mid}) "
                "WHERE m.learnerId = $l AND e.learnerId = $l AND e.status = 'active' "
                "OPTIONAL MATCH (e)-[:ON_TASK]->(t:Task) WHERE t.learnerId = $l "
                "RETURN e.id AS id, e.quote AS quote, t.id AS task, e.sourceSeq AS seq, "
                "e.atMs AS at, e.tier AS tier ORDER BY e.sourceSeq, e.id",
                mid=mid,
                l=learner,
            )
            manifestations = [
                ManifestationFact(
                    evidence_id=evidence,
                    quote=_text(r["quote"]),
                    task_id=_uuid(r["task"]),
                    source_seq=_int(r["seq"]),
                    at_ms=_int(r["at"]),
                    tier=source_of_tier(_int(r["tier"])),
                )
                for r in rows
                if (evidence := _uuid(r["id"])) is not None
            ]
            return MisconceptionDetail(
                misconception_id=UUID(mid),
                status=head[0]["status"],
                statement=str(head[0]["statement"] or ""),
                triggers=[str(t) for t in (head[0]["triggers"] or [])],
                skill_id=skill,
                skill_name=str(head[0]["skill_name"] or ""),
                manifestations=manifestations,
            )

        return await self._xstable(learner, read)

    # --- extraction_candidates (WP15 п.2, D-A-135) --------------------------------------

    async def extraction_candidates(
        self,
        learner_id: UUID,
        session_id: UUID,
        episode_vector: Sequence[float] | None,
        k: int,
        *,
        topic_id: UUID | None = None,
        skill_ids: Sequence[UUID] | None = None,
    ) -> ExtractionCandidates:
        lid = str(learner_id)

        async def read() -> ExtractionCandidates:
            skills = await self._candidate_skills(
                lid, str(session_id), episode_vector, k, topic_id, skill_ids
            )
            misconceptions = await self._misconception_facts(lid, [str(c.skill_id) for c in skills])
            return ExtractionCandidates(skills=skills, misconceptions=misconceptions)

        return await self._xstable(lid, read)

    async def _candidate_skills(
        self,
        lid: str,
        session: str,
        vector: Sequence[float] | None,
        k: int,
        topic_id: UUID | None,
        skill_ids: Sequence[UUID] | None,
    ) -> list[CandidateSkill]:
        seeds = {
            r["id"]
            for r in await self._xrun(
                "MATCH (e:Evidence)-[:ON]->(s:Skill) WHERE e.learnerId = $l AND s.learnerId = $l "
                "AND e.sourceSessionId = $session AND e.status = 'active' RETURN s.id AS id "
                "UNION MATCH (m:Misconception)-[:ABOUT]->(s:Skill) WHERE m.learnerId = $l "
                "AND s.learnerId = $l AND m.targetedInSessionId = $session RETURN s.id AS id",
                l=lid,
                session=session,
            )
        }
        info: dict[str, CandidateSkill] = {}

        def remember(rows: Sequence[Record]) -> None:
            for r in rows:
                skill, topic = _uuid(r["id"]), _uuid(r["topic"])
                if skill is not None and topic is not None:
                    info.setdefault(
                        str(skill),
                        CandidateSkill(skill_id=skill, name=str(r["name"] or ""), topic_id=topic),
                    )

        if skill_ids is not None or topic_id is not None:
            # Re-extraction: the pool is the topic or the listed skills, nothing else.
            remember(
                await self._xrun(
                    "MATCH (t:Topic)-[:COVERS]->(s:Skill) WHERE t.learnerId = $l "
                    "AND s.learnerId = $l AND ($topic IS NULL OR t.id = $topic) "
                    "AND ($skills IS NULL OR s.id IN $skills) "
                    "RETURN s.id AS id, s.name AS name, t.id AS topic",
                    l=lid,
                    topic=str(topic_id) if topic_id is not None else None,
                    skills=[str(s) for s in skill_ids] if skill_ids is not None else None,
                )
            )
            ordered = sorted(info, key=lambda sid: (sid not in seeds, info[sid].name, sid))
            # Skills named by the caller are all kept; a topic is capped like any pool.
            return [info[sid] for sid in (ordered if skill_ids is not None else ordered[:k])]

        if seeds:
            remember(
                await self._xrun(
                    "MATCH (seed:Skill) WHERE seed.learnerId = $l AND seed.id IN $seeds "
                    "MATCH (t:Topic)-[:COVERS]->(seed) WHERE t.learnerId = $l "
                    "MATCH (t)-[:COVERS]->(s:Skill) WHERE s.learnerId = $l "
                    "RETURN DISTINCT s.id AS id, s.name AS name, t.id AS topic",
                    l=lid,
                    seeds=sorted(seeds),
                )
            )
        order: list[str] = [
            sid for sid in sorted(info, key=lambda s: (info[s].name, s)) if sid in seeds
        ]
        if vector:
            reserve = max(int(self._settings.vector_search_reserve), k)
            near = await self._xrun(
                "CYPHER 25 MATCH (s:Skill) SEARCH s IN (VECTOR INDEX skill_emb FOR $vector "
                f"WHERE s.learnerId = $l LIMIT {reserve}) SCORE AS score "
                "MATCH (t:Topic)-[:COVERS]->(s) WHERE t.learnerId = $l "
                "RETURN s.id AS id, s.name AS name, t.id AS topic "
                "ORDER BY score DESC, s.id LIMIT $k",
                vector=list(vector),
                l=lid,
                k=k,
            )
            remember(near)
            order += [sid for r in near if (sid := str(r["id"])) in info and sid not in order]
        order += [sid for sid in sorted(info, key=lambda s: (info[s].name, s)) if sid not in order]
        return [info[sid] for sid in order[:k]]

    # --- review_candidates (WP17.1, Т-§14.1) --------------------------------------------

    async def review_candidates(
        self, learner_id: UUID, now_ms: int, scope: ReviewScope | None = None
    ) -> list[ReviewCandidate]:
        lid = str(learner_id)
        chosen = scope or ReviewScope()
        return await self._xstable(lid, lambda: self._review(lid, now_ms, chosen))

    async def _review(self, lid: str, now_ms: int, scope: ReviewScope) -> list[ReviewCandidate]:
        cfg = self._settings
        skills = await self._skill_rows(lid)
        hubs = await self._hub_coverage(lid)
        for hub_id, hub in hubs.items():
            for topic_id in hub.topics:
                for row in skills.values():
                    if row.topic_id == topic_id:
                        row.in_hubs.add(hub_id)
        rechecks = await self._rechecks(lid, now_ms)
        needed = await self._goal_prerequisites(lid, scope.goal_skill_ids)
        last_practice: dict[str, int] = {}
        for row in skills.values():
            domain = str(row.props.get("domainId") or "")
            last_practice[domain] = max(
                last_practice.get(domain, 0), _int(row.props.get("lastPracticeAtMs"))
            )
        idle_ms = cfg.forgetting_domain_idle_days * MS_PER_DAY
        found: list[ReviewCandidate] = []
        for skill_id, row in skills.items():
            props = row.props
            if scope.topic_id is not None and row.topic_id != str(scope.topic_id):
                continue
            if scope.hub_id is not None and str(scope.hub_id) not in row.in_hubs:
                continue
            reasons: list[ReviewReason] = []
            stability = float(props.get("retStability") or 0.0)
            last_recall = _int(props.get("retLastRecallAtMs"))
            due = _int(props.get("retDueAtMs"))
            recall = (
                retrievability(stability, max(0, now_ms - last_recall))
                if stability > 0 and last_recall > 0
                else None
            )
            if (recall is not None and recall < cfg.review_trigger_retention) or (
                0 < due <= now_ms
            ):
                reasons.append("retention")
            if props.get("needsConsolidation") is True:
                reasons.append("consolidation")
            if skill_id in rechecks:
                reasons.append("misconception_recheck")
            deadline_ms = self._unmet_deadline(props, row, hubs, now_ms)
            if deadline_ms is not None:
                reasons.append("hub_requirement")
            need = 1.0 if skill_id in needed else 0.0
            if not reasons:
                continue
            domain = str(props.get("domainId") or "")
            idle = now_ms - last_practice.get(domain, 0) > idle_ms
            if need == 0.0 and deadline_ms is None and idle:
                continue  # allowed forgetting (П-7.4)
            days_left = (deadline_ms - now_ms) / MS_PER_DAY if deadline_ms is not None else 0.0
            priority = (
                cfg.review_weight_need * need
                + cfg.review_weight_deadline * (1.0 / (1.0 + days_left) if deadline_ms else 0.0)
                + cfg.review_weight_forgetting * (1.0 - recall if recall is not None else 0.0)
                + cfg.review_weight_consolidation
                * (1.0 if "consolidation" in reasons or "misconception_recheck" in reasons else 0.0)
            )
            found.append(
                ReviewCandidate(
                    skill_id=UUID(skill_id),
                    priority=priority,
                    reasons=reasons,
                    is_goal_prerequisite=need > 0,
                )
            )
        found.sort(key=lambda c: (-c.priority, str(c.skill_id)))
        return found[: scope.max_items] if scope.max_items is not None else found

    @staticmethod
    def _unmet_deadline(
        props: dict[str, Any], row: _SkillRow, hubs: dict[str, _HubCoverage], now_ms: int
    ) -> int | None:
        """The nearest future deadline of a hub whose required level of this skill is not
        `proven` / `implied` (Т-§14.1); `None` — no such hub."""
        nearest: int | None = None
        for hub_id in row.in_hubs:
            hub = hubs[hub_id]
            if hub.deadline_ms <= now_ms:
                continue
            levels = hub.required.get(row.topic_id or "", [])
            if any(props.get(f"l{n}Status") not in ("proven", "implied") for n in levels) and (
                nearest is None or hub.deadline_ms < nearest
            ):
                nearest = hub.deadline_ms
        return nearest

    async def _skill_rows(self, lid: str) -> dict[str, _SkillRow]:
        rows = await self._xrun(
            "MATCH (s:Skill) WHERE s.learnerId = $l "
            "OPTIONAL MATCH (t:Topic)-[:COVERS]->(s) WHERE t.learnerId = $l "
            f"RETURN s {SKILL_PROJECTION} AS s, t.id AS topic",
            l=lid,
        )
        return {r["s"]["id"]: _SkillRow(dict(r["s"]), _text(r["topic"])) for r in rows}

    async def _hub_coverage(self, lid: str) -> dict[str, _HubCoverage]:
        """Hubs of the learner with their topics and required levels. `domain_all` stores no
        composition: it is every non-hidden topic of its domain (Т-§4.8)."""
        defaults = self._settings.control_type_levels
        hubs: dict[str, _HubCoverage] = {}
        listed = await self._xrun(
            "MATCH (h:Hub)-[i:INCLUDES]->(t:Topic) WHERE h.learnerId = $l AND t.learnerId = $l "
            "AND coalesce(t.status, 'active') <> 'hidden' "
            "RETURN h.id AS hub, h.deadlineAtMs AS deadline, h.controlType AS control, "
            "t.id AS topic, i.requiredLevels AS levels",
            l=lid,
        )
        whole = await self._xrun(
            "MATCH (d:Domain)-[:HAS_HUB]->(h:Hub) WHERE h.learnerId = $l AND h.kind = 'domain_all' "
            "MATCH (d)-[:HAS_TOPIC]->(t:Topic) WHERE t.learnerId = $l "
            "AND coalesce(t.status, 'active') <> 'hidden' "
            "RETURN h.id AS hub, h.deadlineAtMs AS deadline, h.controlType AS control, "
            "t.id AS topic, null AS levels",
            l=lid,
        )
        for r in [*listed, *whole]:
            hub = hubs.setdefault(r["hub"], _HubCoverage(_int(r["deadline"])))
            levels = r["levels"]
            if not levels:
                levels = defaults.get(str(r["control"] or "no_goal"), defaults.get("no_goal", []))
            hub.topics.append(r["topic"])
            hub.required[r["topic"]] = sorted(int(n) for n in levels)
        return hubs

    async def _rechecks(self, lid: str, now_ms: int) -> set[str]:
        window = self._settings.watch_days * MS_PER_DAY
        rows = await self._xrun(
            "MATCH (m:Misconception)-[:ABOUT]->(s:Skill) WHERE m.learnerId = $l "
            "AND s.learnerId = $l AND m.status = 'watching' "
            "RETURN s.id AS skill, m.watchSinceMs AS since",
            l=lid,
        )
        return {r["skill"] for r in rows if _int(r["since"]) + window <= now_ms}

    async def _goal_prerequisites(self, lid: str, goal: Sequence[UUID]) -> set[str]:
        if not goal:
            return set()
        rows = await self._xrun(
            "MATCH (g:Skill) WHERE g.learnerId = $l AND g.id IN $goal "
            "MATCH (g)-[r:REQUIRES*1..2]->(p:Skill) WHERE p.learnerId = $l "
            "AND all(x IN r WHERE x.confidence >= $min) RETURN DISTINCT p.id AS id",
            l=lid,
            goal=[str(g) for g in goal],
            min=self._settings.requires_min_confidence,
        )
        return {r["id"] for r in rows}

    # --- prep_context (WP17.4, Т-§14.7) -------------------------------------------------

    async def prep_context(self, hub_id: UUID, now_ms: int) -> PrepContext:
        """Slot 6 data. `control_materials` stays empty: the graph keeps no kind of a control
        material (sample exam, past variant, …) — see D-A-152."""
        learner = await self._xowner("Hub", hub_id)
        if learner is None:
            return PrepContext(hub_id=hub_id, ready_count=0, total_count=0)

        async def read() -> PrepContext:
            hubs = await self._hub_coverage(learner)
            hub = hubs.get(str(hub_id))
            if hub is None:
                return PrepContext(hub_id=hub_id, ready_count=0, total_count=0)
            skills = await self._skill_rows(learner)
            ready = [t for t in hub.topics if _topic_ready(t, hub.required[t], skills)]
            days = (
                max(0, -(-(hub.deadline_ms - now_ms) // MS_PER_DAY))
                if hub.deadline_ms > 0
                else None
            )
            candidates = await self._review(learner, now_ms, ReviewScope(hub_id=hub_id))
            return PrepContext(
                hub_id=hub_id,
                days_to_deadline=days,
                ready_count=len(ready),
                total_count=len(hub.topics),
                coverage_topic_ids=[UUID(t) for t in hub.topics],
                candidates=candidates,
            )

        return await self._xstable(learner, read)

    # --- state_at (WP8.3, Т-§2.6) -------------------------------------------------------

    async def state_at(self, learner_id: UUID, seq_n: int, skill_ids: Sequence[UUID]) -> StateAt:
        """Events of the learner up to N replayed by the projector's own handlers and folds;
        never a fold over `seq ≤ N` of the stored evidence (late disputes are not applied)."""
        events = await self._events_up_to(learner_id, seq_n)
        graph = await self._replay(learner_id, events)
        as_of = _to_ms(events[-1].occurred_at) if events else 0
        aliases = graph.alias_map()
        facts = []
        for wanted in skill_ids:
            skill_id = aliases.get(str(wanted), str(wanted))
            props = graph.get("Skill", skill_id)
            if props is not None:
                facts.append(_replayed_skill(graph, skill_id, dict(props), as_of))
        return StateAt(learner_id=learner_id, seq=seq_n, skills=facts)

    async def _events_up_to(self, learner_id: UUID, seq_n: int) -> Sequence[LogEvent]:
        if self._db is None:
            raise RuntimeError("state_at reads the event log: the reader has no Db")
        params = {"learner_id": learner_id, "seq": seq_n}
        return await self._db.fetch_all(_EVENTS_UP_TO, params, row=LogEvent)

    async def _replay(self, learner_id: UUID, events: Sequence[LogEvent]) -> InMemoryGraph:
        """The seam of the projector: the same code as the rebuild (Т-§2.5, Т-§2.7) — `rules@3`
        over the log of Phase 4 (WP8.3, D-A-191)."""
        from quack.graph.projection.rebuild import project_all  # no import cycle: read ← A

        return _memory(await project_all(learner_id, list(events), self._settings))


@dataclass
class _HubCoverage:
    deadline_ms: int
    topics: list[str] = field(default_factory=list)
    required: dict[str, list[int]] = field(default_factory=dict)


def _topic_ready(topic_id: str, levels: Sequence[int], skills: dict[str, _SkillRow]) -> bool:
    """Т-§14.5: every skill of the topic has every required level `proven` / `implied` with
    the basis `direct`; a topic without skills is not ready."""
    members = [row.props for row in skills.values() if row.topic_id == topic_id]
    return bool(members) and all(
        p.get(f"l{n}Status") in ("proven", "implied") and p.get(f"l{n}Basis") == "direct"
        for p in members
        for n in levels
    )


def _memory(graph: Any) -> InMemoryGraph:
    """The replayed subgraph of `rules@3` in the shape `state_at` reads."""
    memory = InMemoryGraph()
    for n in graph.nodes:
        memory.nodes[(n.label, n.id)] = {"id": n.id, **n.props}
    for r in graph.rels:
        memory.rels[(r.from_label, r.from_id, r.rel_type, r.to_label, r.to_id)] = dict(r.props)
    return memory


def _replayed_skill(graph: InMemoryGraph, skill_id: str, props: dict[str, Any], as_of: int):
    evidence: list[EvidenceRow] = []
    for key in graph.in_rels("Skill", skill_id, "ON"):
        node = graph.get("Evidence", key[1]) if key[0] == "Evidence" else None
        if node is not None and node.get("status") == "active" and _int(node.get("level")) >= 1:
            evidence.append(
                EvidenceRow(
                    _int(node.get("level")),
                    _int(node.get("tier")),
                    str(node.get("outcome")),
                    _int(node.get("atMs")),
                    _int(node.get("sourceSeq")),
                )
            )
    exposures = []
    for key in graph.in_rels("Skill", skill_id, "OF"):
        node = graph.get("Exposure", key[1]) if key[0] == "Exposure" else None
        if node is not None:
            exposures.append(
                ExposureFact(
                    exposure_id=UUID(key[1]),
                    kind=str(node.get("kind") or ""),
                    seq=_int(node.get("sourceSeq")),
                    occurred_at_ms=_int(node.get("atMs")),
                )
            )
    requires = []
    for key in graph.out_rels("Skill", skill_id, "REQUIRES"):
        target = graph.get("Skill", key[4])
        if target is None:
            continue
        link = graph.rels.get(key, {})
        requires.append(
            RequiresLink(
                skill_id=UUID(key[4]),
                confidence=_float(link.get("confidence")),
                has_exposure=bool(graph.in_rels("Skill", key[4], "OF")),
                has_evidence=any(
                    (graph.get("Evidence", k[1]) or {}).get("status") == "active"
                    for k in graph.in_rels("Skill", key[4], "ON")
                ),
            )
        )
    topic_id = topic_name = None
    for key in graph.in_rels("Skill", skill_id, "COVERS"):
        topic = graph.get("Topic", key[1])
        if topic is not None:
            topic_id, topic_name = key[1], _text(topic.get("name"))
            break
    return skill_fact(
        props,
        now_ms=as_of,
        evidence=evidence,
        exposures=sorted(exposures, key=lambda x: (x.occurred_at_ms, x.seq)),
        requires=requires,
        topic_id=topic_id,
        topic_name=topic_name,
    )
