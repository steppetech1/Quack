"""The job `embedding.compute` (ТЗ Ф4 WP7.1, seam S6; WP20.3; `Т-§3.1`, `Т-§11.9`).

The kind and the shape of its parameters are A's (S6, D-A-117: `{nodes: [{node_kind, node_id,
text}]}` — the text rides in the job); the body is C's `quack.embeddings.compute`. Modal down —
the job waits in the queue without spending an attempt (§4.4, row «Modal»: «задачи эмбеддингов
ждут в очереди»), again in `job_backoff_max_sec`.
"""

from __future__ import annotations

from uuid import UUID

from quack.embeddings.client import EmbeddingsUnavailable
from quack.embeddings.compute import EmbeddingNode, compute_embeddings
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob, JobDeferred


@register_job_handler("embedding.compute")
async def handle_embedding_compute(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None:
        raise ValueError(f"{job.kind} needs learner_id")
    nodes = [
        EmbeddingNode(node_kind=n["node_kind"], node_id=UUID(n["node_id"]), text=n["text"])
        for n in job.payload["nodes"]
    ]
    try:
        await compute_embeddings(
            ctx.db, ctx.embeddings, learner_id=job.learner_id, nodes=nodes, redis=ctx.redis
        )
    except EmbeddingsUnavailable as exc:
        raise JobDeferred(ctx.settings.job_backoff_max_sec, "embeddings unavailable") from exc
