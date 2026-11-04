"""Reconciliation of the graph, WP16.3 (ТЗ Ф4; Т-§4.3, Т-§4.6; `T-AG3`): candidates by code,
the model chooses link / create / suggest_merge / same idea, code writes the events, never a
counter edge, a repeat writes nothing twice. D-B-205."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from neo4j import AsyncDriver
from pydantic import BaseModel

from quack.agents.reconcile import (
    Candidate,
    Subject,
    build_labels,
    find_candidates,
    read_requires,
    reconcile_with_candidates,
)
from quack.agents.schemas import BuilderOutput, ReconciledItem
from quack.config import Settings
from quack.db import Db
from quack.jobs.runner import JobContext
from quack.llm.gateway import LLMResult
from tests.db.fixtures import create_learner
from tests.support import graph_read as fx

SETTINGS = Settings()
OTHER_DOMAIN = uuid4()


@dataclass
class _Gateway:
    answer: Callable[[dict[str, Any]], BuilderOutput]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_structured(
        self,
        role: str,
        prompt_version: int,
        response_model: type[BaseModel],
        variables: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> LLMResult[Any]:
        self.calls.append(variables)
        return LLMResult(
            value=self.answer(variables), from_cache=False, prompt_hash="h", model="m", usage=None
        )


def _item(name: str, kind: str, decision: str, **extra: Any) -> ReconciledItem:
    fields: dict[str, Any] = {
        "target": None,
        "topic_hint": None,
        "same_idea": None,
        "same_idea_confidence": None,
    }
    return ReconciledItem.model_validate(
        {"name": name, "kind": kind, "decision": decision, **fields, **extra}
    )


async def test_reconciliation_events_no_counter_edge_and_repeat(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    t_old, s_chain, s_velocity = uuid4(), uuid4(), uuid4()
    here = fx.DOMAIN
    cands = [
        Candidate("topic", t_old, "Derivatives", here),
        Candidate("skill", s_chain, "Chain rule", here, None, t_old),
        Candidate("skill", s_velocity, "Instantaneous velocity", OTHER_DOMAIN, "Mechanics"),
    ]
    subjects = [
        Subject("Fractions", "topic", "adding and reducing"),
        Subject("Derivatives", "topic"),
        Subject("Chain rule", "skill"),
        Subject("Rate of change", "skill", "how fast a value changes"),
        Subject("Add fractions", "skill", requires=("Reduce fractions",)),
        Subject("Reduce fractions", "skill", requires=("Add fractions",)),
        Subject("Differentiating composites", "skill"),
    ]
    candidates = [cands] * len(subjects)
    labels = build_labels(SETTINGS, candidates)
    s1, s2, t1 = (
        labels.label("skill", s_chain),
        labels.label("skill", s_velocity),
        labels.label("topic", t_old),
    )

    def answer(_: dict[str, Any]) -> BuilderOutput:
        return BuilderOutput(
            items=[
                _item("Fractions", "topic", "create"),
                _item("Derivatives", "topic", "link", target=t1),
                _item("Chain rule", "skill", "link", target=s1),
                _item(
                    "Rate of change",
                    "skill",
                    "create",
                    topic_hint=t1,
                    same_idea=s2,
                    same_idea_confidence="high",
                ),
                _item("Add fractions", "skill", "create", topic_hint="Fractions"),
                _item("Reduce fractions", "skill", "create", topic_hint="Fractions"),
                _item(
                    "Differentiating composites",
                    "skill",
                    "suggest_merge",
                    target=s1,
                    topic_hint=t1,
                ),
            ]
        )

    gateway = _Gateway(answer)
    ctx = cast(
        JobContext,
        SimpleNamespace(db=pg_db, llm=gateway, settings=SETTINGS, neo4j=None, embeddings=None),
    )

    async def run() -> list[str]:
        result = await reconcile_with_candidates(
            ctx,
            learner_id=learner_id,
            domain_id=here,
            subjects=subjects,
            candidates=candidates,
            existing_requires=frozenset(),
        )
        return result.events

    first = await run()
    rows = await pg_db.fetch_all(
        "SELECT type, payload FROM core.events WHERE learner_id = %(l)s ORDER BY seq",
        {"l": learner_id},
    )
    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r["payload"])

    assert len(gateway.calls) == 1 and gateway.calls[0]["mode"] == "Reconcile"
    assert [p["name"] for p in by_type["topic.created"]] == ["Fractions"]
    assert sorted(p["name"] for p in by_type["skill.created"]) == [
        "Add fractions", "Differentiating composites", "Rate of change", "Reduce fractions",
    ]  # fmt: skip
    edges = by_type["edge.added"]
    requires = [e for e in edges if e["kind"] == "requires"]
    assert len(requires) == 1  # `Add → Reduce` only: no counter edge (Т-§4.6)
    assert requires[0]["origin"] == "material"
    same = [e for e in edges if e["kind"] == "same_idea"]
    assert len(same) == 1 and same[0]["to_id"] == str(s_velocity)
    assert same[0]["confidence"] == SETTINGS.same_idea_confidence["high"]
    assert by_type["skill.merge_suggested"][0]["into_id"] == str(s_chain)
    assert first.count("skill.created") == 4

    await run()  # a repeat adds nothing
    again = await pg_db.fetch_all(
        "SELECT seq FROM core.events WHERE learner_id = %(l)s", {"l": learner_id}
    )
    assert len(again) == len(rows)


async def test_counter_edge_to_the_stored_one_is_dropped(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    t_old, a_id, b_id = uuid4(), uuid4(), uuid4()
    cands = [
        Candidate("topic", t_old, "Algebra", fx.DOMAIN),
        Candidate("skill", a_id, "Add", fx.DOMAIN, None, t_old),
        Candidate("skill", b_id, "Multiply", fx.DOMAIN, None, t_old),
    ]
    subjects = [Subject("Add", "skill", requires=("Multiply",)), Subject("Multiply", "skill")]
    labels = build_labels(SETTINGS, [cands, cands])

    def answer(_: dict[str, Any]) -> BuilderOutput:
        return BuilderOutput(
            items=[
                _item("Add", "skill", "link", target=labels.label("skill", a_id)),
                _item("Multiply", "skill", "link", target=labels.label("skill", b_id)),
            ]
        )

    ctx = cast(
        JobContext,
        SimpleNamespace(
            db=pg_db, llm=_Gateway(answer), settings=SETTINGS, neo4j=None, embeddings=None
        ),
    )
    result = await reconcile_with_candidates(
        ctx,
        learner_id=learner_id,
        domain_id=fx.DOMAIN,
        subjects=subjects,
        candidates=[cands, cands],
        existing_requires=frozenset({(b_id, a_id)}),  # `Multiply REQUIRES Add` is stored
    )
    assert result.events == []
    assert [r.outcome for r in result.resolutions] == ["link", "link"]


# --- candidates from Neo4j -------------------------------------------------------------------


class _Embedder:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    async def embed_query(self, text: str) -> list[float]:
        return self.vector


def _vec(hot: int) -> list[float]:
    v = [0.0] * SETTINGS.embedding_dim
    v[hot] = 1.0
    return v


@pytest.fixture
async def learner_graph(neo4j_driver: AsyncDriver) -> AsyncIterator[tuple[AsyncDriver, UUID]]:
    learner = uuid4()
    yield neo4j_driver, learner
    async with neo4j_driver.session() as session:
        await session.run("MATCH (n) WHERE n.learnerId = $l DETACH DELETE n", l=str(learner))


async def _run(driver: AsyncDriver, query: str, **params: Any) -> None:
    async with driver.session() as session:
        await session.run(query, **params)  # type: ignore[arg-type]


async def test_candidates_vector_neighbours_and_other_domains(
    learner_graph: tuple[AsyncDriver, UUID],
) -> None:
    driver, learner = learner_graph
    domain, other = str(fx.DOMAIN), str(OTHER_DOMAIN)
    topic, skill_near, skill_mate, skill_req, skill_far, skill_other = (
        str(uuid4()) for _ in range(6)
    )
    await _run(
        driver,
        "CREATE (t:Topic {id: $t, learnerId: $l, domainId: $d, name: 'Derivatives', "
        "embedding: $v1}) "
        "CREATE (a:Skill {id: $a, learnerId: $l, domainId: $d, name: 'Chain rule', "
        "status: 'active', embedding: $v1}) "
        "CREATE (m:Skill {id: $m, learnerId: $l, domainId: $d, name: 'Product rule', "
        "status: 'active', embedding: $v5}) "
        "CREATE (r:Skill {id: $r, learnerId: $l, domainId: $d, name: 'Limits', "
        "status: 'active', embedding: $v6}) "
        "CREATE (f:Skill {id: $f, learnerId: $l, domainId: $d, name: 'Graph colouring', "
        "status: 'merged', embedding: $v1}) "
        "CREATE (o:Skill {id: $o, learnerId: $l, domainId: $od, name: 'Velocity', "
        "status: 'active', embedding: $v1}) "
        "CREATE (t)-[:COVERS]->(a) CREATE (t)-[:COVERS]->(m) CREATE (a)-[:REQUIRES]->(r)",
        l=str(learner), d=domain, od=other, t=topic, a=skill_near, m=skill_mate, r=skill_req,
        f=skill_far, o=skill_other, v1=_vec(1), v5=_vec(5), v6=_vec(6),
    )  # fmt: skip
    ctx = cast(
        JobContext,
        SimpleNamespace(settings=SETTINGS, neo4j=driver, embeddings=_Embedder(_vec(1))),
    )
    found = await find_candidates(
        ctx,
        learner_id=learner,
        domain_id=fx.DOMAIN,
        subjects=[Subject("Chain rule", "skill", "d/dx f(g(x))")],
    )
    names = {(c.kind, c.name): c for c in found[0]}
    assert ("topic", "Derivatives") in names
    assert ("skill", "Chain rule") in names
    assert ("skill", "Product rule") in names  # the same topic
    assert ("skill", "Limits") in names  # REQUIRES neighbour of the nearest skill
    assert ("skill", "Graph colouring") not in names  # merged
    assert names[("skill", "Velocity")].domain_id == OTHER_DOMAIN
    assert len(found[0]) <= SETTINGS.candidate_k
    pairs = await read_requires(driver, learner_id=learner, ids=[UUID(skill_near), UUID(skill_req)])
    assert pairs == {(UUID(skill_near), UUID(skill_req))}
