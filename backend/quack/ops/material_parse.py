"""Handler of the job `material.parse` (ТЗ Ф4 WP20.2; D-C-200).

Glue of the assembly layer: `quack.materials` (a product package) may not import
`quack.agents` (§4.1), so the structure step of the parse gets the reconciliation of stream B as
a function from here, and so do the seminar tasks of stream B (`quack.practice`, #271,
D-C-217). The steps themselves are `quack.materials.parse`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from quack.agents.reconcile import Subject, reconcile_domain
from quack.db import Db
from quack.embeddings.client import EmbeddingsUnavailable
from quack.embeddings.compute import EmbeddingNode, compute_embeddings
from quack.graph.projection.store import read_watermark
from quack.jobs.queue_p4 import ClaimedJob as QueuedJob
from quack.jobs.queue_p4 import register_failure_hook
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob, JobDeferred, JobFailedPermanently
from quack.llm.errors import LLMBudgetExceededError
from quack.materials.parse import (
    STEP_STRUCTURE,
    SeminarSpec,
    SubjectSpec,
    mark_failed,
    mark_material_failed,
    parse_step,
    structure_step,
)
from quack.practice.seminar_tasks import SeminarTask, create_seminar_task

# What the reconciliation reads from Neo4j: the nodes of the domain, their vectors and edges.
_GRAPH_TYPES = (
    "topic.created",
    "skill.created",
    "skill.placed",
    "topic.merged",
    "skill.merged",
    "edge.added",
    "embedding.ready",
)
_LAST_GRAPH_SEQ = (
    "SELECT max(seq) AS seq FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = ANY(%(types)s)"
)
_DOMAIN_NODES = (
    "SELECT type, payload FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type IN ('topic.created', 'skill.created') AND payload->>'domain_id' = %(domain_id)s "
    "ORDER BY seq"
)
_POLL_S = 0.25


def node_text(event_type: str, payload: dict[str, Any]) -> EmbeddingNode:
    """What is embedded (decision of 2026-11-24 on the `[ПРОВЕРИТЬ]` of Т-§3.4.2): a topic by its
    name, a skill by its name and summary — the words its candidates are searched with."""
    if event_type == "topic.created":
        return EmbeddingNode("topic", UUID(str(payload["topic_id"])), str(payload["name"]))
    text = "\n".join((str(payload["name"]), str(payload.get("summary") or ""))).strip()
    return EmbeddingNode("skill", UUID(str(payload["skill_id"])), text)


async def embed_domain_nodes(ctx: JobContext, *, learner_id: UUID, domain_id: UUID) -> None:
    """Vectors for the topics and skills of the domain that have none yet. The candidates of the
    reconciliation are found only by vector (`topic_emb`, `skill_emb`): a node without one is
    invisible, and the builder creates a twin of it instead of a link (bug of 2026-11-24)."""
    rows = await ctx.db.fetch_all(
        _DOMAIN_NODES, {"learner_id": learner_id, "domain_id": str(domain_id)}
    )
    nodes = [node_text(r["type"], r["payload"]) for r in rows]
    if not nodes:
        return
    try:
        await compute_embeddings(
            ctx.db, ctx.embeddings, learner_id=learner_id, nodes=nodes, redis=ctx.redis
        )
    except EmbeddingsUnavailable as exc:
        raise JobDeferred(ctx.settings.job_backoff_max_sec, "embeddings unavailable") from exc


async def wait_projected(ctx: JobContext, *, learner_id: UUID) -> None:
    """Wait until Neo4j holds every node, vector and edge written so far: the previous call (or
    the previous material) must be visible to the next one. Too long — the job runs again later
    without spending an attempt."""
    if ctx.neo4j is None:
        return
    row = await ctx.db.fetch_one(
        _LAST_GRAPH_SEQ, {"learner_id": learner_id, "types": list(_GRAPH_TYPES)}
    )
    target = int(row["seq"]) if row is not None and row["seq"] is not None else 0
    if target <= 0:
        return
    deadline = time.monotonic() + ctx.settings.material_projection_wait_s
    while True:
        mark = await read_watermark(ctx.neo4j, str(learner_id))
        if mark is not None and mark[0] >= target:
            return
        if time.monotonic() >= deadline:
            raise JobDeferred(ctx.settings.material_order_retry_s, "the projector is behind")
        await asyncio.sleep(_POLL_S)


async def reconcile_subjects(
    ctx: JobContext, *, learner_id: UUID, domain_id: UUID, subjects: Sequence[SubjectSpec]
) -> dict[tuple[str, str], UUID]:
    """`reconcile_domain` for the subjects of the material: the id of the node of each one it
    linked or created, by `(kind, name)`; the origin of the edges is `material`. Before the call
    every node of the domain has its vector and Neo4j holds it, so the builder sees the topics
    and skills of the earlier lectures and of the previous batch as candidates."""
    await embed_domain_nodes(ctx, learner_id=learner_id, domain_id=domain_id)
    await wait_projected(ctx, learner_id=learner_id)
    result = await reconcile_domain(
        ctx,
        learner_id=learner_id,
        domain_id=domain_id,
        subjects=[Subject(s.name, s.kind, s.text, s.topic, s.requires) for s in subjects],
        origin="material",
    )
    return {
        (r.subject.kind, r.subject.name): r.node_id
        for r in result.resolutions
        if r.node_id is not None
    }


async def create_seminar_tasks(
    ctx: JobContext, *, learner_id: UUID, tasks: Sequence[SeminarSpec]
) -> None:
    """`create_seminar_task` of stream B for every problem of the material, at most
    `material_parser_concurrency` at a time. A task it throws away (`None`) is not an error; an
    error of the model goes up and the job is retried (the keys make the retry write nothing
    twice)."""
    gate = asyncio.Semaphore(ctx.settings.material_parser_concurrency)

    async def create(spec: SeminarSpec) -> None:
        async with gate:
            await create_seminar_task(
                ctx.db,
                ctx.llm,
                ctx.settings,
                SeminarTask(
                    key=spec.key,
                    statement=spec.statement,
                    skill_id=spec.skill_id,
                    skill_name=spec.skill_name,
                    topic_name=spec.topic_name,
                    answer=spec.answer,
                    course_context=spec.course_context,
                    domain_id=spec.domain_id,
                ),
                learner_id=learner_id,
                compute=ctx.compute,
            )

    await asyncio.gather(*(create(spec) for spec in tasks))


def _is_final(error: Exception, job: ClaimedJob) -> bool:
    """The runner will not retry this error: a permanent one, a session budget, or the last
    attempt (the same rule as `fail_or_retry`). A deferral is not a failure."""
    if isinstance(error, JobDeferred):
        return False
    if isinstance(error, LLMBudgetExceededError):
        return error.scope == "session"
    return isinstance(error, JobFailedPermanently) or job.attempt >= job.max_attempts


@register_job_handler("material.parse")
async def handle_material_parse(job: ClaimedJob, ctx: JobContext) -> None:
    try:
        if job.payload.get("step") == STEP_STRUCTURE:
            await structure_step(ctx, job, reconcile_subjects, create_seminar_tasks)
        else:
            await parse_step(ctx, job)
    except Exception as error:
        if _is_final(error, job):
            await mark_failed(ctx.db, job)
        raise


@register_failure_hook("material.parse")
async def mark_material_parse_failed(db: Db, job: QueuedJob) -> None:
    """The queue gave up on the job for good, also without the handler seeing it (a heartbeat
    timeout on the last attempt): the material is `failed`, a parsed one is left alone."""
    await mark_material_failed(db, job.learner_id, job.payload)
