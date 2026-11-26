"""Handler of the job `material.parse` (ТЗ Ф4 WP20.2; D-C-200).

Glue of the assembly layer: `quack.materials` (a product package) may not import
`quack.agents` (§4.1), so the structure step of the parse gets the reconciliation of stream B as
a function from here, and so do the seminar tasks of stream B (`quack.practice`, #271,
D-C-217): they are made by their own job `material.seminar_tasks`, queued by the structure step,
outside the lock of the domain. The steps themselves are `quack.materials.parse`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from quack.agents import hygiene
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
    SEMINAR_TASKS_KIND,
    STEP_STRUCTURE,
    ResolvedTopic,
    SeminarSpec,
    TopicCandidateSpec,
    mark_failed,
    mark_material_failed,
    parse_step,
    seminar_tasks_step,
    structure_step,
)
from quack.practice.seminar_tasks import SeminarTask, create_seminar_task, seminar_task_id

# What the reconciliation reads from Neo4j: the nodes of the domain, their vectors and edges.
_GRAPH_TYPES = (
    "topic.created",
    "topic.proposed",
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
    "AND type IN ('topic.created', 'topic.proposed', 'skill.created') "
    "AND payload->>'domain_id' = %(domain_id)s "
    "ORDER BY seq"
)
_MADE_TASKS = (
    "SELECT task_id FROM content.tasks WHERE learner_id = %(learner_id)s AND task_id = ANY(%(ids)s)"
)
_POLL_S = 0.25


def node_text(event_type: str, payload: dict[str, Any]) -> EmbeddingNode:
    """What is embedded (decision of 2026-11-24 on the `[ПРОВЕРИТЬ]` of Т-§3.4.2): a topic by its
    name, a skill by its name and definition (or summary for legacy events) — the words its
    candidates are searched with."""
    if event_type in ("topic.created", "topic.proposed"):
        return EmbeddingNode("topic", UUID(str(payload["topic_id"])), str(payload["name"]))
    body = payload.get("definition") or payload.get("summary") or ""
    text = "\n".join((str(payload["name"]), str(body))).strip()
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


async def resolve_structure_candidates(
    ctx: JobContext,
    *,
    learner_id: UUID,
    domain_id: UUID,
    candidates: Sequence[TopicCandidateSpec],
    source_refs: Sequence[dict[str, Any]] = (),
) -> list[ResolvedTopic]:
    """Resolve TOC topic candidates via `quack.agents.hygiene.resolve_material_structure`.
    Before the call every node of the domain has its vector and Neo4j holds it, so the
    structure writer sees the topics and skills of the domain as candidates."""
    await embed_domain_nodes(ctx, learner_id=learner_id, domain_id=domain_id)
    await wait_projected(ctx, learner_id=learner_id)
    material_candidates = [
        hygiene.MaterialTopicCandidate(
            name=c.name,
            description=c.description,
            core_skill_name=c.core_skill_name,
            builds_on=tuple(
                hygiene.MaterialBuildsOn(concept=b.concept, quote=b.quote) for b in c.builds_on
            ),
        )
        for c in candidates
    ]
    resolved = await hygiene.resolve_material_structure(
        ctx,
        learner_id=learner_id,
        domain_id=domain_id,
        candidates=material_candidates,
        source_refs=source_refs,
    )
    return [
        ResolvedTopic(
            topic_id=r.topic_id,
            core_skill_id=r.core_skill_id,
            skill_ids=tuple(r.skill_ids),
            skill_names=tuple(r.skill_names),
        )
        for r in resolved
    ]


async def create_seminar_tasks(
    ctx: JobContext, *, learner_id: UUID, tasks: Sequence[SeminarSpec]
) -> None:
    """`create_seminar_task` of stream B for every problem of the material, at most
    `material_parser_concurrency` at a time. A task already made is skipped before any model call
    (its id comes from its key; a repeat of the call could word it differently and meet its own
    key with another payload); a task it throws away (`None`) is not an error. A task that fails
    does not take the others with it: every one is tried, the made ones stay (each is its own
    transaction), and the first error goes up afterwards, so the job is retried and goes on with
    what is left (law 7, law 5)."""
    ids = {seminar_task_id(spec.key): spec for spec in tasks}
    rows = await ctx.db.fetch_all(_MADE_TASKS, {"learner_id": learner_id, "ids": list(ids)})
    made = {row["task_id"] for row in rows}
    todo = [spec for task_id, spec in ids.items() if task_id not in made]
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
                    topic_id=spec.topic_id,
                    topic_skill_ids=spec.topic_skill_ids,
                    topic_skill_names={
                        sid: name
                        for sid, name in zip(
                            spec.topic_skill_ids, spec.topic_skill_names, strict=False
                        )
                        if name
                    },
                ),
                learner_id=learner_id,
                compute=ctx.compute,
            )

    outcomes = await asyncio.gather(*(create(spec) for spec in todo), return_exceptions=True)
    failed = [o for o in outcomes if isinstance(o, BaseException)]
    if failed:
        raise failed[0]


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
            await structure_step(ctx, job, resolve_structure_candidates)
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


@register_job_handler(SEMINAR_TASKS_KIND)
async def handle_material_seminar_tasks(job: ClaimedJob, ctx: JobContext) -> None:
    """Seminar tasks of a parsed material. An error goes up and the job is retried with a growing
    pause; the material is not marked (it is `parsed`, its graph stands), and there is no
    failure hook."""
    await seminar_tasks_step(ctx, job, create_seminar_tasks)
