"""Background graph curation worker `graph.curate`: active-session presence deferral,
deterministic provisional skill promotion and normName deduplication, and `curator@1`
model-assisted merges (ТЗ §2.5, §4; spec §7; Т-§10).
"""

from __future__ import annotations

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.jobs.queue import ClaimedJob
from quack.jobs.runner import JobContext, register_job_handler
from quack.llm.gateway import LLMGateway
from quack.llm.provider import LLMProvider

CURATION_DEFER_MINUTES = 10
CURATION_MIN_EVIDENCE_FOR_PROMOTION = 2

# Module-level hook allowing tests to inject a FakeLLMProvider when running via JobRunner.
llm_provider_override: LLMProvider | None = None


async def curate_domain_graph(
    job: ClaimedJob,
    ctx: JobContext,
    *,
    driver: AsyncDriver | None = None,
    redis: Redis | None = None,
    llm: LLMGateway | None = None,
) -> None:
    """Execute `graph.curate` for a domain: defer by 10 minutes if `presence:learner:{id}` is
    active in Redis; otherwise run deterministic promotion/deduplication and `curator@1`
    merges, emitting `skill.promoted`, `skill.merged`, `topic.merged`, `misconception.merged`
    (ТЗ §2.5, §4; Т-§10).
    """
    raise NotImplementedError


@register_job_handler("graph.curate")
async def handle_graph_curate(job: ClaimedJob, ctx: JobContext) -> None:
    """Registered `ops.jobs` handler for `graph.curate` delegating to `curate_domain_graph`
    (ТЗ §2.5, spec §7, Т-§10).
    """
    raise NotImplementedError
