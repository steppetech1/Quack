"""Unit tests for Structure Writer (`builder@2` Mode `Structure`), `resolve_material_structure`,
Addition #2 (empty provisional skill cleanup), and `builder/v2.md` T-M5 compliance."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from quack.agents.hygiene import (
    DomainGraph,
    MaterialBuildsOn,
    MaterialTopicCandidate,
    ResolvedStructureTopic,
    SkillNode,
    TopicNode,
    UnassignedSkillNode,
    _find_ended_empty_births,
    _RawRequires,
    filter_requires_edges,
    resolve_material_structure,
    run_hygiene,
)
from quack.agents.reconcile import Candidate, append_unwritten, node_id
from quack.agents.schemas import (
    BuilderOutput,
    CoreSkillSpec,
    HygieneOutput,
    ProvisionalSkillDecision,
    RequiresClaim,
    SplitOutput,
    StructureOutput,
    TopicCandidateDecision,
    TopicsOutput,
)
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import EdgeAdded, SkillCreated, SkillDeleted, SkillMerged
from quack.events.log import NewEvent
from quack.graph.ids import deterministic_id
from quack.jobs.runner import JobContext
from quack.llm.gateway import LLMResult, LLMUsage
from tests.llm.prompt_contracts import undeclared_names

SETTINGS = Settings()
DOMAIN = UUID(int=0xD1)


class _FakeDb:
    """In-memory `Db` stub supporting `transaction`, `append_events` / `append_unwritten`, and
    the `core.events` queries used by Addition #2."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._seq = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_FakeDb]:
        yield self

    def add_stored(
        self,
        learner_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        *,
        session_id: UUID | None = None,
        domain_id: UUID | None = DOMAIN,
        idempotency_key: str | None = None,
    ) -> int:
        self._seq += 1
        self.events.append(
            {
                "learner_id": learner_id,
                "seq": self._seq,
                "id": self._seq,
                "type": event_type,
                "schema_version": 1,
                "actor": "test",
                "domain_id": domain_id,
                "session_id": session_id,
                "causation_seq": None,
                "correlation_id": None,
                "idempotency_key": idempotency_key or str(uuid4()),
                "payload_hash": "h",
                "occurred_at": "2026-11-26T00:00:00Z",
                "payload": payload,
                "source_refs": [],
            }
        )
        return self._seq

    async def fetch_one(
        self, query: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if "FROM core.learners" in query:
            return {"seq": self._seq}
        return None

    async def fetch_all(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        params = params or {}
        if "SELECT idempotency_key FROM core.events" in query:
            keys = set(params.get("keys", ()))
            lid = params.get("learner_id")
            return [
                {"idempotency_key": e["idempotency_key"]}
                for e in self.events
                if e["learner_id"] == lid and e["idempotency_key"] in keys
            ]
        if "type = 'session.ended'" in query:
            lid = params.get("learner_id")
            sids: list[dict[str, Any]] = []
            seen: set[str] = set()
            for e in self.events:
                if e["learner_id"] == lid and e["type"] == "session.ended":
                    sid = e["payload"].get("session_id") or (
                        str(e["session_id"]) if e["session_id"] else None
                    )
                    if sid and str(sid) not in seen:
                        seen.add(str(sid))
                        sids.append({"sid": str(sid)})
            return sids
        if "type = ANY" in query:
            lid = params.get("learner_id")
            types = set(params.get("types", ()))
            return [e for e in self.events if e["learner_id"] == lid and e["type"] in types]
        return []


@dataclass
class _StructureGateway:
    responder: Callable[[dict[str, Any], int], StructureOutput]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_structured(
        self,
        role: str,
        prompt_version: int,
        response_model: type[BaseModel],
        variables: dict[str, Any],
        *args: Any,
        check: Callable[[Any], str | None] | None = None,
        **kwargs: Any,
    ) -> LLMResult[Any]:
        idx = len(self.calls)
        self.calls.append(variables)
        out = self.responder(variables, idx)
        if check is not None:
            problem = check(out)
            if problem is not None:
                # Simulate the single repair retry in LLMGateway.
                out = self.responder({**variables, "_repair_error": problem}, idx + 100)
        return LLMResult(
            value=out,
            from_cache=False,
            prompt_hash="h",
            model="m",
            usage=LLMUsage(input_tokens=100, output_tokens=50, cached_tokens=20, latency_ms=30),
        )


def _fake_ctx(db: _FakeDb, gateway: Any, *, settings: Settings = SETTINGS) -> JobContext:
    return cast(
        JobContext,
        SimpleNamespace(
            db=cast(Db, db),
            llm=gateway,
            settings=settings,
            neo4j=None,
            embeddings=None,
        ),
    )


def _patch_append(monkeypatch: pytest.MonkeyPatch, db: _FakeDb) -> list[NewEvent]:
    written_log: list[NewEvent] = []

    async def _fake_append(_db: Any, learner_id: UUID, events: list[NewEvent]) -> list[Any]:
        for e in events:
            payload = (
                e.payload if isinstance(e.payload, dict) else e.payload.model_dump(mode="json")
            )
            db.add_stored(
                learner_id,
                e.type,
                payload,
                domain_id=e.domain_id,
                idempotency_key=e.idempotency_key,
            )
            written_log.append(e)
        return []

    monkeypatch.setattr("quack.agents.reconcile.append_events", _fake_append)
    return written_log


# --- 1. Prompt v2.md checks (LF + T-M5) ---


def test_builder_v2_prompt_has_lf_endings_and_passes_tm5(monkeypatch: pytest.MonkeyPatch) -> None:
    prompt_path = Path(__file__).resolve().parents[2] / "quack" / "prompts" / "builder" / "v2.md"
    raw_bytes = prompt_path.read_bytes()
    assert b"\r" not in raw_bytes, "builder/v2.md must use LF line endings only"

    text = raw_bytes.decode("utf-8")
    for mode_name in ("Reconcile", "Hygiene", "Split", "Topics", "Structure"):
        assert f"**{mode_name}.**" in text

    # Validate T-M5 against the full set of builder@2 output models.
    from tests.llm import prompt_contracts as pc

    monkeypatch.setitem(
        pc.ALL_PROMPT_SCHEMAS,
        "builder@2",
        (
            BuilderOutput,
            HygieneOutput,
            SplitOutput,
            TopicsOutput,
            StructureOutput,
        ),
    )
    assert undeclared_names("builder@2", text) == []
    # Also check strict JSON schema compatibility for StructureOutput.
    from quack.llm.strict_schema import to_strict_json_schema

    schema = to_strict_json_schema(StructureOutput)
    assert set(schema["properties"]) == {"topic_candidates", "provisional_skills"}


# --- 2. Closed-choice validation & unknown-label retry ---


async def test_structure_unknown_label_triggers_repair_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    learner_id = uuid4()
    db = _FakeDb()
    written = _patch_append(monkeypatch, db)
    t1, s1 = uuid4(), uuid4()
    graph = DomainGraph(
        domain_name="Math",
        topics=[
            TopicNode(
                t1,
                "Derivatives",
                "active",
                description="Rules of differentiation",
                core_skill_id=s1,
                core_skill_name="differentiates functions",
                skill_ids=(s1,),
            )
        ],
        skills=[SkillNode(s1, "differentiates functions", "active", [], core=True, topic_id=t1)],
    )

    repair_errors: list[str] = []

    def respond(variables: dict[str, Any], call_id: int) -> StructureOutput:
        if "_repair_error" in variables:
            repair_errors.append(variables["_repair_error"])
            return StructureOutput(
                topic_candidates=[
                    TopicCandidateDecision(
                        candidate_index=0,
                        action="link",
                        target_topic="T1",
                        name=None,
                        description=None,
                        core_skill=None,
                        requires=[],
                    )
                ],
                provisional_skills=[],
            )
        return StructureOutput(
            topic_candidates=[
                TopicCandidateDecision(
                    candidate_index=0,
                    action="link",
                    target_topic="T999",  # unknown label!
                    name=None,
                    description=None,
                    core_skill=None,
                    requires=[],
                )
            ],
            provisional_skills=[],
        )

    gw = _StructureGateway(respond)
    res = await resolve_material_structure(
        _fake_ctx(db, gw),
        learner_id=learner_id,
        domain_id=DOMAIN,
        candidates=[
            MaterialTopicCandidate(
                name="Derivatives",
                description="Differentiation",
                core_skill_name="differentiates functions",
            )
        ],
        graph=graph,
    )
    assert len(repair_errors) == 1
    assert "T999" in repair_errors[0]
    assert res == [
        ResolvedStructureTopic(
            topic_id=t1,
            core_skill_id=s1,
            skill_ids=(s1,),
            skill_names=("differentiates functions",),
        )
    ]
    assert written == []


# --- 3. 1 core skill invariant & new topic creation ---


async def test_structure_one_core_skill_invariant_and_new_topic_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    learner_id = uuid4()
    db = _FakeDb()
    written = _patch_append(monkeypatch, db)
    t_exist, s_core_exist, p_new_core, p_dup_topic = uuid4(), uuid4(), uuid4(), uuid4()
    graph = DomainGraph(
        domain_name="Statistics",
        topics=[
            TopicNode(
                t_exist,
                "Normal distribution",
                "active",
                description="Gaussian",
                core_skill_id=s_core_exist,
                core_skill_name="computes normal probabilities",
                skill_ids=(s_core_exist,),
            )
        ],
        skills=[
            SkillNode(
                s_core_exist,
                "computes normal probabilities",
                "active",
                [],
                core=True,
                topic_id=t_exist,
            ),
            SkillNode(p_new_core, "computes Poisson PMF", "provisional", [], core=False),
            SkillNode(p_dup_topic, "computes z-scores", "provisional", [], core=False),
        ],
    )

    def respond(variables: dict[str, Any], _: int) -> StructureOutput:
        # S1 = s_core_exist, S2 = p_new_core, S3 = p_dup_topic (from subjects/candidates order)
        return StructureOutput(
            topic_candidates=[],
            provisional_skills=[
                ProvisionalSkillDecision(
                    skill="S2",
                    action="core_of_new_topic",
                    target_skill=None,
                    target_topic=None,
                    definition="Computes P(X=k) for Poisson",
                    exemplar="Find P(X=2) for lambda=3",
                    kind="procedure",
                    topic_name="Poisson distribution",
                    topic_description="Discrete count distribution",
                    core_skill=CoreSkillSpec(
                        name="computes Poisson PMF",
                        definition="Computes P(X=k) for Poisson",
                        exemplar="Find P(X=2) for lambda=3",
                        kind="procedure",
                    ),
                ),
                # Tries to make a new topic colliding with existing "Normal distribution"
                ProvisionalSkillDecision(
                    skill="S3",
                    action="core_of_new_topic",
                    target_skill=None,
                    target_topic=None,
                    definition="Standardizes X to Z",
                    exemplar="Compute z=(x-mu)/sigma",
                    kind="procedure",
                    topic_name="Statistics: normal distribution",
                    topic_description="Duplicate",
                    core_skill=None,
                ),
            ],
        )

    gw = _StructureGateway(respond)
    types = await run_hygiene(_fake_ctx(db, gw), graph, learner_id=learner_id, domain_id=DOMAIN)
    assert types == [
        "topic.created",
        "skill.placed",
        "skill.created",
        "skill.placed",
        "skill.created",
    ]

    # p_new_core got core=True in new topic; p_dup_topic was moved into t_exist with core=False!
    by_skill = {
        p.skill_id: p
        for e in written
        if e.type == "skill.created" and isinstance((p := e.payload), SkillCreated)
    }
    assert by_skill[p_new_core].core is True
    assert by_skill[p_dup_topic].core is False
    assert by_skill[p_dup_topic].topic_id == t_exist


# --- 4. All 4 edge rejection rules + material/model confidence ---


def test_filter_requires_edges_all_four_rejection_rules() -> None:
    a, b, c, d, e = (uuid4() for _ in range(5))
    existing = {(b, a), (c, d), (d, e)}
    claims = [
        _RawRequires(a, c, 0.29, "model"),  # 1. score < 0.3 -> dropped (score)
        _RawRequires(
            a, d, 0.8, "model"
        ),  # 2. bidirectional with (d, a) -> dropped (both_directions)
        _RawRequires(d, a, 0.7, "material"),  # 2. bidirectional -> dropped (both_directions)
        _RawRequires(
            a, b, 0.9, "material"
        ),  # 3. reverse of (b, a) in existing -> dropped (reverse)
        _RawRequires(
            c, e, 0.9, "material"
        ),  # 4. transitive c->d->e in existing -> dropped (transitive)
        _RawRequires(a, e, 0.8, "material"),  # valid!
    ]
    accepted, dropped = filter_requires_edges(claims, set(existing), min_score=0.3)
    assert [(x.from_id, x.to_id) for x in accepted] == [(a, e)]
    assert dict(dropped) == {
        "score": 1,
        "both_directions": 2,
        "reverse": 1,
        "transitive": 1,
    }


async def test_other_topic_skill_emits_merge_and_filtered_requires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    learner_id = uuid4()
    db = _FakeDb()
    written = _patch_append(monkeypatch, db)
    t_cur, s_cur_core, p_skill = uuid4(), uuid4(), uuid4()
    t_other, s_other_core, s_other_sub = uuid4(), uuid4(), uuid4()
    graph = DomainGraph(
        domain_name="Math",
        topics=[
            TopicNode(
                t_cur,
                "Integrals",
                "active",
                core_skill_id=s_cur_core,
                core_skill_name="computes integrals",
                skill_ids=(s_cur_core, p_skill),
            ),
            TopicNode(
                t_other,
                "Derivatives",
                "active",
                core_skill_id=s_other_core,
                core_skill_name="computes derivatives",
                skill_ids=(s_other_core, s_other_sub),
            ),
        ],
        skills=[
            SkillNode(s_cur_core, "computes integrals", "active", [], core=True, topic_id=t_cur),
            SkillNode(p_skill, "chain rule", "provisional", [], core=False, topic_id=t_cur),
            SkillNode(
                s_other_core, "computes derivatives", "active", [], core=True, topic_id=t_other
            ),
            SkillNode(
                s_other_sub, "chain rule derivative", "active", [], core=False, topic_id=t_other
            ),
        ],
    )

    async def _fake_find_candidates(
        _ctx: Any, *, learner_id: UUID, domain_id: UUID, subjects: Any
    ) -> list[list[Candidate]]:
        return [
            [
                Candidate(
                    "skill",
                    s_other_sub,
                    "chain rule derivative",
                    domain_id,
                    None,
                    t_other,
                )
            ]
            for _ in subjects
        ]

    monkeypatch.setattr("quack.agents.hygiene.find_candidates", _fake_find_candidates)

    def respond(variables: dict[str, Any], _: int) -> StructureOutput:
        # S1=s_cur_core, S2=s_other_core, S3=p_skill, S4=s_other_sub (from find_candidates)
        assert "S4 (chain rule derivative, topic T2)" in variables["subjects"]
        return StructureOutput(
            topic_candidates=[],
            provisional_skills=[
                ProvisionalSkillDecision(
                    skill="S3",
                    action="other_topic_skill",
                    target_skill="S4",
                    target_topic=None,
                    definition=None,
                    exemplar=None,
                    kind=None,
                    topic_name=None,
                    topic_description=None,
                    core_skill=None,
                )
            ],
        )

    gw = _StructureGateway(respond)
    ctx = cast(
        JobContext,
        SimpleNamespace(
            db=cast(Db, db),
            llm=gw,
            settings=SETTINGS,
            neo4j=object(),
            embeddings=object(),
        ),
    )
    await run_hygiene(ctx, graph, learner_id=learner_id, domain_id=DOMAIN)
    merged = [
        p for e in written if e.type == "skill.merged" and isinstance((p := e.payload), SkillMerged)
    ]
    edges = [e for e in written if e.type == "edge.added"]
    assert len(merged) == 1 and merged[0].from_id == p_skill and merged[0].into_id == s_other_sub
    assert len(edges) == 1
    edge_p = cast(EdgeAdded, edges[0].payload)
    assert edge_p.from_id == s_cur_core
    assert edge_p.to_id == s_other_core
    assert edge_p.origin == "model"
    assert edge_p.confidence == SETTINGS.reconcile_requires_confidence["model"]
    assert edges[0].idempotency_key == f"requires:{s_cur_core}:{s_other_core}:model"


# --- 5. Batching across > 15 items & sequential state updates ---


async def test_structure_batches_over_15_items_sequentially(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    learner_id = uuid4()
    db = _FakeDb()
    written = _patch_append(monkeypatch, db)
    candidates = [
        MaterialTopicCandidate(
            name=f"Topic {i}",
            description=f"Desc {i}",
            core_skill_name=f"does skill {i}",
            builds_on=(MaterialBuildsOn("prev", "as shown previously"),) if i > 0 else (),
        )
        for i in range(18)
    ]

    def respond(variables: dict[str, Any], call_idx: int) -> StructureOutput:
        if call_idx == 0:
            # Batch 1: 15 items (indices 0..14)
            return StructureOutput(
                topic_candidates=[
                    TopicCandidateDecision(
                        candidate_index=i,
                        action="create",
                        target_topic=None,
                        name=f"Topic {i}",
                        description=f"Desc {i}",
                        core_skill=CoreSkillSpec(
                            name=f"does skill {i}",
                            definition=f"Def {i}",
                            exemplar=f"Ex {i}",
                            kind="procedure",
                        ),
                        requires=[],
                    )
                    for i in range(15)
                ],
                provisional_skills=[],
            )
        # Batch 2: 3 items (indices 0..2 -> global 15..17); should see T1..T15 and S1..S15!
        assert "Topic 0" in variables["candidates"]
        assert "Topic 14" in variables["candidates"]
        return StructureOutput(
            topic_candidates=[
                TopicCandidateDecision(
                    candidate_index=i,
                    action="create",
                    target_topic=None,
                    name=f"Topic {15 + i}",
                    description=f"Desc {15 + i}",
                    core_skill=CoreSkillSpec(
                        name=f"does skill {15 + i}",
                        definition=f"Def {15 + i}",
                        exemplar=f"Ex {15 + i}",
                        kind="concept",
                    ),
                    requires=[
                        RequiresClaim(
                            target_core_skill_label="S1",
                            score=0.9,
                            origin="material",
                        )
                    ]
                    if i == 0
                    else [],
                )
                for i in range(3)
            ],
            provisional_skills=[],
        )

    gw = _StructureGateway(respond)
    res = await resolve_material_structure(
        _fake_ctx(db, gw),
        learner_id=learner_id,
        domain_id=DOMAIN,
        candidates=candidates,
        source_refs=[{"seq": 42}],
        graph=DomainGraph(domain_name="Math"),
    )
    assert len(gw.calls) == 2
    assert len(res) == 18
    assert res[0].topic_id == node_id(learner_id, DOMAIN, "topic", "Topic 0")
    assert res[17].topic_id == node_id(learner_id, DOMAIN, "topic", "Topic 17")
    edges = [e for e in written if e.type == "edge.added"]
    assert len(edges) == 1
    edge_p = cast(EdgeAdded, edges[0].payload)
    assert edge_p.from_id == res[15].core_skill_id
    assert edge_p.to_id == res[0].core_skill_id
    assert edge_p.origin == "material"
    assert edge_p.confidence == SETTINGS.reconcile_requires_confidence["material"]
    assert edges[0].source_refs == [{"seq": 42}]

    out_log = capsys.readouterr().out
    assert "structure_writer_completed" in out_log


# --- 6. append_unwritten retry idempotency ---


async def test_append_unwritten_skips_written_keys_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    learner_id = uuid4()
    db = _FakeDb()
    _patch_append(monkeypatch, db)
    e1 = NewEvent(
        type="topic.created",
        payload={
            "topic_id": str(uuid4()),
            "domain_id": str(DOMAIN),
            "name": "A",
            "origin": "material",
        },
        actor="builder@2",
        idempotency_key="structure:topic.created:a",
        domain_id=DOMAIN,
    )
    e2 = NewEvent(
        type="topic.created",
        payload={
            "topic_id": str(uuid4()),
            "domain_id": str(DOMAIN),
            "name": "B",
            "origin": "material",
        },
        actor="builder@2",
        idempotency_key="structure:topic.created:b",
        domain_id=DOMAIN,
    )
    first = await append_unwritten(cast(Db, db), learner_id, [e1])
    assert len(first) == 1
    # Retry with same key (even different payload) + a new key -> only e2 is written, no conflict!
    e1_reworded = NewEvent(
        type="topic.created",
        payload={
            "topic_id": str(uuid4()),
            "domain_id": str(DOMAIN),
            "name": "A reworded",
            "origin": "material",
        },
        actor="builder@2",
        idempotency_key="structure:topic.created:a",
        domain_id=DOMAIN,
    )
    second = await append_unwritten(cast(Db, db), learner_id, [e1_reworded, e2])
    assert [e.idempotency_key for e in second] == ["structure:topic.created:b"]


# --- 7. Addition #2: empty provisional skills after session.ended ---


async def test_addition2_empty_provisional_skills_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    learner_id = uuid4()
    session_id = uuid4()
    db = _FakeDb()
    written = _patch_append(monkeypatch, db)

    topic_id, core_id = uuid4(), uuid4()
    # Birth 1: in a topic, 0 evidence & 0 exposures -> should merge into core_id
    seq_in_topic = db.add_stored(
        learner_id,
        "task.created",
        {
            "task_id": str(uuid4()),
            "markup": {
                "tests": [{"new_skill": {"name": "Empty in topic"}, "level": 3}],
                "parts": [],
            },
        },
        session_id=session_id,
    )
    sk_in_topic = UUID(deterministic_id(learner_id, seq_in_topic, "skill:empty in topic"))

    # Birth 2: a provisional skill no task tests, unassigned, 0 evidence & 0 exposures ->
    # should emit skill.deleted
    sk_unassigned = uuid4()
    db.add_stored(
        learner_id,
        "skill.created",
        {"skill_id": str(sk_unassigned), "name": "Empty unassigned", "status": "provisional"},
        session_id=session_id,
    )

    # Birth 3: in observation.extracted WITH evidence -> NOT empty, kept for Structure LLM
    seq_with_ev = db.add_stored(
        learner_id,
        "observation.extracted",
        {
            "elements": [
                {"kind": "evidence", "skill_id": None, "new_skill": {"name": "Observed skill"}}
            ]
        },
        session_id=session_id,
    )
    sk_with_ev = UUID(deterministic_id(learner_id, seq_with_ev, "skill:observed skill"))

    db.add_stored(
        learner_id, "session.ended", {"session_id": str(session_id)}, session_id=session_id
    )

    graph = DomainGraph(
        domain_name="Math",
        topics=[
            TopicNode(
                topic_id,
                "Limits",
                "active",
                core_skill_id=core_id,
                core_skill_name="computes limits",
                skill_ids=(core_id, sk_in_topic),
            )
        ],
        skills=[
            SkillNode(core_id, "computes limits", "active", [], core=True, topic_id=topic_id),
            SkillNode(
                sk_in_topic, "Empty in topic", "provisional", [], core=False, topic_id=topic_id
            ),
            SkillNode(
                sk_unassigned, "Empty unassigned", "provisional", [], core=False, topic_id=None
            ),
            SkillNode(
                sk_with_ev, "Observed skill", "provisional", [], core=False, topic_id=topic_id
            ),
        ],
        unassigned_skills=[UnassignedSkillNode(sk_unassigned, "Empty unassigned", "provisional")],
    )

    def respond(_: dict[str, Any], __: int) -> StructureOutput:
        # Only sk_with_ev remains for Structure LLM!
        return StructureOutput(
            topic_candidates=[],
            provisional_skills=[
                ProvisionalSkillDecision(
                    skill="S2",
                    action="keep",
                    target_skill=None,
                    target_topic=None,
                    definition="Observed limit skill",
                    exemplar="Find lim x->0",
                    kind="procedure",
                    topic_name=None,
                    topic_description=None,
                    core_skill=None,
                )
            ],
        )

    gw = _StructureGateway(respond)
    await run_hygiene(_fake_ctx(db, gw), graph, learner_id=learner_id, domain_id=DOMAIN)

    by_type: dict[str, list[NewEvent]] = {}
    for e in written:
        by_type.setdefault(e.type, []).append(e)

    assert len(by_type["skill.merged"]) == 1
    merged_p = cast(SkillMerged, by_type["skill.merged"][0].payload)
    assert merged_p.from_id == sk_in_topic
    assert merged_p.into_id == core_id
    assert (
        by_type["skill.merged"][0].idempotency_key
        == f"hygiene:empty_birth:skill.merged:{sk_in_topic}:{core_id}"
    )

    assert len(by_type["skill.deleted"]) == 1
    deleted_p = cast(SkillDeleted, by_type["skill.deleted"][0].payload)
    assert deleted_p.skill_id == sk_unassigned
    assert deleted_p.reason == "empty_session_birth"
    assert (
        by_type["skill.deleted"][0].idempotency_key
        == f"hygiene:empty_birth:skill.deleted:{sk_unassigned}"
    )

    assert len(by_type["skill.created"]) == 1
    created_p = cast(SkillCreated, by_type["skill.created"][0].payload)
    assert created_p.skill_id == sk_with_ev
    assert created_p.status == "active"


async def test_empty_births_count_pending_answers_and_tested_skills() -> None:
    """A pending answer is work on the skills of its task; a skill some task tests is reported
    as tested (never deleted); a run named by one session judges every ended session too (the
    hygiene jobs of a domain coalesce)."""
    learner_id, ended, other = uuid4(), uuid4(), uuid4()
    db = _FakeDb()
    task_answered, task_idle = uuid4(), uuid4()
    seq_answered = db.add_stored(
        learner_id,
        "task.created",
        {
            "task_id": str(task_answered),
            "markup": {"tests": [{"new_skill": {"name": "Answered"}, "level": 3}], "parts": []},
        },
        session_id=ended,
    )
    seq_idle = db.add_stored(
        learner_id,
        "task.created",
        {
            "task_id": str(task_idle),
            "markup": {"tests": [{"new_skill": {"name": "Idle"}, "level": 3}], "parts": []},
        },
        session_id=ended,
    )
    # an answer with no verdict yet, and not from a card
    db.add_stored(learner_id, "answer.submitted", {"task_id": str(task_answered)}, session_id=ended)
    other_skill = uuid4()
    db.add_stored(
        learner_id,
        "skill.created",
        {"skill_id": str(other_skill), "name": "Other", "status": "provisional"},
        session_id=other,
    )
    db.add_stored(learner_id, "session.ended", {"session_id": str(other)}, session_id=other)
    answered = UUID(deterministic_id(learner_id, seq_answered, "skill:answered"))
    idle = UUID(deterministic_id(learner_id, seq_idle, "skill:idle"))

    births, ended_set, non_empty, tested = await _find_ended_empty_births(
        cast(Db, db), learner_id, session_id=ended
    )

    assert ended_set == {ended, other}
    assert births[answered] == ended and births[idle] == ended and births[other_skill] == other
    assert answered in non_empty and idle not in non_empty
    assert {answered, idle} <= tested and other_skill not in tested


async def test_material_candidate_creates_a_proposed_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A topic found in a material, with no work of the learner yet, is `topic.proposed`
    (Т-§4.7); its core skill is created with it."""
    learner_id = uuid4()
    db = _FakeDb()
    written = _patch_append(monkeypatch, db)
    graph = DomainGraph(domain_name="Math", topics=[], skills=[])

    def respond(_: dict[str, Any], __: int) -> StructureOutput:
        return StructureOutput(
            topic_candidates=[
                TopicCandidateDecision(
                    candidate_index=0,
                    action="create",
                    target_topic=None,
                    name="Series",
                    description="Infinite sums",
                    core_skill=None,
                    requires=[],
                )
            ],
            provisional_skills=[],
        )

    res = await resolve_material_structure(
        _fake_ctx(db, _StructureGateway(respond)),
        learner_id=learner_id,
        domain_id=DOMAIN,
        candidates=[
            MaterialTopicCandidate(
                name="Series", description="Infinite sums", core_skill_name="sums series"
            )
        ],
        graph=graph,
    )
    topic_events = [e for e in written if e.type.startswith("topic.")]
    assert [e.type for e in topic_events] == ["topic.proposed"]
    assert topic_events[0].idempotency_key == f"structure:topic.proposed:{res[0].topic_id}"
    assert res[0].skill_names == ("sums series",)


async def test_new_topics_of_one_material_can_require_each_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New topics of one batch have no `S…` label yet: a claim names another candidate as
    `C<index>`, resolved to its core skill once every candidate has one."""
    learner_id = uuid4()
    db = _FakeDb()
    written = _patch_append(monkeypatch, db)
    graph = DomainGraph(domain_name="Physics", topics=[], skills=[])
    seen: list[dict[str, Any]] = []

    def create(index: int, name: str, requires: list[RequiresClaim]) -> TopicCandidateDecision:
        return TopicCandidateDecision(
            candidate_index=index,
            action="create",
            target_topic=None,
            name=name,
            description=name,
            core_skill=None,
            requires=requires,
        )

    def respond(variables: dict[str, Any], _: int) -> StructureOutput:
        seen.append(variables)
        return StructureOutput(
            topic_candidates=[
                create(0, "Kinematics", []),
                create(
                    1,
                    "Dynamics",
                    [RequiresClaim(target_core_skill_label="C0", score=0.9, origin="model")],
                ),
            ],
            provisional_skills=[],
        )

    res = await resolve_material_structure(
        _fake_ctx(db, _StructureGateway(respond)),
        learner_id=learner_id,
        domain_id=DOMAIN,
        candidates=[
            MaterialTopicCandidate(
                name="Kinematics", description="Motion", core_skill_name="describes motion"
            ),
            MaterialTopicCandidate(
                name="Dynamics", description="Forces", core_skill_name="applies Newton's laws"
            ),
        ],
        graph=graph,
    )
    assert "[1] C1 Dynamics" in str(seen[0])
    edges = [cast(EdgeAdded, e.payload) for e in written if e.type == "edge.added"]
    assert [(e.from_id, e.to_id, e.origin) for e in edges] == [
        (res[1].core_skill_id, res[0].core_skill_id, "model")
    ]


async def test_structure_topics_over_limit_shows_all_and_logs_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    learner_id = uuid4()
    db = _FakeDb()
    _patch_append(monkeypatch, db)
    topics = []
    skills = []
    for i in range(5):
        tid, sid = uuid4(), uuid4()
        topics.append(
            TopicNode(
                tid,
                f"Topic {i}",
                "active",
                core_skill_id=sid,
                core_skill_name=f"skill {i}",
                skill_ids=(sid,),
            )
        )
        skills.append(SkillNode(sid, f"skill {i}", "active", [], core=True, topic_id=tid))
    graph = DomainGraph(domain_name="Math", topics=topics, skills=skills)

    def respond(_: dict[str, Any], __: int) -> StructureOutput:
        return StructureOutput(
            topic_candidates=[
                TopicCandidateDecision(
                    candidate_index=0,
                    action="link",
                    target_topic="T1",
                    name=None,
                    description=None,
                    core_skill=None,
                    requires=[],
                )
            ],
            provisional_skills=[],
        )

    small = SETTINGS.model_copy(update={"structure_full_list_max_topics": 3})
    gw = _StructureGateway(respond)
    await resolve_material_structure(
        _fake_ctx(db, gw, settings=small),
        learner_id=learner_id,
        domain_id=DOMAIN,
        candidates=[
            MaterialTopicCandidate(
                name="Topic 0",
                description="Desc",
                core_skill_name="skill 0",
            )
        ],
        graph=graph,
    )
    out_log = capsys.readouterr().out
    assert "structure_topics_over_limit" in out_log
    shown = str(gw.calls[0])
    assert all(f"Topic {i}" in shown for i in range(5))
