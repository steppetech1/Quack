"""Handlers of the jobs of the dispatcher that work on a canvas line (ТЗ Ф4 WP21.3 п.4,
WP21.4 п.4; #263; D-C-218). The rows of the dispatcher are stream A's; the bodies are here, and
both are the calls the socket makes itself, run again from the log:

- `second_opinion.diagnosis` (from `observation.diagnosed` with `kind = question`): the second
  opinion of `step_check.second_opinion`, written as `observation.second_opinion` under
  `observation.second_opinion:{target_seq}`. The socket may have written it first; then the job
  finds the event and asks nobody (law 5).
- `canvas.rediagnose` (from `handwriting.corrected`): the cascade of WP21.4 on the line as the
  learner corrected it. Another reading of a line is another diagnosis, under its own key; an
  unsure one queues its own `second_opinion.diagnosis` through the dispatcher.

Both read the canvas and the line from the log, never from the memory of an API process.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from quack.canvas.boards import Canvas, CanvasNotFound, get_canvas
from quack.canvas.step_check import (
    StepEnv,
    StepOutcome,
    canvas_lines,
    check_step,
    diagnosed_key,
    second_opinion,
)
from quack.events.event_types import CanvasLineCommitted, ObservationDiagnosed
from quack.graph.read import create_graph_reader
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob, JobFailedPermanently
from quack.logging import get_logger

log = get_logger(__name__)

_DIAGNOSED = (
    "SELECT payload, idempotency_key FROM core.events WHERE learner_id = %(learner_id)s "
    "AND seq = %(seq)s AND type = 'observation.diagnosed'"
)
_OPINION = (
    "SELECT 1 AS present FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'observation.second_opinion' AND idempotency_key = %(key)s"
)


def _learner(job: ClaimedJob) -> UUID:
    if job.learner_id is None:
        raise JobFailedPermanently(f"{job.kind} without a learner")
    return job.learner_id


async def _canvas(ctx: JobContext, learner_id: UUID, canvas_id: UUID) -> Canvas:
    try:
        return await get_canvas(ctx.db, learner_id=learner_id, canvas_id=canvas_id)
    except CanvasNotFound as exc:
        raise JobFailedPermanently(str(exc)) from exc


def _env(ctx: JobContext, learner_id: UUID, canvas: Canvas) -> StepEnv:
    """The environment of the socket's check; the memory read is there for the known
    misconceptions of the diagnoser and fails soft (D-C-158 п.6)."""
    return StepEnv(
        db=ctx.db,
        llm=ctx.llm,
        compute=ctx.compute,
        settings=ctx.settings,
        learner_id=learner_id,
        session_id=canvas.session_id,
        canvas_id=canvas.canvas_id,
        task_id=canvas.task_id,
        reader=create_graph_reader(ctx.neo4j, ctx.settings, learner_id=learner_id, db=ctx.db)
        if canvas.task_id
        else None,
        redis=ctx.redis,
    )


async def _line(
    env: StepEnv, line_id: str
) -> tuple[CanvasLineCommitted, list[CanvasLineCommitted]] | None:
    """The line as it stands now and the lines before it; `None` — it is erased or never was."""
    lines = await canvas_lines(env.db, learner_id=env.learner_id, canvas_id=env.canvas_id)
    ids = [item.line_id for item in lines]
    if line_id not in ids:
        return None
    index = ids.index(line_id)
    return lines[index], lines[:index]


@register_job_handler("second_opinion.diagnosis")
async def handle_second_opinion(job: ClaimedJob, ctx: JobContext) -> None:
    learner_id = _learner(job)
    seq = int(job.payload["trigger_seq"])
    row = await ctx.db.fetch_one(_DIAGNOSED, {"learner_id": learner_id, "seq": seq})
    if row is None:
        raise JobFailedPermanently(f"observation.diagnosed {seq} not found")
    diagnosed = ObservationDiagnosed.model_validate(row["payload"])
    if diagnosed.diagnosis.kind != "question":
        return
    key = f"observation.second_opinion:{seq}"
    if await ctx.db.fetch_one(_OPINION, {"learner_id": learner_id, "key": key}) is not None:
        return
    canvas = await _canvas(ctx, learner_id, diagnosed.canvas_id)
    if canvas.task_id is None:
        return
    env = _env(ctx, learner_id, canvas)
    found = await _line(env, diagnosed.line_id)
    if found is None:
        return
    line, earlier = found
    if diagnosed_key(env, line) != row["idempotency_key"]:
        log.info("second_opinion_line_corrected", canvas_id=str(env.canvas_id), seq=seq)
        return  # the learner has corrected the line since: `canvas.rediagnose` judges that reading
    outcome = StepOutcome(line.line_id, "question", seq, diagnosed.method)
    if await second_opinion(env, line, outcome, earlier) is None:
        # The model is down (the socket leaves the mark a question): try again later.
        raise RuntimeError(f"second opinion on {seq} unavailable")


@register_job_handler("canvas.rediagnose")
async def handle_rediagnose(job: ClaimedJob, ctx: JobContext) -> None:
    learner_id = _learner(job)
    payload: dict[str, Any] = job.payload
    canvas = await _canvas(ctx, learner_id, UUID(str(payload["canvas_id"])))
    if canvas.task_id is None:
        return
    env = _env(ctx, learner_id, canvas)
    found = await _line(env, str(payload["line_id"]))
    if found is None:
        return
    line, earlier = found
    await check_step(env, line, earlier)
