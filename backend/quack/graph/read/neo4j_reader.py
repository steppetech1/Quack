"""`Neo4jGraphReader` — the Neo4j core of `quack.graph.read` (ТЗ Ф4 WP6.4; Т-§2.6, Т-§8, Т-§10.2,
Т-§4.6, Т-§14.5; D-A-140…D-A-143).

Reads the `rules@3` schema (Т-§3.4): `Skill.l1…l6` level properties, `ret*` retention, `ON` /
`OF` / `MANIFESTS` / `COVERS` / `ABOUT` / `INCLUDES` relationships, `Learner.projectedSeq`
(`appliedSeq` of Phase 3 is the fallback). Every query carries `learnerId` (law 1): a method
that takes only a node id finds the owner of the node first and then reads within that learner;
a reader bound with `for_learner` additionally refuses nodes of other learners.

The ten methods of the memory tools, review and state replay are `ExtraReads` (stream A,
`neo4j_extras.py`); until that file joins, they raise `NotImplementedError` here.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, cast
from uuid import UUID

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.neo4j import cypher, read_session
from quack.embeddings.client import EmbeddingClient
from quack.events.values import GapType, Level
from quack.graph.folds import retrievability
from quack.graph.overlay import SessionOverlay
from quack.graph.read.models import (
    ControlType,
    EvidenceFact,
    EvidenceSource,
    ExposureFact,
    ExtractionCandidates,
    Frontier,
    HubGoal,
    HubReadiness,
    HubTopicGoal,
    LevelBasis,
    LevelFact,
    LevelStatus,
    MemorySlice,
    MisconceptionFact,
    MisconceptionStatus,
    PastSolution,
    ProfileFacts,
    RequiresLink,
    ReviewCandidate,
    ReviewScope,
    RootCauseFact,
    SimilarMistake,
    SkillFact,
    SliceFocus,
    StateAt,
    TopicMemory,
    TopicRequires,
    WhyFacts,
)
from quack.graph.read.neo4j_extras import ExtraReads
from quack.graph.read.neo4j_profile import read_profile
from quack.graph.slice import SliceDeps, read_consistent_slice

type Row = dict[str, Any]

LEVELS: Final = (1, 2, 3, 4, 5, 6)
# Т-§10.2: the misconceptions the teacher sees.
VISIBLE_MISCONCEPTIONS: Final = ("suspected", "confirmed", "in_work", "watching")
# D-A-141: verbatim quotes per misconception (the schema of the model: max 2); the number of
# exposures per skill is `Settings.slice_recent_exposures` (D-A-242).
MAX_QUOTES: Final = 2
# Т-§9.4: a root cause is a prerequisite that several hypotheses point to.
ROOT_CAUSE_MIN_HYPOTHESES: Final = 2
_ALIAS_HOPS: Final = 8
_LEVEL_PROPS: Final = ", ".join(f".l{n}Status, .l{n}Basis" for n in LEVELS)

_STATUS: Final[dict[str, LevelStatus]] = {
    "proven": "proven",
    "implied": "implied",
    "unstable": "unstable",
    "failed": "failed",
}
_GAP_TYPES: Final = frozenset({"no_idea", "when_to_apply", "confusion", "execution", "slip",
                               "misconception"})  # fmt: skip


def _s(props: Mapping[str, Any], key: str, default: str = "") -> str:
    value = props.get(key, default)
    return value if isinstance(value, str) else default


def _i(props: Mapping[str, Any], key: str, default: int = 0) -> int:
    value = props.get(key, default)
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else default


def _f(props: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    value = props.get(key, default)
    return (
        float(value) if isinstance(value, int | float) and not isinstance(value, bool) else default
    )


def _b(props: Mapping[str, Any], key: str) -> bool:
    return props.get(key) is True


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _opt_uuid(value: object) -> UUID | None:
    return _uuid(value) if isinstance(value, str) and value else None


def _level_status(props: Mapping[str, Any], level: int) -> str:
    return _s(props, f"l{level}Status", "unknown")


def _level_basis(props: Mapping[str, Any], level: int) -> str:
    return _s(props, f"l{level}Basis", "none")


def level_is_open(props: Mapping[str, Any], level: int) -> bool:
    """Т-§8.5: a level that is neither proven nor implied."""
    return _level_status(props, level) not in ("proven", "implied")


def level_is_ready_direct(
    props: Mapping[str, Any], level: int, implications: Mapping[int, Sequence[int]]
) -> bool:
    """Т-§14.5: proven with basis `direct`, or implied by a level proven with basis `direct`
    (the basis of an implied level is `implied` itself, Т-§8.2 п.5; D-A-142)."""
    status = _level_status(props, level)
    if status == "proven":
        return _level_basis(props, level) == "direct"
    if status != "implied":
        return False
    return any(
        level in implied
        and _level_status(props, source) == "proven"
        and _level_basis(props, source) == "direct"
        for source, implied in implications.items()
    )


def evidence_source(tier: int) -> EvidenceSource:
    """Т-§8.7: tier in words — T1 by code, T2 by a model, T3/T4 by conversation."""
    return "code" if tier == 1 else "model" if tier == 2 else "conversation"


@dataclass
class _Exposure:
    id: str
    kind: str
    seq: int
    at_ms: int


@dataclass
class _Require:
    skill_id: str
    confidence: float
    has_exposure: bool
    has_evidence: bool


@dataclass
class _SkillData:
    id: str
    props: dict[str, Any]
    topic_id: str | None = None
    topic_name: str | None = None
    counts: dict[int, int] = field(default_factory=dict)
    # level -> (atMs, seq, outcome) of the latest T1 evidence
    last_t1: dict[int, tuple[int, int, str]] = field(default_factory=dict)
    exposures: list[_Exposure] = field(default_factory=list)
    requires: list[_Require] = field(default_factory=list)


@dataclass
class _MisData:
    id: str
    props: dict[str, Any]
    skill_id: str
    confused_id: str | None
    count: int
    quotes: list[tuple[int, int, str]]  # (atMs, seq, quote)


@dataclass
class _Loaded:
    skills: dict[str, _SkillData] = field(default_factory=dict)
    misconceptions: list[_MisData] = field(default_factory=list)
    roots: dict[str, int] = field(default_factory=dict)


class Neo4jGraphReader(ExtraReads):
    """The reader of a process over `ctx.neo4j`. `db` (Postgres) lets
    `slice_for_turn` wait for the whole journal of the learner; without it the wait target is
    the tail of the overlay (D-A-140). `clock` gives the current time in ms (retention is
    computed at read time, Т-§8.3)."""

    def __init__(
        self,
        neo4j: AsyncDriver,
        settings: Settings,
        *,
        learner_id: UUID | None = None,
        redis: Redis | None = None,
        embedder: EmbeddingClient | None = None,
        clock: Callable[[], int] | None = None,
        db: Db | None = None,
    ) -> None:
        self._db = db
        self._neo4j = neo4j
        self._settings = settings
        self._bound = learner_id
        self._embedder = embedder
        self._redis = redis
        self._clock = clock or (lambda: int(time.time() * 1000))

    def for_learner(self, learner_id: UUID) -> Neo4jGraphReader:
        """A reader that sees only this learner's nodes (law 1, defence in depth)."""
        return Neo4jGraphReader(
            self._neo4j,
            self._settings,
            learner_id=learner_id,
            redis=self._redis,
            clock=self._clock,
            db=self._db,
        )

    # --- plumbing -------------------------------------------------------------------------

    async def _run(self, query: str, **params: Any) -> list[Row]:
        async with read_session(self._neo4j) as session:
            result = await session.run(cypher(query), params)
            return [record.data() async for record in result]

    async def _projected_seq(self, learner_id: UUID) -> int:
        rows = await self._run(
            "MATCH (l:Learner {id: $l}) WHERE l.learnerId = $l "
            "RETURN coalesce(l.projectedSeq, l.appliedSeq, 0) AS seq",
            l=str(learner_id),
        )
        seq = rows[0]["seq"] if rows else 0
        return int(seq) if isinstance(seq, int) else 0

    def _deps(self) -> SliceDeps:
        return SliceDeps(
            self._redis,
            self._projected_seq,
            self._settings.snapshot_read_retries,
            db=self._db,
        )

    async def _stable[T](
        self, learner_id: UUID, load: Callable[[], Awaitable[T]], *, min_seq: int | None = 0
    ) -> tuple[T, int, bool, bool]:
        """(data, projected_seq, stable, lag_degraded): read between two reads of the
        watermark, repeated `snapshot_read_retries` times (Т-§2.6)."""
        res = await read_consistent_slice(
            learner_id,
            min_seq,
            load,
            self._settings.projector_lag_timeout_ms,
            deps=self._deps(),
        )
        return res.data, res.applied_seq, res.stable, res.lag_degraded

    async def _anchor(self, label: str, node_id: UUID) -> tuple[str, UUID] | None:
        """(live id, owner learner) of a node, through `Alias` (Т-§2.8)."""
        rows = await self._run(
            f"OPTIONAL MATCH (n:{label} {{id: $id}}) "
            f"OPTIONAL MATCH (a:Alias {{id: $id}})-[:ALIAS_OF*1..{_ALIAS_HOPS}]->(t:{label}) "
            "WITH coalesce(n, t) AS x WHERE x IS NOT NULL "
            "RETURN x.id AS id, x.learnerId AS learner",
            id=str(node_id),
        )
        if not rows:
            return None
        owner = _uuid(str(rows[0]["learner"]))
        if owner is None or (self._bound is not None and owner != self._bound):
            return None
        return str(rows[0]["id"]), owner

    async def _live_ids(self, label: str, ids: Sequence[UUID], learner: UUID) -> list[str]:
        if not ids:
            return []
        rows = await self._run(
            f"UNWIND $ids AS i OPTIONAL MATCH (n:{label} {{id: i}}) WHERE n.learnerId = $l "
            f"OPTIONAL MATCH (a:Alias {{id: i}})-[:ALIAS_OF*1..{_ALIAS_HOPS}]->(t:{label}) "
            "WHERE a.learnerId = $l AND t.learnerId = $l "
            "RETURN i AS given, coalesce(n.id, t.id) AS live",
            ids=[str(i) for i in ids],
            l=str(learner),
        )
        live = {r["given"]: r["live"] for r in rows if r["live"] is not None}
        out: list[str] = []
        for i in ids:
            found = live.get(str(i))
            if found is not None and found not in out:
                out.append(found)
        return out

    # --- loading skills and misconceptions -------------------------------------------------

    async def _load(
        self, learner: UUID, skill_ids: Sequence[str], *, with_roots: bool = False
    ) -> _Loaded:
        l_ = str(learner)
        out = _Loaded()
        if not skill_ids:
            return out
        ids = list(skill_ids)
        rows = await self._run(
            "MATCH (s:Skill) WHERE s.id IN $ids AND s.learnerId = $l "
            "OPTIONAL MATCH (t:Topic)-[:COVERS]->(s) WHERE t.learnerId = $l "
            "RETURN s.id AS id, s{.*, embedding: null} AS props, t.id AS tid, t.name AS tname",
            ids=ids,
            l=l_,
        )
        for r in rows:
            out.skills[r["id"]] = _SkillData(r["id"], r["props"], r["tid"], r["tname"])
        live = list(out.skills)
        if not live:
            return out
        for r in await self._run(
            "MATCH (e:Evidence)-[:ON]->(s:Skill) WHERE s.id IN $ids AND s.learnerId = $l "
            "AND e.learnerId = $l AND e.status = 'active' "
            "WITH s.id AS sid, e.level AS lvl, e ORDER BY e.atMs DESC, e.sourceSeq DESC, e.id DESC "
            "RETURN sid, lvl, count(e) AS cnt, head(collect(CASE WHEN e.tier = 1 THEN "
            "{outcome: e.outcome, at: e.atMs, seq: e.sourceSeq} END)) AS t1",
            ids=live,
            l=l_,
        ):
            data, lvl = out.skills[r["sid"]], int(r["lvl"])
            data.counts[lvl] = int(r["cnt"])
            if r["t1"] is not None:
                data.last_t1[lvl] = (int(r["t1"]["at"]), int(r["t1"]["seq"]), r["t1"]["outcome"])
        for r in await self._run(
            "MATCH (x:Exposure)-[:OF]->(s:Skill) WHERE s.id IN $ids AND s.learnerId = $l "
            "AND x.learnerId = $l WITH s.id AS sid, x ORDER BY x.atMs DESC, x.sourceSeq DESC, "
            "x.id DESC RETURN sid, collect({id: x.id, kind: x.kind, seq: x.sourceSeq, "
            "at: x.atMs})[..$n] AS xs",
            ids=live,
            l=l_,
            n=self._settings.slice_recent_exposures,
        ):
            out.skills[r["sid"]].exposures = [
                _Exposure(x["id"], x["kind"], int(x["seq"]), int(x["at"])) for x in r["xs"]
            ]
        for r in await self._run(
            "MATCH (s:Skill)-[r:REQUIRES]->(p:Skill) WHERE s.id IN $ids AND s.learnerId = $l "
            "AND p.learnerId = $l RETURN s.id AS sid, p.id AS pid, r.confidence AS conf, "
            "EXISTS { MATCH (x:Exposure)-[:OF]->(p) WHERE x.learnerId = $l } AS has_exp, "
            "EXISTS { MATCH (e:Evidence)-[:ON]->(p) WHERE e.learnerId = $l "
            "AND e.status = 'active' } AS has_ev",
            ids=live,
            l=l_,
        ):
            out.skills[r["sid"]].requires.append(
                _Require(r["pid"], float(r["conf"] or 0.0), bool(r["has_exp"]), bool(r["has_ev"]))
            )
        for data in out.skills.values():
            data.requires.sort(key=lambda q: (-q.confidence, q.skill_id))
        for r in await self._run(
            "MATCH (m:Misconception)-[:ABOUT]->(s:Skill) WHERE s.id IN $ids AND s.learnerId = $l "
            "AND m.learnerId = $l AND m.status IN $statuses "
            "OPTIONAL MATCH (m)-[:CONFUSED_WITH]->(c:Skill) WHERE c.learnerId = $l "
            "OPTIONAL MATCH (e:Evidence)-[:MANIFESTS]->(m) WHERE e.learnerId = $l "
            "AND e.status = 'active' WITH m, s, c, e ORDER BY e.atMs DESC, e.sourceSeq DESC "
            "WITH m, s, c, count(e) AS cnt, collect(CASE WHEN e.quote <> '' THEN "
            "{at: e.atMs, seq: e.sourceSeq, q: e.quote} END)[..$k] AS quotes "
            "RETURN m.id AS id, m{.*, embedding: null} AS props, s.id AS sid, c.id AS cid, "
            "cnt, quotes",
            ids=live,
            l=l_,
            statuses=list(VISIBLE_MISCONCEPTIONS),
            k=MAX_QUOTES,
        ):
            quotes = [(int(q["at"]), int(q["seq"]), q["q"]) for q in r["quotes"] if q is not None]
            out.misconceptions.append(
                _MisData(r["id"], r["props"], r["sid"], r["cid"], int(r["cnt"]), quotes)
            )
        if with_roots:
            for r in await self._run(
                "MATCH (e:Evidence)-[:ON]->(s:Skill), (e)-[:ROOT_CAUSE]->(p:Skill) "
                "WHERE s.id IN $ids AND s.learnerId = $l AND e.learnerId = $l "
                "AND p.learnerId = $l AND e.status = 'active' "
                "RETURN p.id AS pid, count(DISTINCT e) AS cnt",
                ids=live,
                l=l_,
            ):
                out.roots[r["pid"]] = int(r["cnt"])
        return out

    async def _skill_ids_of_topics(self, learner: UUID, topic_ids: Sequence[str]) -> list[str]:
        if not topic_ids:
            return []
        rows = await self._run(
            "MATCH (t:Topic)-[:COVERS]->(s:Skill) WHERE t.id IN $ids AND t.learnerId = $l "
            "AND s.learnerId = $l RETURN s.id AS id ORDER BY s.name, s.id",
            ids=list(topic_ids),
            l=str(learner),
        )
        return [r["id"] for r in rows]

    # --- overlay (Т-§2.6, Т-§5.5) ----------------------------------------------------------

    async def _apply_overlay(
        self, learner: UUID, loaded: _Loaded, overlay: SessionOverlay, projected: int
    ) -> None:
        """Patch the read state with the session overlay while the projector lags: state props
        of the skills and misconceptions in the overlay graph (rules@3 shape) replace the read
        ones; evidence and exposures of the overlay that Neo4j does not have yet are added
        (D-A-143). Does nothing once the projector reached the tail of the overlay."""
        if overlay.overlay_seq <= projected:
            return
        graph = overlay.graph
        for sid, data in loaded.skills.items():
            node = graph.get("Skill", sid)
            if node is not None:
                for key, value in node.items():
                    if key.startswith(("l", "ret", "exp", "gap", "consolidation", "needs")):
                        data.props[key] = value
        new_ev: dict[str, tuple[str, Any]] = {}
        new_ex: dict[str, tuple[str, Any]] = {}
        for fl, fid, rt, tl, tid in graph.rels:
            if tl == "Skill" and tid in loaded.skills:
                if (fl, rt) == ("Evidence", "ON"):
                    new_ev[fid] = (tid, graph.nodes.get(("Evidence", fid)))
                elif (fl, rt) == ("Exposure", "OF"):
                    new_ex[fid] = (tid, graph.nodes.get(("Exposure", fid)))
        known = await self._known_ids([*new_ev, *new_ex], learner)
        for eid, (sid, props) in new_ev.items():
            if eid in known or props is None or props.get("status", "active") != "active":
                continue
            data = loaded.skills[sid]
            lvl = _i(props, "level")
            data.counts[lvl] = data.counts.get(lvl, 0) + 1
            at, seq = _i(props, "atMs"), _i(props, "sourceSeq")
            if _i(props, "tier") == 1 and (
                lvl not in data.last_t1 or data.last_t1[lvl][:2] < (at, seq)
            ):
                data.last_t1[lvl] = (at, seq, _s(props, "outcome"))
        for xid, (sid, props) in new_ex.items():
            if xid not in known and props is not None:
                data = loaded.skills[sid]
                data.exposures.append(
                    _Exposure(xid, _s(props, "kind"), _i(props, "sourceSeq"), _i(props, "atMs"))
                )
                data.exposures.sort(key=lambda x: (-x.at_ms, -x.seq, x.id))
                del data.exposures[self._settings.slice_recent_exposures :]
        by_id = {m.id: m for m in loaded.misconceptions}
        for mid, props in graph.nodes_with_label("Misconception"):
            about = [
                k for k in graph.out_rels("Misconception", mid, "ABOUT") if k[4] in loaded.skills
            ]
            if not about:
                continue
            mine = by_id.get(mid)
            if mine is None:
                if _s(props, "status") not in VISIBLE_MISCONCEPTIONS:
                    continue
                mine = _MisData(mid, dict(props), about[0][4], None, 0, [])
                loaded.misconceptions.append(mine)
                by_id[mid] = mine
            else:
                mine.props.update(props)
            manifest = graph.in_rels("Misconception", mid, "MANIFESTS")
            seen = known | await self._known_ids([k[1] for k in manifest], learner)
            for fl, fid, _rt, _tl, _tid in manifest:
                ev = graph.nodes.get((fl, fid))
                if fid in seen or ev is None or ev.get("status", "active") != "active":
                    continue
                mine.count += 1
                quote = _s(ev, "quote")
                if quote:
                    mine.quotes.append((_i(ev, "atMs"), _i(ev, "sourceSeq"), quote))
            mine.quotes.sort(reverse=True)
            del mine.quotes[MAX_QUOTES:]
        loaded.misconceptions = [
            m for m in loaded.misconceptions if _s(m.props, "status") in VISIBLE_MISCONCEPTIONS
        ]

    async def _known_ids(self, ids: Sequence[str], learner: UUID) -> set[str]:
        if not ids:
            return set()
        rows = await self._run(
            "MATCH (n) WHERE n.id IN $ids AND n.learnerId = $l RETURN n.id AS id",
            ids=list(ids),
            l=str(learner),
        )
        return {r["id"] for r in rows}

    # --- models ----------------------------------------------------------------------------

    def _recall(self, props: Mapping[str, Any], now_ms: int) -> float | None:
        stability, last = _f(props, "retStability"), _i(props, "retLastRecallAtMs")
        if _s(props, "retState", "none") == "none" or stability <= 0 or last <= 0:
            return None
        return min(1.0, max(0.0, retrievability(stability, now_ms - last)))

    def _skill_fact(self, data: _SkillData, domain_id: UUID | None, now_ms: int) -> SkillFact:
        p = data.props
        levels: list[LevelFact] = []
        for n in LEVELS:
            raw = _level_status(p, n)
            status = _STATUS.get(raw, "not_tested")
            basis = _level_basis(p, n)
            deciding = _opt_uuid(_s(p, f"l{n}DecisiveEvidenceId"))
            last_t1 = data.last_t1.get(n)
            levels.append(
                LevelFact(
                    level=cast(Level, n),
                    status=status,
                    basis=cast(LevelBasis, basis) if basis in ("direct", "transfer") else None,
                    deciding_evidence_id=deciding if status != "not_tested" else None,
                    evidence_count=data.counts.get(n, 0),
                    proven_by_direct=status == "proven" and basis == "direct",
                    last_t1_failed=last_t1 is not None and last_t1[2] == "incorrect",
                )
            )
        gap = _s(p, "gapType")
        gap_type = cast(GapType, gap) if gap in _GAP_TYPES else None
        due = _i(p, "retDueAtMs")
        lapse = _i(p, "retLastLapseAtMs")
        return SkillFact(
            skill_id=UUID(data.id),
            name=_s(p, "name") or None,
            topic_id=_opt_uuid(data.topic_id),
            topic_name=data.topic_name,
            domain_id=_opt_uuid(_s(p, "domainId")) or domain_id,
            levels=levels,
            recall_probability=self._recall(p, now_ms),
            due_at_ms=due if due > 0 else None,
            consolidation_flag=_b(p, "needsConsolidation"),
            gap_type=gap_type,
            gap_type_confidence=min(1.0, max(0.0, _f(p, "gapTypeConfidence")))
            if gap_type
            else None,
            last_lapse_at_ms=lapse if lapse > 0 else None,
            exposures=[
                ExposureFact(exposure_id=UUID(x.id), kind=x.kind, seq=x.seq, occurred_at_ms=x.at_ms)
                for x in data.exposures
            ],
            requires=[
                RequiresLink(
                    skill_id=UUID(q.skill_id),
                    confidence=min(1.0, max(0.0, q.confidence)),
                    has_exposure=q.has_exposure,
                    has_evidence=q.has_evidence,
                )
                for q in data.requires
            ],
        )

    def _misconception_fact(self, m: _MisData, session_id: UUID | None) -> MisconceptionFact:
        status = _s(m.props, "status")
        if (
            status == "confirmed"
            and session_id is not None
            and _s(m.props, "targetedInSessionId") == str(session_id)
        ):
            status = "in_work"  # Т-§9.2: confirmed and targeted in the current session
        raw = m.props.get("triggers")
        triggers = [t for t in raw if isinstance(t, str)] if isinstance(raw, list) else []
        return MisconceptionFact(
            misconception_id=UUID(m.id),
            skill_id=UUID(m.skill_id),
            status=cast(MisconceptionStatus, status),
            statement=_s(m.props, "statement"),
            triggers=triggers,
            confused_with_skill_id=_opt_uuid(m.confused_id),
            manifestation_count=m.count,
            quotes=[q for _, _, q in sorted(m.quotes, reverse=True)[:MAX_QUOTES]],
        )

    def _facts(
        self, loaded: _Loaded, order: Sequence[str], session_id: UUID | None, now_ms: int
    ) -> tuple[list[SkillFact], list[MisconceptionFact]]:
        skills = [
            self._skill_fact(loaded.skills[i], None, now_ms) for i in order if i in loaded.skills
        ]
        mis = sorted(
            (self._misconception_fact(m, session_id) for m in loaded.misconceptions),
            key=lambda m: (str(m.skill_id), str(m.misconception_id)),
        )
        return skills, mis

    # --- GraphReader: core -----------------------------------------------------------------

    async def slice_for_turn(
        self,
        learner_id: UUID,
        session_id: UUID,
        focus: SliceFocus,
        overlay: SessionOverlay | None = None,
    ) -> MemorySlice:
        if self._bound is not None and learner_id != self._bound:
            return MemorySlice(learner_id=learner_id, projected_seq=0)
        topics = await self._live_ids("Topic", focus.topic_ids, learner_id)
        direct = await self._live_ids("Skill", focus.skill_ids, learner_id)
        order = list(direct)
        order += [s for s in await self._skill_ids_of_topics(learner_id, topics) if s not in order]

        async def load() -> _Loaded:
            return await self._load(learner_id, order, with_roots=True)

        if overlay is not None:
            min_seq: int | None = overlay.overlay_seq
        else:
            min_seq = None if self._db is not None else 0
        loaded, projected, stable, lag = await self._stable(learner_id, load, min_seq=min_seq)
        if overlay is not None:
            await self._apply_overlay(learner_id, loaded, overlay, projected)
        skills, mis = self._facts(loaded, order, session_id, self._clock())
        roots = sorted(
            (
                RootCauseFact(skill_id=UUID(pid), hypothesis_count=n)
                for pid, n in loaded.roots.items()
                if n >= ROOT_CAUSE_MIN_HYPOTHESES
            ),
            key=lambda r: (-r.hypothesis_count, str(r.skill_id)),
        )
        return MemorySlice(
            learner_id=learner_id,
            projected_seq=projected,
            lag_degraded=lag,
            stable=stable,
            skills=skills,
            misconceptions=mis,
            root_causes=roots,
        )

    async def frontier(
        self, skill_id: UUID, target_levels: Sequence[Level], overlay: SessionOverlay | None = None
    ) -> Frontier:
        wanted = sorted(set(target_levels))
        if not wanted:
            return Frontier(skill_id=skill_id, level=None)
        anchor = await self._anchor("Skill", skill_id)
        if anchor is None:
            return Frontier(skill_id=skill_id, level=wanted[0])  # law 8: nothing known = open
        live, learner = anchor

        async def load() -> dict[str, Any]:
            rows = await self._run(
                "MATCH (s:Skill {id: $id}) WHERE s.learnerId = $l "
                f"RETURN s{{{_LEVEL_PROPS}}} AS props",
                id=live,
                l=str(learner),
            )
            return dict(rows[0]["props"]) if rows else {}

        props, projected, _, _ = await self._stable(learner, load)
        if overlay is not None and overlay.overlay_seq > projected:
            node = overlay.graph.get("Skill", live)
            if node is not None:
                props.update({k: v for k, v in node.items() if k[:1] == "l" and k[1:2].isdigit()})
        for level in wanted:
            if level_is_open(props, level):
                return Frontier(skill_id=skill_id, level=level)
        return Frontier(skill_id=skill_id, level=None)

    def _hub_levels(self, control_type: str) -> list[int]:
        levels = self._settings.control_type_levels
        return sorted(levels.get(control_type) or levels.get("no_goal", [3]))

    async def _hub_topics(
        self, learner: UUID, hub_id: str, hub: Mapping[str, Any]
    ) -> list[tuple[str, str, list[int]]]:
        """(topic id, name, required levels) of a hub: `INCLUDES`, or every live topic of the
        domain for `domain_all` (the composition is not stored, Т-§4.8)."""
        default = self._hub_levels(_s(hub, "controlType", "no_goal"))
        if _s(hub, "kind") == "domain_all":
            rows = await self._run(
                "MATCH (t:Topic) WHERE t.learnerId = $l AND t.domainId = $d "
                "AND t.status IN ['active', 'proposed'] RETURN t.id AS id, t.name AS name, "
                "null AS levels ORDER BY t.name, t.id",
                l=str(learner),
                d=_s(hub, "domainId"),
            )
        else:
            rows = await self._run(
                "MATCH (h:Hub {id: $h})-[r:INCLUDES]->(t:Topic) WHERE h.learnerId = $l "
                "AND t.learnerId = $l RETURN t.id AS id, t.name AS name, "
                "r.requiredLevels AS levels ORDER BY t.name, t.id",
                h=hub_id,
                l=str(learner),
            )
        out: list[tuple[str, str, list[int]]] = []
        for r in rows:
            raw = r["levels"]
            levels = sorted({int(x) for x in raw}) if isinstance(raw, list) and raw else default
            out.append((r["id"], str(r["name"] or ""), levels))
        return out

    async def topic_readiness(self, hub_id: UUID) -> HubReadiness:
        anchor = await self._anchor("Hub", hub_id)
        if anchor is None:
            return HubReadiness(hub_id=hub_id)
        live, learner = anchor
        implications = self._settings.level_implications

        async def load() -> tuple[
            list[tuple[str, str, list[int]]], dict[str, list[dict[str, Any]]]
        ]:
            hubs = await self._run(
                "MATCH (h:Hub {id: $h}) WHERE h.learnerId = $l RETURN properties(h) AS props",
                h=live,
                l=str(learner),
            )
            if not hubs:
                return [], {}
            topics = await self._hub_topics(learner, live, hubs[0]["props"])
            rows = await self._run(
                "MATCH (t:Topic)-[:COVERS]->(s:Skill) WHERE t.id IN $ids AND t.learnerId = $l "
                f"AND s.learnerId = $l RETURN t.id AS tid, s{{{_LEVEL_PROPS}}} AS props",
                ids=[t[0] for t in topics],
                l=str(learner),
            )
            by_topic: dict[str, list[dict[str, Any]]] = {}
            for r in rows:
                by_topic.setdefault(r["tid"], []).append(r["props"])
            return topics, by_topic

        (topics, by_topic), _, _, _ = await self._stable(learner, load)
        ready: list[UUID] = []
        not_ready: list[UUID] = []
        for tid, _name, levels in topics:
            skills = by_topic.get(tid, [])
            ok = bool(skills) and all(
                level_is_ready_direct(p, n, implications) for p in skills for n in levels
            )
            (ready if ok else not_ready).append(UUID(tid))
        return HubReadiness(
            hub_id=hub_id, ready_topic_ids=sorted(ready), not_ready_topic_ids=sorted(not_ready)
        )

    async def why(self, topic_id: UUID, level: Level | None = None) -> WhyFacts:
        anchor = await self._anchor("Topic", topic_id)
        if anchor is None:
            return WhyFacts(topic_id=topic_id, level=level)
        live, learner = anchor
        n_recent = self._settings.why_recent_n

        async def load() -> tuple[int | None, list[Row], list[Row]]:
            l_ = str(learner)
            topics = await self._run(
                "MATCH (t:Topic {id: $t}) WHERE t.learnerId = $l "
                "RETURN t.displayLevel AS lvl, t.displayReason AS reason",
                t=live,
                l=l_,
            )
            shown = topics[0]["lvl"] if topics else None
            use = (
                level
                if level is not None
                else (shown if isinstance(shown, int) and shown > 0 else None)
            )
            reason = str(topics[0]["reason"] or "") if topics else ""
            sk = await self._run(
                "MATCH (t:Topic {id: $t})-[:COVERS]->(s:Skill) WHERE t.learnerId = $l "
                "AND s.learnerId = $l AND ($sid = '' OR s.id = $sid) RETURN s.id AS sid, "
                "properties(s) AS props",
                t=live,
                l=l_,
                sid=reason if reason else "",
            )
            if reason and not sk:  # the reason names a misconception, not a skill of the topic
                sk = await self._run(
                    "MATCH (t:Topic {id: $t})-[:COVERS]->(s:Skill) WHERE t.learnerId = $l "
                    "AND s.learnerId = $l RETURN s.id AS sid, properties(s) AS props",
                    t=live,
                    l=l_,
                )
            sids = [r["sid"] for r in sk]
            decisive: list[Row] = []
            if use is not None:
                eids = [
                    _s(r["props"], f"l{use}DecisiveEvidenceId")
                    for r in sk
                    if _s(r["props"], f"l{use}Status") in ("proven", "failed", "unstable")
                ]
                decisive = await self._evidence_rows(
                    l_, "e.id IN $eids", eids=[e for e in eids if e], n=len(eids) or 1
                )
            recent = await self._evidence_rows(
                l_,
                "s.id IN $sids AND e.status = 'active' AND ($lvl IS NULL OR e.level = $lvl)",
                sids=sids,
                lvl=use,
                n=n_recent,
            )
            return use, decisive, recent

        (used, decisive, recent), _, _, _ = await self._stable(learner, load)
        deciding = max(
            decisive,
            key=lambda r: (_i(r["props"], "atMs"), _i(r["props"], "sourceSeq")),
            default=None,
        )
        return WhyFacts(
            topic_id=topic_id,
            level=cast("Level | None", used),
            deciding=self._evidence_fact(deciding) if deciding else None,
            recent=[self._evidence_fact(r) for r in recent],
        )

    async def _evidence_rows(self, learner: str, where: str, *, n: int, **params: Any) -> list[Row]:
        if params.get("eids") == [] or params.get("sids") == []:
            return []
        return await self._run(
            "MATCH (e:Evidence)-[:ON]->(s:Skill) WHERE e.learnerId = $l AND s.learnerId = $l "
            f"AND {where} OPTIONAL MATCH (e)-[:ON_TASK]->(k:Task) WHERE k.learnerId = $l "
            "RETURN e{.*, embedding: null} AS props, s.id AS sid, k.id AS task "
            "ORDER BY e.atMs DESC, e.sourceSeq DESC, e.id DESC LIMIT $n",
            l=learner,
            n=n,
            **params,
        )

    def _evidence_fact(self, row: Row) -> EvidenceFact:
        p = row["props"]
        hint = _i(p, "hintStep")
        return EvidenceFact(
            evidence_id=UUID(p["id"]),
            skill_id=UUID(row["sid"]),
            level=cast(Level, _i(p, "level", 1)),
            outcome=cast("Any", _s(p, "outcome", "unclear")),
            source=evidence_source(_i(p, "tier", 4)),
            assistance=cast("Any", _s(p, "assistance", "none")),
            hint_step=hint if 1 <= hint <= 3 else None,
            quote=_s(p, "quote") or None,
            task_id=_opt_uuid(row.get("task")),
            session_id=_opt_uuid(_s(p, "sourceSessionId")),
            seq=_i(p, "sourceSeq"),
            occurred_at_ms=_i(p, "atMs"),
        )

    async def topic_requires(self, domain_id: UUID) -> list[TopicRequires]:
        anchor = await self._anchor("Domain", domain_id)
        if anchor is None:
            return []
        live, learner = anchor

        async def load() -> list[Row]:
            return await self._run(
                "MATCH (ta:Topic)-[:COVERS]->(a:Skill)-[:REQUIRES]->(b:Skill), "
                "(tb:Topic)-[:COVERS]->(b) "
                "WHERE ta.learnerId = $l AND tb.learnerId = $l AND a.learnerId = $l "
                "AND b.learnerId = $l AND ta.domainId = $d AND tb.domainId = $d AND ta <> tb "
                "RETURN ta.id AS a, tb.id AS b, count(*) AS weight ORDER BY a, b",
                l=str(learner),
                d=live,
            )

        rows, _, _, _ = await self._stable(learner, load)
        return [
            TopicRequires(
                from_topic_id=UUID(r["a"]), to_topic_id=UUID(r["b"]), weight=int(r["weight"])
            )
            for r in rows
        ]

    async def recall_probability(self, skill_id: UUID, now_ms: int) -> float | None:
        anchor = await self._anchor("Skill", skill_id)
        if anchor is None:
            return None
        live, learner = anchor
        rows = await self._run(
            "MATCH (s:Skill {id: $id}) WHERE s.learnerId = $l "
            "RETURN s.retState AS retState, s.retStability AS retStability, "
            "s.retLastRecallAtMs AS retLastRecallAtMs",
            id=live,
            l=str(learner),
        )
        return self._recall(rows[0], now_ms) if rows else None

    async def domain_goals(self, domain_id: UUID) -> list[HubGoal]:
        anchor = await self._anchor("Domain", domain_id)
        if anchor is None:
            return []
        live, learner = anchor

        async def load() -> list[tuple[Row, list[tuple[str, str, list[int]]]]]:
            hubs = await self._run(
                "MATCH (d:Domain {id: $d})-[:HAS_HUB]->(h:Hub) WHERE d.learnerId = $l "
                "AND h.learnerId = $l AND h.status = 'active' RETURN h.id AS id, "
                "properties(h) AS props",
                d=live,
                l=str(learner),
            )
            return [(h, await self._hub_topics(learner, h["id"], h["props"])) for h in hubs]

        data, _, _, _ = await self._stable(learner, load)
        goals: list[HubGoal] = []
        for hub, topics in data:
            p = hub["props"]
            deadline = _i(p, "deadlineAtMs")
            goals.append(
                HubGoal(
                    hub_id=UUID(hub["id"]),
                    name=_s(p, "name"),
                    kind=_s(p, "kind"),
                    control_type=cast(ControlType, _s(p, "controlType", "no_goal")),
                    deadline_at_ms=deadline if deadline > 0 else None,
                    topics=[
                        HubTopicGoal(
                            topic_id=UUID(tid), name=name, required_levels=cast("list[Level]", lv)
                        )
                        for tid, name, lv in topics
                    ],
                )
            )
        goals.sort(
            key=lambda g: (g.deadline_at_ms is None, g.deadline_at_ms or 0, g.name, str(g.hub_id))
        )
        return goals

    async def topic_memory(self, topic_ids: Sequence[UUID]) -> list[TopicMemory]:
        out: list[TopicMemory] = []
        seen: set[UUID] = set()
        for given in topic_ids:
            anchor = await self._anchor("Topic", given)
            if anchor is None:
                continue
            live, learner = anchor
            if UUID(live) in seen:
                continue
            seen.add(UUID(live))

            async def load(
                live: str = live, learner: UUID = learner
            ) -> tuple[Row, _Loaded, list[str]]:
                head = await self._run(
                    "MATCH (t:Topic {id: $t}) WHERE t.learnerId = $l "
                    "RETURN t.name AS name, t.domainId AS domain",
                    t=live,
                    l=str(learner),
                )
                ids = await self._skill_ids_of_topics(learner, [live])
                return head[0], await self._load(learner, ids), ids

            (head, loaded, ids), _, _, _ = await self._stable(learner, load)
            skills, mis = self._facts(loaded, ids, None, self._clock())
            out.append(
                TopicMemory(
                    topic_id=UUID(live),
                    name=str(head["name"] or ""),
                    domain_id=_opt_uuid(head["domain"]),
                    skills=skills,
                    misconceptions=mis,
                )
            )
        return out

    # --- GraphReader: ExtraReads (neo4j_extras.py) ---------------------------------------------
    # A reader bound to a learner answers nothing about another one (law 1, D-A-144): the
    # methods that take `learner_id` are guarded here, the ones that start from an id of a node
    # through `_owner`.

    def _foreign(self, learner_id: UUID) -> bool:
        return self._bound is not None and learner_id != self._bound

    async def _xowner(self, label: str, node_id: UUID) -> str | None:
        owner = await super()._xowner(label, node_id)
        if owner is not None and self._bound is not None and owner != str(self._bound):
            return None
        return owner

    async def review_candidates(
        self, learner_id: UUID, now_ms: int, scope: ReviewScope | None = None
    ) -> list[ReviewCandidate]:
        if self._foreign(learner_id):
            return []
        return await super().review_candidates(learner_id, now_ms, scope)

    async def similar_mistakes(
        self, learner_id: UUID, evidence_id: UUID | None = None, text: str | None = None
    ) -> list[SimilarMistake]:
        if self._foreign(learner_id):
            return []
        return await super().similar_mistakes(learner_id, evidence_id, text)

    async def past_solution(
        self, learner_id: UUID, task_id: UUID | None = None, skill_id: UUID | None = None
    ) -> list[PastSolution]:
        if self._foreign(learner_id):
            return []
        return await super().past_solution(learner_id, task_id, skill_id)

    async def state_at(self, learner_id: UUID, seq_n: int, skill_ids: Sequence[UUID]) -> StateAt:
        if self._foreign(learner_id):
            return StateAt(learner_id=learner_id, seq=seq_n)
        return await super().state_at(learner_id, seq_n, skill_ids)

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
        if self._foreign(learner_id):
            return ExtractionCandidates(skills=[], misconceptions=[])
        return await super().extraction_candidates(
            learner_id, session_id, episode_vector, k, topic_id=topic_id, skill_ids=skill_ids
        )

    async def profile(self, learner_id: UUID) -> ProfileFacts:
        if self._foreign(learner_id):
            return ProfileFacts(learner_id=learner_id)
        return await read_profile(self._neo4j, learner_id)
