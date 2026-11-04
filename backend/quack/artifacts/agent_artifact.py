"""The job `agent.artifact`: a request for the notes or the cards of a topic (ТЗ Ф4 WP23.2, §6.5;
Т-§17.7; issue #263; D-C-230).

The dispatcher puts the job for every `agent.requested {kind: artifact}` except the cheat sheet of
a hub (`artifact.cheatsheet`, WP23.5). The body is the one of `artifact.topic_refresh`
(`refresh_topic_notes`, `refresh_topic_cards`): the request names the artifact
(`params.artifact`, D-C-143) and the topic (`params.topic_id`); without `artifact` both are
refreshed. The answer is `agent.completed` under `agent.completed:{request_id}` (D-B-191): a repeat
of a finished job finds it and calls no model (law 5). A request that cannot succeed on a retry —
no topic, a cheat sheet, nothing valid came back — is answered `ok: false`; an error of the model
or of the store raises out of the handler and commits nothing (law 7): the queue retries.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from quack.artifacts.accuracy import WRITER_ROLE
from quack.artifacts.delivery import completed_key, deliver
from quack.artifacts.documents import ArtifactKind, artifact_id_for
from quack.artifacts.topic_refresh import Prefetch, refresh_topic_cards, refresh_topic_notes
from quack.events.event_types import AgentRequested
from quack.graph.read import GraphReader, create_graph_reader
from quack.graph.read.materials import ChunkHit, materials_prefetch
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.logging import get_logger
from quack.practice.repo import agent_request, event_by_key

log = get_logger(__name__)

KIND = "artifact"
TOPIC_REQUIRED = "topic_required"
UNSUPPORTED_ARTIFACT = "unsupported_artifact"
NOTHING_WRITTEN = "nothing_written"


async def answer_artifact_request(
    ctx: JobContext,
    reader: GraphReader,
    prefetch: Prefetch,
    *,
    learner_id: UUID,
    request_id: UUID,
) -> None:
    """Write what the request asks for, then `agent.completed`; a finished request is skipped."""
    if await event_by_key(ctx.db, learner_id=learner_id, key=completed_key(request_id)):
        return
    requested = await agent_request(ctx.db, learner_id=learner_id, request_id=request_id)
    params = AgentRequested.model_validate(requested.payload).params
    topic_id = params.topic_id
    error: str | None = None
    result_ref: str | None = None
    if params.artifact == "cheatsheet":
        error = UNSUPPORTED_ARTIFACT
    elif topic_id is None:
        error = TOPIC_REQUIRED
    else:
        key = f"request:{request_id}"
        written: list[ArtifactKind] = []
        if params.artifact in (None, "notes"):
            version = await refresh_topic_notes(
                ctx.db, ctx.llm, reader, prefetch, ctx.settings,
                learner_id=learner_id, topic_id=topic_id, key=key,
                redis=ctx.redis, compute=ctx.compute,
            )  # fmt: skip
            if version is not None:
                written.append("notes")
        if params.artifact in (None, "cards"):
            version = await refresh_topic_cards(
                ctx.db, ctx.llm, reader, prefetch, ctx.settings,
                learner_id=learner_id, topic_id=topic_id, key=key, redis=ctx.redis,
            )  # fmt: skip
            if version is not None:
                written.append("cards")
        if written:
            result_ref = str(artifact_id_for(learner_id, written[0], topic_id))
        else:
            error = NOTHING_WRITTEN
    await deliver(
        ctx.db,
        learner_id=learner_id,
        request_id=request_id,
        job_key=None,
        kind=KIND,
        ok=error is None,
        actor=ctx.settings.llm_roles[WRITER_ROLE].prompt or WRITER_ROLE,
        error=error,
        result_ref=result_ref,
        domain_id=requested.domain_id,
        session_id=requested.session_id,
        causation_seq=requested.seq,
    )


@register_job_handler("agent.artifact")
async def _agent_artifact_handler(job: ClaimedJob, ctx: JobContext) -> None:
    learner_id = job.learner_id
    if learner_id is None:
        raise ValueError("agent.artifact without a learner")

    async def prefetch(skill_ids: Sequence[UUID]) -> list[ChunkHit]:
        return await materials_prefetch(
            ctx.neo4j, learner_id=learner_id, skill_ids=skill_ids, k=ctx.settings.materials_k
        )

    reader = create_graph_reader(ctx.neo4j, ctx.settings, learner_id=learner_id)
    await answer_artifact_request(
        ctx,
        reader,
        prefetch,
        learner_id=learner_id,
        request_id=UUID(str(job.payload["request_id"])),
    )
