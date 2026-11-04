"""WP21.3 п.4, WP21.4 п.4: the handlers of the jobs `second_opinion.diagnosis` and
`canvas.rediagnose` (#263; D-C-217). Postgres is real, the models are `ScriptedLLMProvider`,
compute a `ComputeScenario`."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from quack.canvas.step_check import StepEnv, check_step
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import HandwritingCorrected
from quack.events.log import NewEvent, append_events
from quack.jobs.kinds import JOB_KINDS, KIND_LLM_ROLES
from quack.jobs.rules_p4 import JOB_KIND_SPECS
from quack.jobs.runner import JOB_HANDLERS, JobContext
from quack.jobs.types import ClaimedJob, JobFailedPermanently
from quack.runtime import HANDLER_MODULES
from tests.canvas.steps_support import commit, events, line, make_canvas
from tests.canvas.test_step_check_wp21 import (
    DIAGNOSER,
    SECOND,
    _diagnosis,
    _unavailable,
    _verdict,
)
from tests.db.fixtures import create_learner
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources

KINDS = ("second_opinion.diagnosis", "canvas.rediagnose")


def _job(kind: str, learner: UUID, **payload: Any) -> ClaimedJob:
    return ClaimedJob(
        id=uuid4(),
        kind=kind,
        learner_id=learner,
        lock_key="",
        coalesce_key=None,
        priority=0,
        payload=payload,
        attempt=1,
        max_attempts=5,
        job_key=f"{kind}:{learner}:{payload.get('trigger_seq')}",
    )


class _World:
    def __init__(self, ctx: JobContext, env: StepEnv, provider: ScriptedLLMProvider) -> None:
        self.ctx, self.env, self.provider = ctx, env, provider


async def _world(pg_db: Db, settings: Settings) -> tuple[_World, UUID]:
    learner = await create_learner(pg_db)
    session_id, canvas = await make_canvas(pg_db, learner)
    provider = ScriptedLLMProvider()
    resources = build_app_resources(
        settings, None, None, db=pg_db, provider=provider,
        compute=scripted_compute(ComputeScenario()),
    )  # fmt: skip
    ctx = cast(
        JobContext,
        SimpleNamespace(
            db=pg_db,
            settings=settings,
            llm=resources.llm,
            compute=resources.compute,
            redis=None,
            neo4j=None,
        ),
    )
    env = StepEnv(
        db=pg_db,
        llm=resources.llm,
        compute=resources.compute,
        settings=settings,
        learner_id=learner,
        session_id=session_id,
        canvas_id=canvas.canvas_id,
        task_id=canvas.task_id,
    )
    return _World(ctx, env, provider), learner


def test_the_kinds_are_registered_by_the_procedure_of_issue_173() -> None:
    """One commit: the rows of JOB_KINDS, KIND_LLM_ROLES (the roles of the dispatcher's table)
    and HANDLER_MODULES, and the handlers themselves."""
    assert "quack.canvas.jobs" in HANDLER_MODULES
    importlib.import_module("quack.canvas.jobs")
    for kind in KINDS:
        assert kind in JOB_KINDS and kind in JOB_HANDLERS
        assert KIND_LLM_ROLES[kind] == JOB_KIND_SPECS[kind].roles
    assert KIND_LLM_ROLES["second_opinion.diagnosis"] == ("second_opinion",)
    assert KIND_LLM_ROLES["canvas.rediagnose"] == ("canvas_diagnoser",)


async def test_second_opinion_job_repeats_the_call_and_writes_once(
    pg_db: Db, settings: Settings
) -> None:
    """The job of the dispatcher on an unsure `observation.diagnosed` writes the second opinion
    under `observation.second_opinion:{target_seq}`; a repeat of the job writes nothing and does
    not ask the model again (law 5)."""
    w, learner = await _world(pg_db, settings)
    first = line(w.env.canvas_id, "l1", "x=3")
    await commit(pg_db, learner, w.env.session_id, first)
    w.provider.respond(DIAGNOSER, lambda _r: _diagnosis(confidence=0.4))
    w.provider.respond(SECOND, lambda _r: _verdict("incorrect"))
    outcome = await check_step(w.env, first, [])
    assert outcome.mark == "question" and outcome.diagnosed_seq is not None
    job = _job("second_opinion.diagnosis", learner, trigger_seq=outcome.diagnosed_seq)

    await JOB_HANDLERS["second_opinion.diagnosis"](job, w.ctx)

    [opinion] = await pg_db.fetch_all(
        "SELECT payload, idempotency_key, causation_seq FROM core.events "
        "WHERE learner_id = %(l)s AND type = 'observation.second_opinion'",
        {"l": learner},
    )
    assert opinion["payload"] == {"target_seq": outcome.diagnosed_seq, "agreed": True}
    assert opinion["idempotency_key"] == f"observation.second_opinion:{outcome.diagnosed_seq}"
    asked = len(w.provider.calls_for(schema_name=SECOND))
    assert asked == 1

    await JOB_HANDLERS["second_opinion.diagnosis"](job, w.ctx)
    assert len(await events(pg_db, learner, "observation.second_opinion")) == 1
    assert len(w.provider.calls_for(schema_name=SECOND)) == asked


async def test_second_opinion_job_skips_what_it_must_not_judge(
    pg_db: Db, settings: Settings
) -> None:
    """A diagnosis that is not a question, a line the learner has corrected since (another
    reading is another diagnosis, `canvas.rediagnose` judges it) and an erased line get no
    second opinion; a diagnosis that is not in the log fails permanently."""
    w, learner = await _world(pg_db, settings)
    canvas_id, session_id = w.env.canvas_id, w.env.session_id
    sure, unsure = line(canvas_id, "l1", "x=3"), line(canvas_id, "l2", "x=4")
    for row in (sure, unsure):
        await commit(pg_db, learner, session_id, row)
    w.provider.respond(SECOND, lambda _r: _verdict("incorrect"))

    w.provider.respond(DIAGNOSER, lambda _r: _diagnosis(confidence=0.95))
    confident = await check_step(w.env, sure, [])
    w.provider.respond(DIAGNOSER, lambda _r: _diagnosis(confidence=0.4))
    question = await check_step(w.env, unsure, [sure])
    assert confident.diagnosed_seq and question.diagnosed_seq
    handler = JOB_HANDLERS["second_opinion.diagnosis"]

    await handler(_job("second_opinion.diagnosis", learner, trigger_seq=confident.diagnosed_seq), w.ctx)

    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="handwriting.corrected",
                payload=HandwritingCorrected(canvas_id=canvas_id, line_id="l2", text="x=2"),
                actor="learner",
                idempotency_key="correct-l2",
                session_id=session_id,
            )
        ],
    )
    await handler(_job("second_opinion.diagnosis", learner, trigger_seq=question.diagnosed_seq), w.ctx)
    assert w.provider.calls_for(schema_name=SECOND) == []
    assert await events(pg_db, learner, "observation.second_opinion") == []

    with pytest.raises(JobFailedPermanently):
        await handler(_job("second_opinion.diagnosis", learner, trigger_seq=999_999), w.ctx)


async def test_second_opinion_job_is_retried_while_the_model_is_down(
    pg_db: Db, settings: Settings
) -> None:
    """The socket leaves the mark a question when the model is down; the job must not end as
    done then, or nobody asks again."""
    w, learner = await _world(pg_db, settings)
    first = line(w.env.canvas_id, "l1", "x=3")
    await commit(pg_db, learner, w.env.session_id, first)
    w.provider.respond(DIAGNOSER, lambda _r: _diagnosis(confidence=0.4))
    outcome = await check_step(w.env, first, [])
    assert outcome.diagnosed_seq is not None
    w.provider.fail(_unavailable(SECOND))
    job = _job("second_opinion.diagnosis", learner, trigger_seq=outcome.diagnosed_seq)

    with pytest.raises(Exception, match="second opinion") as raised:
        await JOB_HANDLERS["second_opinion.diagnosis"](job, w.ctx)
    assert not isinstance(raised.value, JobFailedPermanently)
    assert await events(pg_db, learner, "observation.second_opinion") == []


async def test_rediagnose_checks_the_corrected_line_once(pg_db: Db, settings: Settings) -> None:
    """`handwriting.corrected` → `canvas.rediagnose`: the cascade of WP21.4 on the line as the
    learner corrected it (its text in the prompt), under the key of the new reading; a repeat
    writes nothing and asks nobody. The unsure diagnosis it writes is for the dispatcher's
    `second_opinion.diagnosis`, not for this job."""
    w, learner = await _world(pg_db, settings)
    canvas_id, session_id = w.env.canvas_id, w.env.session_id
    first = line(canvas_id, "l1", "x=l")  # misread
    await commit(pg_db, learner, session_id, first)
    w.provider.respond(DIAGNOSER, lambda _r: _diagnosis(error=False, gap_type="none"))
    assert (await check_step(w.env, first, [])).mark is None
    assert await events(pg_db, learner, "observation.diagnosed") == []

    [fix] = await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="handwriting.corrected",
                payload=HandwritingCorrected(canvas_id=canvas_id, line_id="l1", text="x=3"),
                actor="learner",
                idempotency_key="correct-l1",
                session_id=session_id,
            )
        ],
    )
    w.provider.respond(DIAGNOSER, lambda _r: _diagnosis(confidence=0.4))
    job = _job(
        "canvas.rediagnose", learner,
        trigger_seq=fix.seq, canvas_id=str(canvas_id), line_id="l1",
    )  # fmt: skip

    await JOB_HANDLERS["canvas.rediagnose"](job, w.ctx)

    assert "x=3" in w.provider.calls_for(schema_name=DIAGNOSER)[-1].prompt
    [diagnosed] = await events(pg_db, learner, "observation.diagnosed")
    assert diagnosed["payload"]["line_id"] == "l1"
    assert diagnosed["payload"]["diagnosis"]["kind"] == "question"
    assert await events(pg_db, learner, "observation.second_opinion") == []
    calls = len(w.provider.calls)

    await JOB_HANDLERS["canvas.rediagnose"](job, w.ctx)
    assert len(await events(pg_db, learner, "observation.diagnosed")) == 1
    assert len(w.provider.calls) == calls


async def test_rediagnose_leaves_alone_what_is_gone(pg_db: Db, settings: Settings) -> None:
    """An erased line, a line that was never there and a canvas of another learner are not
    checked."""
    w, learner = await _world(pg_db, settings)
    canvas_id, session_id = w.env.canvas_id, w.env.session_id
    gone = line(canvas_id, "l1", "x=3")
    await commit(pg_db, learner, session_id, gone)
    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="canvas.line_erased",
                payload={"canvas_id": str(canvas_id), "line_id": "l1", "text": "x=3"},
                actor="learner",
                idempotency_key="erase-l1",
                session_id=session_id,
            )
        ],
    )
    w.provider.respond(DIAGNOSER, lambda _r: _diagnosis(confidence=0.4))
    handler = JOB_HANDLERS["canvas.rediagnose"]
    for line_id in ("l1", "nope"):
        await handler(
            _job("canvas.rediagnose", learner, trigger_seq=1, canvas_id=str(canvas_id), line_id=line_id),
            w.ctx,
        )
    assert w.provider.calls == []

    stranger = await create_learner(pg_db)
    with pytest.raises(JobFailedPermanently):
        await handler(
            _job("canvas.rediagnose", stranger, trigger_seq=1, canvas_id=str(canvas_id), line_id="l1"),
            w.ctx,
        )
