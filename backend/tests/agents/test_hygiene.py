"""Graph hygiene, WP16.3 (ТЗ Ф4; Т-§4.10; `T-AG4`): a duplicate with a temporary skill is
merged, two active duplicates are only suggested, misconceptions merge within a skill. D-B-185."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from pydantic import BaseModel

from quack.agents.hygiene import DomainGraph, MisconceptionNode, SkillNode, run_hygiene
from quack.agents.schemas import DuplicateVerdict, HygieneOutput
from quack.config import Settings
from quack.db import Db
from quack.jobs.runner import JobContext
from quack.llm.gateway import LLMResult
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
