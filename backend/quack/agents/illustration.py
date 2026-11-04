"""The illustrator, WP16.6 (ТЗ Ф4; `agent.requested {kind: illustration}`, `П-4.5`, D-B-187,
D-B-196). The job `agent.illustration` takes `params.description`, asks `illustrator` (Muse
Image) for a conceptual picture and stores the bytes in the object store; `agent.completed
{ok: true, result_ref: key}`. No description, or an answer that is only a `url` (no fetch from
a third-party host without a rule), is `ok: false` and the teacher goes on without a picture.
A repeat of a finished job finds its `agent.completed` and does nothing (law 5).
"""

from __future__ import annotations

from uuid import UUID

from quack.agents.delivery import completed_key, deliver
from quack.events.event_types import AgentRequested
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.practice.repo import agent_request, event_by_key
from quack.storage.keys import new_ulid
from quack.storage.objects import learner_prefix

KIND = "illustration"
NO_DESCRIPTION = "no_description"
URL_ONLY = "image_url_only"
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
# П-4.5: a conceptual picture, never mathematics (formulas, graphs, diagrams are not drawn).
_BRIEF = (
    "A conceptual illustration of an idea, no formulas, equations, graphs, plots or "
    "mathematical diagrams, no text in the picture: "
)


def illustration_key(learner_id: UUID, request_id: UUID, mime: str) -> str:
    """The key in the learner's prefix; the ULID keeps a repeat after a crash from colliding
    with an object already written (Т-§3.3)."""
    ext = _EXTENSIONS.get(mime, "bin")
    return f"{learner_prefix(learner_id)}illustrations/{request_id}-{new_ulid()}.{ext}"


async def illustrate_request(ctx: JobContext, *, learner_id: UUID, request_id: UUID) -> None:
    done = await event_by_key(ctx.db, learner_id=learner_id, key=completed_key(request_id))
    if done is not None:
        return
    requested = await agent_request(ctx.db, learner_id=learner_id, request_id=request_id)
    description = AgentRequested.model_validate(requested.payload).params.description
    ok, ref, error = False, None, NO_DESCRIPTION
    if description and description.strip():
        result = await ctx.llm.illustrate(
            _BRIEF + description.strip(), learner_id=learner_id, session_id=requested.session_id
        )
        image = result.value
        if image.image:
            ref = illustration_key(learner_id, request_id, image.mime)
            await ctx.storage.put(ref, image.image, image.mime)
            ok, error = True, None
        else:
            error = URL_ONLY
    await deliver(
        ctx.db,
        learner_id=learner_id,
        request_id=request_id,
        job_key=None,
        kind=KIND,
        ok=ok,
        actor=f"agent:{KIND}",
        error=error,
        result_ref=ref,
        domain_id=requested.domain_id,
        session_id=requested.session_id,
        causation_seq=requested.seq,
    )


@register_job_handler("agent.illustration")
async def handle_illustration(job: ClaimedJob, ctx: JobContext) -> None:
    if job.learner_id is None:
        raise ValueError("agent.illustration needs learner_id")
    await illustrate_request(
        ctx, learner_id=job.learner_id, request_id=UUID(str(job.payload["request_id"]))
    )
