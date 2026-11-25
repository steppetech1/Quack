"""Graph hygiene, WP16.3 (ТЗ Ф4; Т-§4.10; `T-AG4`): a duplicate with a temporary skill is
merged, two active duplicates are only suggested, misconceptions merge within a skill. D-B-185."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from pydantic import BaseModel

from quack.agents.hygiene import (
    DomainGraph,
    MisconceptionNode,
    SkillNode,
    TopicNode,
    UnassignedSkillNode,
    cleanup_duplicate_topics,
    deterministic_topic_cleanup_plan,
    run_hygiene,
)
from quack.agents.schemas import (
    CoreSkillSpec,
    DuplicateVerdict,
    HygieneOutput,
    ProvisionalSkillDecision,
    StructureOutput,
)
from quack.config import Settings
from quack.db import Db
from quack.graph.topic_names import canonical_topic_name
from quack.jobs.runner import JobContext
from quack.llm.gateway import LLMResult, LLMUsage
from tests.db.fixtures import create_learner
from tests.support import graph_read as fx

SETTINGS = Settings()


@dataclass
class _Gateway:
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
        pairs = [line.split(" | ") for line in variables["subjects"].splitlines()]
        verdicts = [DuplicateVerdict(first=a, second=b, duplicate=True) for a, b in pairs]
        return LLMResult(
            value=HygieneOutput(pairs=verdicts),
            from_cache=False,
            prompt_hash="h",
            model="m",
            usage=None,
        )


async def test_hygiene_merges_only_provisional(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    active_a, active_b, temp, active_c, far = (uuid4() for _ in range(5))
    m1, m2 = uuid4(), uuid4()
    graph = DomainGraph(
        skills=[
            SkillNode(active_a, "fractions add", "active", [1.0, 0.0, 0.0]),
            SkillNode(active_b, "adding fractions", "active", [0.99, 0.05, 0.0]),
            SkillNode(temp, "chain rule", "provisional", [0.0, 1.0, 0.0], frozenset({"t"})),
            SkillNode(
                active_c, "chain rule derivative", "active", [0.0, 0.97, 0.1], frozenset({"t"})
            ),
            SkillNode(far, "limits", "active", [0.0, 0.0, 1.0]),
        ],
        misconceptions=[
            MisconceptionNode(m1, "adds denominators", active_a, [1.0, 0.0]),
            MisconceptionNode(m2, "sums the bottoms", active_a, [0.98, 0.1]),
        ],
    )
    gateway = _Gateway()
    ctx = cast(JobContext, SimpleNamespace(db=pg_db, llm=gateway, settings=SETTINGS, neo4j=None))

    written = await run_hygiene(ctx, graph, learner_id=learner_id, domain_id=fx.DOMAIN)

    assert sorted(written) == ["misconception.merged", "skill.merge_suggested", "skill.merged"]
    rows = await pg_db.fetch_all(
        "SELECT type, payload FROM core.events WHERE learner_id = %(l)s ORDER BY seq",
        {"l": learner_id},
    )
    by_type = {r["type"]: r["payload"] for r in rows}
    assert by_type["skill.merged"] == {"from_id": str(temp), "into_id": str(active_c)}
    assert {
        by_type["skill.merge_suggested"]["from_id"],
        by_type["skill.merge_suggested"]["into_id"],
    } == {str(active_a), str(active_b)}
    assert str(far) not in str(gateway.calls[0]["subjects"])


def test_canonical_topic_name_and_five_topic_cleanup_plan() -> None:
    assert canonical_topic_name("Statistics: binomial distribution", "Statistics") == (
        "binomial distribution"
    )
    assert canonical_topic_name("Statistics:   normal distribution ", "statistics") == (
        "normal distribution"
    )
    assert canonical_topic_name("Statistics", "Statistics") == "Statistics"

    t_binom_prefixed = TopicNode(uuid4(), "Statistics: binomial distribution", "proposed")
    t_binom = TopicNode(uuid4(), "binomial distribution", "active")
    t_binom_basics = TopicNode(uuid4(), "binomial distribution basics", "active")
    t_norm_prefixed = TopicNode(uuid4(), "Statistics: normal distribution", "proposed")
    t_norm = TopicNode(uuid4(), "normal distribution", "active")

    merges, renames = deterministic_topic_cleanup_plan(
        [t_binom_prefixed, t_binom, t_binom_basics, t_norm_prefixed, t_norm],
        domain_name="Statistics",
    )
    assert set(merges) == {
        (t_binom_prefixed.topic_id, t_binom.topic_id),
        (t_binom_basics.topic_id, t_binom.topic_id),
        (t_norm_prefixed.topic_id, t_norm.topic_id),
    }
    assert renames == []


@dataclass
class _TopicsGateway:
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
        self.calls.append({"role": role, "model": response_model.__name__, **variables})
        assert response_model is StructureOutput
        # Deterministic topic cleanup merged T1 ("Statistics: binomial distribution") into
        # T2 ("binomial distribution") before Structure mode ran!
        # Remaining topics: T1 = "binomial distribution" [active],
        # T2 = "normal distribution" [active]
        # S2 fits T1 (move), S3 creates a new canonical topic (accidental domain prefix stripped)
        out = StructureOutput(
            topic_candidates=[],
            provisional_skills=[
                ProvisionalSkillDecision(
                    skill="S2",
                    action="move",
                    target_skill=None,
                    target_topic="T1",
                    definition="Mean of binomial is np",
                    exemplar="Find E[X] for Bin(10, 0.3)",
                    kind="fact",
                    topic_name=None,
                    topic_description=None,
                    core_skill=None,
                ),
                ProvisionalSkillDecision(
                    skill="S3",
                    action="core_of_new_topic",
                    target_skill=None,
                    target_topic=None,
                    definition="Computes Poisson PMF",
                    exemplar="Find P(X=2) for Poisson(3)",
                    kind="procedure",
                    topic_name="Statistics: Poisson distribution",
                    topic_description="Discrete count distribution",
                    core_skill=CoreSkillSpec(
                        name="Poisson lambda",
                        definition="Computes Poisson PMF",
                        exemplar="Find P(X=2) for Poisson(3)",
                        kind="procedure",
                    ),
                ),
            ],
        )
        return LLMResult(
            value=out,
            from_cache=False,
            prompt_hash="h",
            model="m",
            usage=LLMUsage(input_tokens=120, output_tokens=45, cached_tokens=10, latency_ms=50),
        )


async def test_hygiene_places_unassigned_skills_merges_topics_and_logs_tokens(
    pg_db: Db, capsys: Any
) -> None:
    learner_id = await create_learner(pg_db)
    t_prefixed, t_binom, t_norm = uuid4(), uuid4(), uuid4()
    s_core, s1, s2 = uuid4(), uuid4(), uuid4()
    graph = DomainGraph(
        domain_name="Statistics",
        topics=[
            TopicNode(t_prefixed, "Statistics: binomial distribution", "proposed"),
            TopicNode(
                t_binom,
                "binomial distribution",
                "active",
                skill_names=("Binomial PMF",),
                core_skill_id=s_core,
                core_skill_name="Binomial PMF",
                skill_ids=(s_core,),
            ),
            TopicNode(t_norm, "normal distribution", "active"),
        ],
        skills=[
            SkillNode(s_core, "Binomial PMF", "active", [], core=True, topic_id=t_binom),
        ],
        unassigned_skills=[
            UnassignedSkillNode(s1, "Binomial mean np", "provisional"),
            UnassignedSkillNode(s2, "Poisson lambda", "provisional"),
        ],
    )
    gateway = _TopicsGateway()
    ctx = cast(JobContext, SimpleNamespace(db=pg_db, llm=gateway, settings=SETTINGS, neo4j=None))

    written = await run_hygiene(ctx, graph, learner_id=learner_id, domain_id=fx.DOMAIN)
    assert written == [
        "topic.merged",
        "skill.placed",
        "skill.created",
        "topic.created",
        "skill.placed",
        "skill.created",
    ]

    rows = await pg_db.fetch_all(
        "SELECT type, payload FROM core.events WHERE learner_id = %(l)s ORDER BY seq",
        {"l": learner_id},
    )
    assert rows[0]["type"] == "topic.merged"
    assert rows[0]["payload"] == {"from_id": str(t_prefixed), "into_id": str(t_binom)}
    assert rows[1]["type"] == "skill.placed"
    assert rows[1]["payload"]["topic_id"] == str(t_binom)
    assert rows[3]["type"] == "topic.created"
    # Safety net stripped "Statistics: " prefix
    assert rows[3]["payload"]["name"] == "Poisson distribution"
    assert rows[4]["type"] == "skill.placed"
    assert rows[4]["payload"]["topic_id"] == rows[3]["payload"]["topic_id"]

    captured = capsys.readouterr().out
    assert "builder_hygiene_completed" in captured
    assert "structure_writer_completed" in captured
    assert '"calls": 1' in captured or "calls=1" in captured
    assert '"input_tokens": 120' in captured or "input_tokens=120" in captured
    assert '"output_tokens": 45' in captured or "output_tokens=45" in captured


async def test_cleanup_duplicate_topics_writes_idempotent_merges(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    t1, t2, t3 = uuid4(), uuid4(), uuid4()

    class _FakeResult:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows

        async def single(self) -> dict[str, Any] | None:
            return self._rows[0] if self._rows else None

        def __aiter__(self) -> Any:
            return self._aiter()

        async def _aiter(self) -> Any:
            for r in self._rows:
                yield r

    class _FakeSession:
        def __init__(self) -> None:
            self.n = 0

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def run(self, query: Any, **params: Any) -> _FakeResult:
            self.n += 1
            if "d.name" in str(query):
                return _FakeResult([{"name": "Statistics"}])
            if "t.embedding" in str(query):
                return _FakeResult(
                    [
                        {
                            "id": str(t1),
                            "name": "Statistics: binomial distribution",
                            "status": "proposed",
                            "embedding": None,
                            "skill_names": [],
                        },
                        {
                            "id": str(t2),
                            "name": "binomial distribution",
                            "status": "active",
                            "embedding": None,
                            "skill_names": [],
                        },
                        {
                            "id": str(t3),
                            "name": "binomial distribution basics",
                            "status": "active",
                            "embedding": None,
                            "skill_names": [],
                        },
                    ]
                )
            return _FakeResult([])

    class _FakeDriver:
        def session(self, **kwargs: Any) -> _FakeSession:
            return _FakeSession()

    merges = await cleanup_duplicate_topics(
        pg_db, cast(Any, _FakeDriver()), learner_id=learner_id, domain_id=fx.DOMAIN
    )
    assert set(merges) == {(t1, t2), (t3, t2)}
    rows = await pg_db.fetch_all(
        "SELECT type, payload FROM core.events WHERE learner_id = %(l)s ORDER BY seq",
        {"l": learner_id},
    )
    assert [r["type"] for r in rows] == ["topic.merged", "topic.merged"]
