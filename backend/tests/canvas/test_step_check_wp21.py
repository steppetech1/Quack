"""WP21.4: the check of canvas steps, T-CV4 (ТЗ Ф4 WP21.4; Т-§15.4; D-C-158). The cascade:
symbolic transition in compute, then `canvas_diagnoser@1`, then the second opinion. Compute is
a `ComputeScenario`, the models are `ScriptedLLMProvider`; Postgres is real."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from quack.canvas.step_check import StepEnv, canvas_lines, check_step, second_opinion
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import HandwritingCorrected
from quack.events.log import NewEvent, append_events
from quack.llm.provider import ProviderRequest
from tests.canvas.steps_support import (
    TRANSITION,
    commit,
    events,
    follows,
    line,
    make_canvas,
)
from tests.db.fixtures import create_learner
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources

DIAGNOSER = "CanvasDiagnoserOutput"
SECOND = "ModelVerdictOutput"


def _diagnosis(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "error": True,
        "gap_type": "slip",
        "misconception": None,
        "misconception_hypothesis": None,
        "root_cause": "S1",
        "confidence": 0.95,
    }
    return base | fields


def _verdict(outcome: str) -> dict[str, Any]:
    return {
        "items": [],
        "outcome": outcome,
        "confidence": 0.9,
        "misconception": None,
        "misconception_hypothesis": None,
    }


def _new_line(request: ProviderRequest) -> str:
    """The text of the new line in the prompt of the diagnoser."""
    return request.prompt.split("### The new line")[-1].strip()


class Setup:
    def __init__(self, env: StepEnv, provider: ScriptedLLMProvider, scenario: ComputeScenario):
        self.env, self.provider, self.scenario = env, provider, scenario


async def _setup(
    pg_db: Db,
    settings: Settings,
    *,
    task: bool = True,
) -> tuple[Setup, UUID]:
    learner = await create_learner(pg_db)
    session_id, canvas = await make_canvas(pg_db, learner, task=task)
    scenario = ComputeScenario()
    provider = ScriptedLLMProvider()
    resources = build_app_resources(
        settings, None, None, db=pg_db, provider=provider,
        compute=scripted_compute(scenario),
    )  # fmt: skip
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
    return Setup(env, provider, scenario), learner


async def test_step_check_cascade(pg_db: Db, settings: Settings) -> None:
    """T-CV4: a discrepancy of the transition is a confirmed error (`method: symbolic`, the
    model names the gap); a confirmed transition writes nothing; an unsure model diagnosis is a
    `question` with the second opinion `observation.second_opinion {target_seq, agreed}`; a
    disagreeing second opinion leaves no error mark."""
    s, learner = await _setup(pg_db, settings)
    canvas_id, session_id = s.env.canvas_id, s.env.session_id
    answers = {
        "x=3": _diagnosis(gap_type="execution", confidence=0.7),
        "x=5": _diagnosis(confidence=0.5),
    }
    s.provider.respond(
        DIAGNOSER, lambda r: answers.get(_new_line(r), _diagnosis(error=False, gap_type="none"))
    )
    s.provider.respond(SECOND, lambda _r: _verdict("correct"))
    rows = [line(canvas_id, f"l{i}", text) for i, text in enumerate(["2x+3=7", "2x=4", "x=3"], 1)]
    for row in rows:
        await commit(pg_db, learner, session_id, row)

    # l1 has no previous line: the model, no error. l2 follows from l1: nothing at all.
    s.scenario.push_for(TRANSITION, follows(True), follows(False))
    assert (await check_step(s.env, rows[0], [])).mark is None
    assert (await check_step(s.env, rows[1], rows[:1])).mark is None
    assert await events(pg_db, learner, "observation.diagnosed") == []
    assert len(s.provider.calls) == 1  # the model was not asked about the confirmed step

    # l3 contradicts l2: confirmed by code, the model only names the gap.
    outcome = await check_step(s.env, rows[2], rows[:2])
    assert (outcome.mark, outcome.method) == ("error", "symbolic")
    [diagnosed] = await events(pg_db, learner, "observation.diagnosed")
    assert diagnosed["seq"] == outcome.diagnosed_seq and diagnosed["actor"] == "code"
    assert (
        diagnosed["payload"]["method"] == "symbolic" and diagnosed["payload"]["confidence"] == 1.0
    )
    assert diagnosed["payload"]["diagnosis"]["kind"] == "error"
    assert diagnosed["payload"]["diagnosis"]["gap_type"] == "execution"
    assert diagnosed["payload"]["task_id"] == str(s.env.task_id)
    # A repeat (the check request) takes the diagnosis written: compute is not asked again.
    again = await check_step(s.env, rows[2], rows[:2])
    assert again == outcome and len(await events(pg_db, learner, "observation.diagnosed")) == 1

    # The check is impossible (compute is down): an unsure model diagnosis is a question.
    unsure = line(canvas_id, "l4", "x=5")
    await commit(pg_db, learner, session_id, unsure)
    asked = await check_step(s.env, unsure, rows)
    assert (asked.mark, asked.method) == ("question", "model")
    [_, question] = await events(pg_db, learner, "observation.diagnosed")
    assert question["payload"]["diagnosis"]["kind"] == "question"
    assert question["payload"]["confidence"] == 0.5

    # The second opinion does not agree: written, no error.
    assert await second_opinion(s.env, unsure, asked, rows) is False
    [opinion] = await events(pg_db, learner, "observation.second_opinion")
    assert opinion["payload"] == {"target_seq": question["seq"], "agreed": False, "diagnosis": None}
    assert opinion["actor"] == "second_opinion@1"
    # D-B-212: no outbox, a repeat asks the model again; the same answer is the same event.
    assert await second_opinion(s.env, unsure, asked, rows) is False  # same key, same answer
    assert len(await events(pg_db, learner, "observation.second_opinion")) == 1


async def test_second_opinion_agrees_and_repeats_nothing(pg_db: Db, settings: Settings) -> None:
    """T-CV4: an agreeing second opinion is `agreed: true`; the key is the diagnosis, so the
    dispatcher's job `second_opinion.diagnosis` cannot add a second one; with the model down
    nothing is written and the mark stays a question."""
    s, learner = await _setup(pg_db, settings)
    first = line(s.env.canvas_id, "l1", "x=3")
    await commit(pg_db, learner, s.env.session_id, first)
    s.provider.respond(DIAGNOSER, lambda _r: _diagnosis(confidence=0.4))
    s.provider.respond(SECOND, lambda _r: _verdict("incorrect"))
    outcome = await check_step(s.env, first, [])
    assert outcome.mark == "question"
    assert await second_opinion(s.env, first, outcome, []) is True
    assert await second_opinion(s.env, first, outcome, []) is True
    [opinion] = await events(pg_db, learner, "observation.second_opinion")
    assert opinion["payload"]["agreed"] is True
    prompt = s.provider.calls[-1].prompt
    assert "x=3" in prompt and "Solve for x." in prompt

    down = line(s.env.canvas_id, "l2", "x=4")
    await commit(pg_db, learner, s.env.session_id, down)
    s.provider.responders.pop(SECOND)
    s.provider.failures.append(_unavailable(SECOND))
    unsure = await check_step(s.env, down, [first])
    assert await second_opinion(s.env, down, unsure, [first]) is None
    assert len(await events(pg_db, learner, "observation.second_opinion")) == 1


def _unavailable(schema: str) -> Any:
    from tests.support.llm import ScriptedFailure

    return ScriptedFailure("unavailable", schema_name=schema, times=10)


async def test_model_diagnosis_without_confirmation_gives_no_mark(
    pg_db: Db, settings: Settings
) -> None:
    """Q-A-3 п.1: a sure model diagnosis (`confidence >= mark_min_confidence`) is written as
    `error`/`model`, but without a symbolic discrepancy or a second opinion it is no mark; no
    error found - no event (D-C-120); the model down - nothing is written."""
    s, learner = await _setup(pg_db, settings)
    sure = line(s.env.canvas_id, "l1", "x=3")
    s.provider.respond(DIAGNOSER, lambda _r: _diagnosis(confidence=0.95))
    outcome = await check_step(s.env, sure, [])
    assert (outcome.mark, outcome.method) == (None, "model")
    [diagnosed] = await events(pg_db, learner, "observation.diagnosed")
    assert diagnosed["payload"]["diagnosis"]["kind"] == "error"
    assert diagnosed["payload"]["diagnosis"]["root_cause_skill_id"] is not None

    s.provider.respond(DIAGNOSER, lambda _r: _diagnosis(error=False, gap_type="none"))
    clean = await check_step(s.env, line(s.env.canvas_id, "l2", "x=2"), [sure])
    assert clean.mark is None and clean.diagnosed_seq is None
    assert len(await events(pg_db, learner, "observation.diagnosed")) == 1

    s.provider.responders.pop(DIAGNOSER)
    s.provider.failures.append(_unavailable(DIAGNOSER))
    broken = await check_step(s.env, line(s.env.canvas_id, "l3", "x=9"), [sure])
    assert broken.mark is None and broken.diagnosed_seq is None


async def test_symbolic_error_without_the_model_keeps_its_error(
    pg_db: Db, settings: Settings
) -> None:
    """D-C-158 п.1: a confirmed discrepancy stays an error when the model is down or finds
    none; the gap is then `execution`."""
    s, learner = await _setup(pg_db, settings)
    first, second = line(s.env.canvas_id, "l1", "2x=4"), line(s.env.canvas_id, "l2", "x=3")
    s.scenario.push_for(TRANSITION, follows(False))
    s.provider.respond(DIAGNOSER, lambda _r: _diagnosis(error=False, gap_type="none"))
    outcome = await check_step(s.env, second, [first])
    assert outcome.mark == "error"
    [diagnosed] = await events(pg_db, learner, "observation.diagnosed")
    assert diagnosed["payload"]["diagnosis"]["gap_type"] == "execution"
    assert diagnosed["payload"]["diagnosis"]["root_cause_skill_id"] is None


async def test_impossible_transition_goes_to_the_model(pg_db: Db, settings: Settings) -> None:
    """Т-§15.4 п.1, п.3: a text line, a line compute cannot parse (`status: failed`, `follows:
    null`) and a fall of compute all hand the line to the model; the previous lines and the
    solution space reach the prompt; crossed out lines and canvases without a task are not
    checked."""
    s, learner = await _setup(pg_db, settings)
    first = line(s.env.canvas_id, "l1", "let x be the unknown", kind="text")
    second = line(s.env.canvas_id, "l2", "x=3")
    s.provider.respond(DIAGNOSER, lambda _r: _diagnosis(error=False, gap_type="none"))
    await check_step(s.env, second, [first])
    assert s.scenario.requests == []  # a text line is not sent to compute
    prompt = s.provider.calls[-1].prompt
    assert "let x be the unknown" in prompt and "sign lost when moving a term" in prompt
    assert "2x+3=7" in prompt

    s.scenario.push_for(TRANSITION, follows(None, "failed"), 503)
    before = len(s.provider.calls)
    for number in (3, 4):  # not parsed by compute, then compute is down
        parsed = line(s.env.canvas_id, f"l{number}", "x=2", sympy="Eq(x, 2)")
        await check_step(s.env, parsed, [line(s.env.canvas_id, "l0", "2x=4", sympy="Eq(2*x, 4)")])
    assert len(s.provider.calls) == before + 2

    struck = line(s.env.canvas_id, "l9", "x=7", crossed_out=True)
    assert (await check_step(s.env, struck, [first])).mark is None
    assert len(s.provider.calls) == before + 2

    free, _ = await _setup(pg_db, settings, task=False)
    assert (await check_step(free.env, line(free.env.canvas_id, "l1", "x=1"), [])).mark is None
    assert free.provider.calls == []
    assert await events(pg_db, learner, "observation.diagnosed") == []


async def test_lines_of_the_canvas_follow_the_log(pg_db: Db, settings: Settings) -> None:
    """The lines a step is compared with: the last commit of a line, the learner's correction
    applied, the erased ones left out, in the order they first appeared."""
    s, learner = await _setup(pg_db, settings)
    canvas_id, session_id = s.env.canvas_id, s.env.session_id
    for row in (
        line(canvas_id, "a", "1"),
        line(canvas_id, "b", "2"),
        line(canvas_id, "c", "3"),
        line(canvas_id, "a", "1+"),
    ):
        await commit(pg_db, learner, session_id, row)
    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type="canvas.line_erased",
                payload={"canvas_id": str(canvas_id), "line_id": "b", "text": "2"},
                actor="learner",
                idempotency_key="erase-b",
                session_id=session_id,
            ),
            NewEvent(
                type="handwriting.corrected",
                payload=HandwritingCorrected(
                    canvas_id=canvas_id, line_id="c", text="3x", sympy="3*x"
                ),
                actor="learner",
                idempotency_key="correct-c",
                session_id=session_id,
            ),
        ],
    )
    lines = await canvas_lines(pg_db, learner_id=learner, canvas_id=canvas_id)
    assert [(item.line_id, item.text, item.sympy) for item in lines] == [
        ("a", "1+", None),
        ("c", "3x", "3*x"),
    ]
