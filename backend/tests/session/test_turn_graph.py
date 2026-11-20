"""ТЗ Ф4 §8.9 T-TT2, T-TT3, T-TT4, T-TT5, T-TT6 at the turn graph (WP13.2, WP13.4, WP13.5).

Only a violation found before the text is shown returns the turn to the model (the head, the
spoiler, a cut answer); the markup, a stance without a code verdict, the plan format and the
boundary are settled or logged by code, with one stream call and no `reset`. The spoiler guard
has two modes (`spoiler_guard_mode`): `enforce` — the holdback buffer, the rollback, the cut
sentence; `shadow` (the default) — nothing changes in the turn, the hit is only recorded
(`guard_events`, `GuardSample`).

The graph runs on the seam S13 provider (`ScriptedLLMProvider.stream_structured`) and on
in-memory ports: admission, context, tools, the write and the frames are the caller's
(route body, WP13.1/§4.7; context WP14). The write of `turn.committed` with the manifest
(T-TT7) is the commit port over `Db`.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from structlog.testing import capture_logs

from quack.config import Settings
from quack.graph.read.models import RequiresLink
from quack.llm.labels import LabelMap
from quack.llm.provider import ProviderRequest
from quack.llm.streaming import StreamCompleted, StreamEvent, StreamRequest
from quack.practice.check import Verdict
from quack.session.actions import (
    ElementFrame,
    ResetFrame,
    StatusFrame,
    TokenFrame,
    TurnCommittedFrame,
    VerdictFrame,
)
from quack.session.elements import Element
from quack.session.guards import Boundary
from quack.session.spoiler_guard import spoiler_hits
from quack.session.teacher_output import TurnTask
from quack.session.turn_graph import (
    AssembledTurn,
    GuardInputs,
    MarkupFix,
    Role,
    TurnSlots,
    TurnToCommit,
    run_turn,
)
from tests.support.llm import ScriptedLLMProvider, ScriptedToolCall, ScriptedToolStep

SCHEMA = "TeacherOutputV2"
ANSWER = "1/3"
CLEAN_PREFIX = (
    "Let us look at the series again: every term is a third of the one before it, "
    "so the partial sums keep growing but more and more slowly as we go on. "
)
NUMBER = {"type": "number", "value": {"latex": "\\frac{1}{3}", "sympy": "1/3"}}


def _settings(**overrides: Any) -> Settings:
    return Settings(env="test", **overrides)


def _head(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent": "feedback",
        "targetSkills": ["S1"],
        "verdict_stance": None,
        "branchType": None,
        "topicLabel": None,
        "approach": "none",
    }
    return {**base, **fields}


def _task(level: int = 3, **extra: Any) -> dict[str, Any]:
    return {
        "element_id": "e1",
        "tests": [{"skill": "S1", "level": level}],
        "methodNamed": False,
        "answer": NUMBER,
        **extra,
    }


def _markup(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tasks": [_task()],
        "elements": [
            {
                "element_id": "e1",
                "kind": "short_answer",
                "prompt": "What is the sum?",
                "ask_confidence": False,
            }
        ],
        "exposures": [{"skill": "S1", "kind": "explained", "approach": "none"}],
        "targetsMisconception": None,
        "planUpdate": {
            "goal": None,
            "current_step": {
                "id": "sum-of-a-geometric-series",
                "text": "sum of a geometric series",
                "landmark": None,
            },
            "next_steps": [],
            "landmark_edits": None,
            "branches": [],
            "cancel_requests": [],
        },
        "agentRequests": [],
        "correctionsAcknowledged": [],
        "decisionReason": "practice after the explanation",
        "deadline": None,
    }
    return {**base, **fields}


def _turn(text: str = CLEAN_PREFIX, *, head: dict[str, Any] | None = None, **markup: Any) -> dict:
    return {
        "p1_head": head or _head(),
        "p2_body": {
            "blocks": [
                {"block_id": "b1", "text": text, "element_id": None},
                {"block_id": "b2", "text": None, "element_id": "e1"},
            ]
        },
        "p3_markup": _markup(**markup),
    }


def _has_spoiler(text: str) -> bool:
    return ANSWER in text


def _remove_spoiler(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(s for s in sentences if ANSWER not in s).strip()


def _guard(
    verdict: Verdict | None = None,
    boundary: Boundary | None = None,
    labels: LabelMap | None = None,
) -> GuardInputs:
    return GuardInputs(
        answered_verdict=verdict,
        skill_labels=frozenset({"S1", "S2"}),
        misconception_labels=frozenset({"M1"}),
        task_labels=frozenset(),
        has_spoiler=_has_spoiler,
        remove_spoiler=_remove_spoiler,
        boundary=boundary,
        labels=labels,
        spoiler_hits=lambda text: spoiler_hits(text, [ANSWER], _settings()),
    )


def _request() -> StreamRequest:
    return StreamRequest(
        ProviderRequest(
            model="muse-wonder-1.0",
            prompt="context",
            json_schema={"type": "object"},
            schema_name=SCHEMA,
            temperature=0.0,
            timeout_sec=30.0,
            reasoning_effort="low",
            max_output_tokens=8192,
        )
    )


def _verdict(verification: str = "code") -> Verdict:
    return Verdict.model_validate(
        {
            "answer_seq": 4,
            "part": "main",
            "outcome": "incorrect",
            "method": "numeric",
            "verification": verification,
        }
    )


@dataclass
class Ports:
    assembled: AssembledTurn
    frames: list[Any] = field(default_factory=list)
    committed: list[TurnToCommit] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    fixes: list[dict[str, Any]] = field(default_factory=list)
    fix_calls: list[MarkupFix] = field(default_factory=list)
    tool_gate: asyncio.Event | None = None
    tool_started: asyncio.Event = field(default_factory=asyncio.Event)

    async def admit(self) -> Role:
        return "teacher"

    async def assemble(self, role: Role) -> AssembledTurn:
        return self.assembled

    async def execute_tool(self, name: str, arguments: str) -> str:
        self.tools.append(name)
        if self.tool_gate is not None:
            self.tool_started.set()
            await self.tool_gate.wait()
        return f"history of {name}"

    async def commit(self, turn: TurnToCommit) -> int:
        self.committed.append(turn)
        return 42

    async def emit(self, frame: Any) -> None:
        self.frames.append(frame)

    async def fix_markup(self, fix: MarkupFix) -> TurnTask | Element | None:
        """The n-th fix answer is `fixes[n]` (the last repeats); none — no fix. An answer with a
        `kind` is an element, any other is the markup of a task."""
        self.fix_calls.append(fix)
        if not self.fixes:
            return None
        answer = self.fixes.pop(0) if len(self.fixes) > 1 else self.fixes[0]
        if "kind" in answer:
            return TypeAdapter(Element).validate_python(answer)
        return TurnTask.model_validate(answer)


def _ports(
    verdict: Verdict | None = None,
    frame: VerdictFrame | None = None,
    boundary: Boundary | None = None,
    labels: LabelMap | None = None,
) -> Ports:
    return Ports(
        AssembledTurn(
            request=_request(),
            guard=_guard(verdict, boundary, labels),
            effort="low",
            degraded_mode=None,
            verdict=frame,
        )
    )


def _answers(provider: ScriptedLLMProvider, *turns: dict[str, Any]) -> None:
    """The n-th streamed answer is `turns[n]`, the last one repeats."""
    queue = list(turns)

    def responder(_request: ProviderRequest) -> dict[str, Any]:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    provider.respond(SCHEMA, responder)


def _shown(frames: list[Any]) -> str:
    return "".join(f.text for f in frames if isinstance(f, TokenFrame))


def _kinds(frames: list[Any]) -> list[str]:
    """Frame names with runs of `token` collapsed."""
    names: list[str] = []
    for frame in frames:
        name = {
            StatusFrame: "status",
            VerdictFrame: "verdict",
            TokenFrame: "token",
            ResetFrame: "reset",
            ElementFrame: "element",
            TurnCommittedFrame: "turn_committed",
        }[type(frame)]
        if not (name == "token" and names and names[-1] == "token"):
            names.append(name)
    return names


async def _run(ports: Ports, provider: ScriptedLLMProvider, **settings: Any) -> int | None:
    return await run_turn(ports, provider, settings=_settings(**settings), cancel=asyncio.Event())


# --- T-TT2 ---


async def test_sse_frame_order() -> None:
    """status thinking → verdict → status tool → token… → element → turn_committed."""
    provider = ScriptedLLMProvider()
    _answers(provider, _turn())
    provider.script_tools(SCHEMA, ScriptedToolStep((ScriptedToolCall("skill_history", {}),)))
    frame = VerdictFrame(
        answer_seq=4, part="main", outcome="incorrect", method="numeric", verification="code"
    )
    ports = _ports(frame=frame)

    seq = await _run(ports, provider)

    assert seq == 42
    assert _kinds(ports.frames) == [
        "status",
        "verdict",
        "status",
        "token",
        "element",
        "turn_committed",
    ]
    assert ports.frames[0] == StatusFrame(state="thinking")
    assert ports.frames[2] == StatusFrame(state="tool", tool="skill_history")
    assert ports.tools == ["skill_history"]
    assert _shown(ports.frames) == CLEAN_PREFIX
    [element] = [f for f in ports.frames if isinstance(f, ElementFrame)]
    assert element.block_id == "b2" and element.element.element_id == "e1"
    assert ports.frames[-1] == TurnCommittedFrame(
        seq=42, degraded_mode=None, effort="low", role="teacher"
    )
    [turn] = ports.committed
    assert turn.text == CLEAN_PREFIX and turn.guard_retries == 0


async def test_no_token_before_checked_head_body_first() -> None:
    """Body before head: buffered fully; a rejected head shows nothing of that attempt."""
    provider = ScriptedLLMProvider(body_before_head=True)
    rejected = _turn("Rejected attempt text. " * 4, head=_head(targetSkills=["S9"]))
    _answers(provider, rejected, _turn())
    ports = _ports()

    await _run(ports, provider, spoiler_holdback_chars=5)

    assert "Rejected" not in _shown(ports.frames)
    assert not [f for f in ports.frames if isinstance(f, ResetFrame)]
    assert _shown(ports.frames) == CLEAN_PREFIX
    assert len(provider.stream_calls) == 2


async def test_long_body_streams_before_the_end() -> None:
    """No artificial delay: text beyond the holdback goes out before the body ends."""
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(CLEAN_PREFIX * 3))
    ports = _ports()

    await _run(ports, provider, spoiler_holdback_chars=40, spoiler_guard_mode="enforce")

    tokens = [f for f in ports.frames if isinstance(f, TokenFrame)]
    assert len(tokens) > 2
    assert _shown(ports.frames) == CLEAN_PREFIX * 3


# --- T-TT3 ---


async def test_holdback_spoiler_reset() -> None:
    """A spoiler in the buffer → `reset {spoiler}`, retry with the reason; nothing shown had
    the answer."""
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(CLEAN_PREFIX + f"The sum is {ANSWER}. Check it."), _turn())
    ports = _ports()

    await _run(ports, provider, spoiler_holdback_chars=20, spoiler_guard_mode="enforce")

    resets = [f for f in ports.frames if isinstance(f, ResetFrame)]
    assert resets == [ResetFrame(reason="spoiler")]
    at = ports.frames.index(resets[0])
    assert ports.frames[at + 1] == StatusFrame(state="thinking")
    before = ports.frames[:at]
    assert _shown(before) and ANSWER not in _shown(before)
    after = ports.frames[ports.frames.index(resets[0]) :]
    assert _shown(after) == CLEAN_PREFIX
    retry_input = provider.stream_calls[1].input_items
    assert any("spoiler" in str(item) for item in retry_input)
    [turn] = ports.committed
    assert turn.guard_retries == 1 and ANSWER not in turn.text


async def test_spoiler_after_retries_sentence_removed() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(CLEAN_PREFIX + f"The sum is {ANSWER}. Check it."))
    ports = _ports()

    await _run(
        ports,
        provider,
        spoiler_holdback_chars=20,
        guard_max_retries=2,
        spoiler_guard_mode="enforce",
    )

    assert len(provider.stream_calls) == 3
    assert ANSWER not in _shown(ports.frames)
    [turn] = ports.committed
    assert ANSWER not in turn.text and "Check it." in turn.text
    assert turn.guard_retries == 2
    # the cleaned text comes in sentences, not in one piece; the pieces are the committed text
    last_reset = max(i for i, f in enumerate(ports.frames) if isinstance(f, ResetFrame))
    assert ports.frames[last_reset + 1] == StatusFrame(state="thinking")
    tail = [f.text for f in ports.frames[last_reset:] if isinstance(f, TokenFrame)]
    assert len(tail) >= 2 and "".join(tail) == turn.text
    body = [b.text for b in turn.output.p2_body.blocks if b.text is not None]
    assert all(ANSWER not in text for text in body)


# --- T-TT4 ---


async def test_head_guards() -> None:
    """«The turn is marked»: an empty `targetSkills` goes back to the model before the body is
    shown. A `code` stance without a code verdict does not: it is lowered at once."""
    provider = ScriptedLLMProvider()
    _answers(
        provider,
        _turn("Unmarked.", head=_head(targetSkills=[])),
        _turn(CLEAN_PREFIX, head=_head(verdict_stance="code")),
    )
    ports = _ports(verdict=_verdict("model"))

    with capture_logs() as logs:
        await _run(ports, provider)

    assert len(provider.stream_calls) == 2
    assert _shown(ports.frames) == CLEAN_PREFIX
    assert not [f for f in ports.frames if isinstance(f, ResetFrame)]
    reasons = str(provider.stream_calls[1].input_items)
    assert "no_target_skills" in reasons and "stance_without_code_verdict" not in reasons
    [turn] = ports.committed
    assert turn.output.p1_head.verdict_stance == "formative" and turn.guard_retries == 1
    assert any(
        e["event"] == "guard_violation"
        and e["rule"] == "head"
        and e["reasons"] == ["stance_without_code_verdict"]
        for e in logs
    )


async def test_code_stance_without_a_code_verdict_is_lowered_without_a_retry() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(head=_head(verdict_stance="code")))
    ports = _ports(verdict=_verdict("model"))

    with capture_logs() as logs:
        await _run(ports, provider)

    assert len(provider.stream_calls) == 1
    assert _shown(ports.frames) == CLEAN_PREFIX
    assert not [f for f in ports.frames if isinstance(f, ResetFrame)]
    [turn] = ports.committed
    assert turn.output.p1_head.verdict_stance == "formative"
    assert turn.guard_retries == 0
    assert any(e["event"] == "guard_violation" and e["rule"] == "head" for e in logs)


async def test_code_stance_with_code_verdict_passes() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(head=_head(verdict_stance="code")))
    ports = _ports(verdict=_verdict("code"))

    with capture_logs() as logs:
        await _run(ports, provider)

    assert len(provider.stream_calls) == 1
    assert ports.committed[0].output.p1_head.verdict_stance == "code"
    assert not [e for e in logs if e["event"] == "guard_violation"]


def _jump() -> Boundary:
    """S1 needs S2, which was neither shown nor practised."""
    pre = uuid4()
    return Boundary(
        requires={"S1": (RequiresLink(skill_id=pre, confidence=0.9),)},
        skill_ids={"S1": uuid4(), "S2": pre},
        min_confidence=0.6,
    )


async def test_boundary_jump_is_logged_and_does_not_return_the_turn() -> None:
    """«Do not jump the boundary» (the head): the turn is shown and committed at the first
    attempt; the case is a structured log with the labels and a count (the metric)."""
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(head=_head(targetSkills=["S1"])))
    ports = _ports(boundary=_jump())

    with capture_logs() as logs:
        await _run(ports, provider)

    assert len(provider.stream_calls) == 1 and len(ports.committed) == 1
    assert ports.committed[0].guard_retries == 0
    assert _shown(ports.frames) == CLEAN_PREFIX
    assert not [f for f in ports.frames if isinstance(f, ResetFrame)]
    [entry] = [e for e in logs if e["event"] == "guard_violation"]
    assert entry["rule"] == "boundary" and entry["reasons"] == ["boundary_jump"]
    assert entry["skills"] == ["S1"] and entry["missing"] == ["S2"] and entry["count"] == 1


async def test_a_turn_that_takes_the_prerequisite_too_logs_nothing() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(head=_head(targetSkills=["S1", "S2"])))
    ports = _ports(boundary=_jump())

    with capture_logs() as logs:
        await _run(ports, provider)

    assert len(provider.stream_calls) == 1
    assert not [e for e in logs if e["event"] == "guard_violation"]


async def test_unknown_labels_after_retries_are_dropped_from_the_committed_turn() -> None:
    """A hub label in `targetSkills` and an unknown exposure on every attempt: after the retries
    the turn is committed without them (it used to fail in `commit` with `UnknownLabelError`)."""
    provider = ScriptedLLMProvider()
    _answers(
        provider,
        _turn(
            head=_head(targetSkills=["H1"]),
            exposures=[{"skill": "S9", "kind": "explained", "approach": "none"}],
        ),
    )
    ports = _ports()

    await _run(ports, provider, guard_max_retries=1)

    [turn] = ports.committed
    assert turn.output.p1_head.target_skills == []
    assert turn.output.p3_markup.exposures == []
    assert turn.guard_retries == 1


async def test_unknown_markup_label_after_shown_text_is_dropped_without_a_retry() -> None:
    """An unknown exposure and `targetsMisconception` in `p3_markup`, after the text is shown:
    one model attempt, no `reset`, the text stays, the labels are dropped, the turn is committed
    and the case is logged."""
    provider = ScriptedLLMProvider()
    bad = _turn(
        exposures=[
            {"skill": "S1", "kind": "explained", "approach": "none"},
            {"skill": "S9", "kind": "explained", "approach": "none"},
        ],
        targetsMisconception="M9",
    )
    _answers(provider, bad)
    ports = _ports()

    with capture_logs() as logs:
        await _run(ports, provider, spoiler_holdback_chars=20)

    assert len(provider.stream_calls) == 1
    assert not [f for f in ports.frames if isinstance(f, ResetFrame)]
    assert _shown(ports.frames) == CLEAN_PREFIX
    [turn] = ports.committed
    assert turn.text == CLEAN_PREFIX and turn.guard_retries == 0
    assert [e.skill for e in turn.output.p3_markup.exposures] == ["S1"]
    assert turn.output.p3_markup.targets_misconception is None
    [entry] = [e for e in logs if e["event"] == "guard_violation"]
    assert entry["rule"] == "markup"
    assert entry["reasons"] == ["unknown_skill_label", "unknown_misconception_label"]


async def test_plan_format_is_made_by_code_without_a_retry() -> None:
    """A step `id` that is no slug and too many `next_steps` used to return the turn: now the slug
    is made and the list is cut, at every turn, before the commit."""
    steps = [
        {"id": "Ratio Test!", "text": "ratio test", "landmark": None},
        {"id": "b", "text": "b", "landmark": None},
        {"id": "c", "text": "c", "landmark": None},
        {"id": "d", "text": "d", "landmark": None},
    ]
    plan = _markup()["planUpdate"] | {
        "current_step": {"id": "Sum Of Series", "text": "sum", "landmark": None},
        "next_steps": steps,
    }
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(planUpdate=plan))
    ports = _ports()

    with capture_logs() as logs:
        await _run(ports, provider, plan_next_steps_max=2)

    assert len(provider.stream_calls) == 1
    assert not [f for f in ports.frames if isinstance(f, ResetFrame)]
    [turn] = ports.committed
    committed = turn.output.p3_markup.plan_update
    assert committed.current_step is not None and committed.current_step.id == "sum-of-series"
    assert [s.id for s in committed.next_steps or []] == ["ratio-test", "b"]
    assert turn.guard_retries == 0 and turn.text == CLEAN_PREFIX
    assert any(e["event"] == "guard_violation" and "plan_step" in e["reasons"] for e in logs)


async def test_invalid_task_markup_removes_element() -> None:
    """«Every task is marked» (Т-§5.3): a task element with bad markup is not shown and
    leaves the committed turn; the text stays."""
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(tasks=[_task(level=1)]))
    ports = _ports()

    await _run(ports, provider)

    assert not [f for f in ports.frames if isinstance(f, ElementFrame)]
    [turn] = ports.committed
    assert turn.text == CLEAN_PREFIX
    assert turn.output.p3_markup.elements == [] and turn.output.p3_markup.tasks == []
    assert [b.block_id for b in turn.output.p2_body.blocks] == ["b1"]


# --- T-TT5 ---


async def test_markup_fix_keeps_text() -> None:
    """Bad task markup → a separate fix call with the shown text and the problems; the text
    is not generated again; the fixed markup is shown and committed."""
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(tasks=[_task(level=1)]))
    ports = _ports()
    ports.fixes = [_task(level=3)]

    await _run(ports, provider)

    assert len(provider.stream_calls) == 1
    assert not [f for f in ports.frames if isinstance(f, ResetFrame)]
    assert _shown(ports.frames) == CLEAN_PREFIX
    [fix] = ports.fix_calls
    assert fix.text == CLEAN_PREFIX and fix.element.element_id == "e1"
    assert fix.task is not None and fix.problems == ["level_1_needs_options"]
    assert [f.block_id for f in ports.frames if isinstance(f, ElementFrame)] == ["b2"]
    [turn] = ports.committed
    assert turn.text == CLEAN_PREFIX
    assert [t.tests[0].level for t in turn.output.p3_markup.tasks] == [3]


async def test_markup_fix_after_retries_removes_element() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(tasks=[_task(level=1)]))
    ports = _ports()
    ports.fixes = [_task(level=1)]  # every fix is still bad

    await _run(ports, provider, guard_max_retries=2)

    assert len(ports.fix_calls) == 2 and len(provider.stream_calls) == 1
    assert not [f for f in ports.frames if isinstance(f, ElementFrame)]
    [turn] = ports.committed
    assert turn.output.p3_markup.tasks == [] and turn.text == CLEAN_PREFIX


async def test_markup_fix_for_another_element_is_not_taken() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(tasks=[_task(level=1)]))
    ports = _ports()
    ports.fixes = [{**_task(level=3), "element_id": "e9"}]

    await _run(ports, provider, guard_max_retries=1)

    assert not [f for f in ports.frames if isinstance(f, ElementFrame)]
    assert ports.committed[0].output.p3_markup.tasks == []


# --- T-TT6 ---


async def test_markup_fix_of_a_task_without_markup_is_committed() -> None:
    """Review C #109: a task element without any markup, fixed by the fix call, is in the
    committed markup (else its answer finds no task, WP13.5, T-TT10)."""
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(tasks=[]))
    ports = _ports()
    ports.fixes = [_task(level=3)]

    await _run(ports, provider)

    [fix] = ports.fix_calls
    assert fix.task is None and fix.problems == ["no task markup"]
    assert [f.block_id for f in ports.frames if isinstance(f, ElementFrame)] == ["b2"]
    [turn] = ports.committed
    assert [t.element_id for t in turn.output.p3_markup.tasks] == ["e1"]


async def test_cancel_during_a_tool_call_stops_the_turn() -> None:
    """Review C #109 п.1: a step without text (a tool call) is cancelled too, the turn frees
    its slot at once and writes nothing."""
    provider = ScriptedLLMProvider()
    _answers(provider, _turn())
    provider.script_tools(SCHEMA, ScriptedToolStep((ScriptedToolCall("skill_history", {}),)))
    ports = _ports()
    ports.tool_gate = asyncio.Event()  # the tool never finishes by itself
    cancel = asyncio.Event()

    turn = asyncio.create_task(run_turn(ports, provider, settings=_settings(), cancel=cancel))
    await ports.tool_started.wait()
    cancel.set()

    assert await asyncio.wait_for(turn, 1) is None
    assert ports.committed == []


async def test_cancel_before_a_markup_fix_writes_nothing() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(tasks=[_task(level=1)]))
    ports = _ports()
    cancel = asyncio.Event()

    async def fix_and_cancel(fix: MarkupFix) -> TurnTask | None:
        cancel.set()  # a new action arrives while the fix call runs
        ports.fix_calls.append(fix)
        return TurnTask.model_validate(_task(level=3))

    ports.fix_markup = fix_and_cancel  # type: ignore[method-assign]

    assert await run_turn(ports, provider, settings=_settings(), cancel=cancel) is None
    assert ports.committed == [] and len(ports.fix_calls) == 1


async def test_new_action_cancels_turn() -> None:
    """A second action during the stream: the first turn is not written, the second is."""
    provider = ScriptedLLMProvider()
    _answers(provider, _turn())
    gate = provider.hold(SCHEMA)
    slots = TurnSlots()
    session_id = uuid4()
    first, second = _ports(), _ports()

    async def turn(ports: Ports) -> int | None:
        async with slots.claim(session_id) as cancel:
            return await run_turn(ports, provider, settings=_settings(), cancel=cancel)

    task1 = asyncio.create_task(turn(first))
    await gate.arrived.wait()
    task2 = asyncio.create_task(turn(second))
    await asyncio.sleep(0)
    gate.release.set()

    assert await task1 is None
    assert await task2 == 42
    assert first.committed == []
    assert not [f for f in first.frames if isinstance(f, TokenFrame | TurnCommittedFrame)]
    assert len(second.committed) == 1


# --- labels the write resolves, orphan tasks, a cut step (review of 2026-11-13) ---


def _label_table() -> LabelMap:
    return LabelMap.build(_settings(), skills=[uuid4()], requests=[uuid4()])


def _with_link(turn: dict[str, Any], chunk: str) -> dict[str, Any]:
    """`turn` with one more block: a `material_link` element `e2` of the chunk `chunk`."""
    turn["p2_body"]["blocks"].append({"block_id": "b3", "text": None, "element_id": "e2"})
    turn["p3_markup"]["elements"].append(
        {"element_id": "e2", "kind": "material_link", "chunk": chunk, "page": None}
    )
    return turn


async def test_unresolvable_labels_are_dropped_from_the_turn_without_a_retry() -> None:
    """`cancel_requests: ["R9"]`, a request of an unknown topic and a link to an unknown chunk
    used to reach `turn_batch` after the text was shown (`UnknownLabelError`, no turn in the
    journal), then they returned the turn to the model. Now code takes the labels out of what is
    committed at the first attempt; the link goes to the fix call first (none came here), then
    leaves with its block."""
    plan = _markup()["planUpdate"] | {"cancel_requests": ["R1", "R9"]}
    request = {
        "kind": "viz",
        "params": {
            "skill": None,
            "level": None,
            "topic": "T9",
            "task": None,
            "misconception": None,
            "description": "d",
        },
        "needed_in_session": True,
    }
    bad = _with_link(_turn(planUpdate=plan, agentRequests=[request]), "P9")
    provider = ScriptedLLMProvider()
    _answers(provider, bad)
    ports = _ports(labels=_label_table())

    with capture_logs() as logs:
        await _run(ports, provider, guard_max_retries=1)

    assert len(provider.stream_calls) == 1
    assert not [f for f in ports.frames if isinstance(f, ResetFrame)]
    assert _shown(ports.frames) == CLEAN_PREFIX
    [entry] = [e for e in logs if e["event"] == "guard_violation" and e["rule"] == "markup"]
    detail = str(entry["details"])
    assert entry["reasons"] == ["unknown_label"] and "R9" in detail and "P9" in detail
    [fix] = ports.fix_calls
    assert fix.element.element_id == "e2" and fix.task is None
    assert fix.problems == ["unknown_label: P9 (chunk)"] and fix.text == CLEAN_PREFIX
    [turn] = ports.committed
    markup = turn.output.p3_markup
    assert markup.plan_update.cancel_requests == ["R1"]
    assert markup.agent_requests == []
    assert [e.element_id for e in markup.elements] == ["e1"]
    assert [b.block_id for b in turn.output.p2_body.blocks] == ["b1", "b2"]
    assert [f.block_id for f in ports.frames if isinstance(f, ElementFrame)] == ["b2"]
    assert turn.text == CLEAN_PREFIX and turn.guard_retries == 0


def _chunk_table() -> LabelMap:
    return LabelMap.build(_settings(), skills=[uuid4()], chunks=[uuid4()])


def _link_fix(chunk: str, **fields: Any) -> dict[str, Any]:
    return {"element_id": "e2", "kind": "material_link", "chunk": chunk, "page": None} | fields


async def test_an_element_with_an_unknown_label_is_fixed_and_the_text_stays() -> None:
    """The fix call of `fix_markup`, as for a task: the text is not generated again, no `reset`,
    the fixed element is shown and committed in its place."""
    provider = ScriptedLLMProvider()
    _answers(provider, _with_link(_turn(), "P9"))
    ports = _ports(labels=_chunk_table())
    ports.fixes = [_link_fix("P1")]

    await _run(ports, provider)

    assert len(provider.stream_calls) == 1
    assert not [f for f in ports.frames if isinstance(f, ResetFrame)]
    [fix] = ports.fix_calls
    assert fix.element.element_id == "e2" and fix.attempt == 1 and fix.text == CLEAN_PREFIX
    assert [f.block_id for f in ports.frames if isinstance(f, ElementFrame)] == ["b2", "b3"]
    [turn] = ports.committed
    assert turn.text == CLEAN_PREFIX
    links = [e for e in turn.output.p3_markup.elements if e.kind == "material_link"]
    assert [(e.element_id, e.chunk) for e in links] == [("e2", "P1")]
    assert [b.block_id for b in turn.output.p2_body.blocks] == ["b1", "b2", "b3"]


async def test_an_element_that_cannot_be_fixed_is_removed_with_its_block() -> None:
    """Every fix still has the unknown label (or is another element, or another kind): at most
    `guard_max_retries` calls, then the element goes with its block; the text is whole."""
    provider = ScriptedLLMProvider()
    _answers(provider, _with_link(_turn(), "P9"))
    ports = _ports(labels=_chunk_table())
    ports.fixes = [_link_fix("P8"), _link_fix("P1", element_id="e9"), _link_fix("P1")]

    with capture_logs() as logs:
        await _run(ports, provider, guard_max_retries=2)

    assert len(ports.fix_calls) == 2 and len(provider.stream_calls) == 1
    assert [f.attempt for f in ports.fix_calls] == [1, 2]
    assert [f.block_id for f in ports.frames if isinstance(f, ElementFrame)] == ["b2"]
    [turn] = ports.committed
    assert turn.text == CLEAN_PREFIX
    assert [e.element_id for e in turn.output.p3_markup.elements] == ["e1"]
    assert [b.block_id for b in turn.output.p2_body.blocks] == ["b1", "b2"]
    assert any(e["event"] == "guard_violation" and e["rule"] == "element_markup" for e in logs)


async def test_an_element_fix_of_another_kind_is_not_taken() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _with_link(_turn(), "P9"))
    ports = _ports(labels=_chunk_table())
    ports.fixes = [{"element_id": "e2", "kind": "card_series", "cards": []}]

    await _run(ports, provider, guard_max_retries=1)

    assert len(ports.fix_calls) == 1
    [turn] = ports.committed
    assert [e.element_id for e in turn.output.p3_markup.elements] == ["e1"]


async def test_an_element_with_an_unknown_label_and_no_block_is_dropped_unfixed() -> None:
    """No block shows it, so there is nothing to fix; it must not reach the write."""
    provider = ScriptedLLMProvider()
    turn = _with_link(_turn(), "P9")
    turn["p2_body"]["blocks"].pop()  # the link element has no block now
    _answers(provider, turn)
    ports = _ports(labels=_chunk_table())

    await _run(ports, provider)

    assert ports.fix_calls == []
    [committed] = ports.committed
    assert [e.element_id for e in committed.output.p3_markup.elements] == ["e1"]


async def test_task_markup_without_a_block_is_checked_too() -> None:
    """Т-§5.3: a task of `p3_markup.tasks` that no block points at went to the commit unchecked.
    A bad one (level 1 without options, an unknown skill label) is dropped; a good one stays."""
    provider = ScriptedLLMProvider()
    orphan = {"element_id": "e5", "kind": "short_answer", "prompt": "p", "ask_confidence": False}
    good = {**_task(), "element_id": "e6"}
    bad_level = {**_task(level=1), "element_id": "e7"}
    bad_label = {**_task(), "element_id": "e8", "tests": [{"skill": "S9", "level": 3}]}
    _answers(
        provider,
        _turn(
            tasks=[_task(), good, bad_level, bad_label],
            elements=[_markup()["elements"][0], orphan],
        ),
    )
    ports = _ports()

    await _run(ports, provider)

    [turn] = ports.committed
    assert [t.element_id for t in turn.output.p3_markup.tasks] == ["e1", "e6"]
    assert [e.element_id for e in turn.output.p3_markup.elements] == ["e1", "e5"]


class _CutSecondStep:
    """The second streamed step ends `incomplete` (Т-§11.1): the text came, the answer did not."""

    def __init__(self, inner: ScriptedLLMProvider) -> None:
        self._inner = inner
        self._calls = 0

    async def stream_structured(self, request: StreamRequest) -> AsyncIterator[StreamEvent]:
        self._calls += 1
        async for event in self._inner.stream_structured(request):
            if self._calls == 2 and isinstance(event, StreamCompleted):
                event = replace(event, status="incomplete")
            yield event


async def test_a_cut_step_after_tools_leaves_no_shown_text_behind() -> None:
    """The client dropped the cut step's text (`reset {guard}`); the next step starts clean, so a
    spoiler held back in its buffer is not announced with a second, pointless `reset`."""
    provider = ScriptedLLMProvider()
    provider.script_tools(SCHEMA, ScriptedToolStep((ScriptedToolCall("skill_history", {}),)))
    _answers(provider, _turn(), _turn(f"The sum is {ANSWER}."), _turn())
    ports = _ports()

    cut = _CutSecondStep(provider)
    await run_turn(
        ports,
        cut,
        settings=_settings(spoiler_holdback_chars=20, spoiler_guard_mode="enforce"),
        cancel=asyncio.Event(),
    )

    resets = [f for f in ports.frames if isinstance(f, ResetFrame)]
    assert resets == [ResetFrame(reason="guard")]
    assert ports.frames[ports.frames.index(resets[0]) + 1] == StatusFrame(state="thinking")
    [turn] = ports.committed
    assert ANSWER not in turn.text


# --- the spoiler guard in `shadow`, and what the guard records (guard_events, samples) ---------


SPOILED = CLEAN_PREFIX + f"The sum is {ANSWER}. Check it."


def _events(turn: TurnToCommit) -> list[tuple[str, str, str, bool]]:
    return [(e.rule, e.reason, e.phase, e.retried) for e in turn.guard_events]


async def test_shadow_spoiler_changes_nothing_in_the_turn_and_is_recorded() -> None:
    """`shadow` (the default): the reply with the answer is shown whole, at once, with one model
    attempt, no `reset`, no cut sentence; the hit is a `guard_events` entry and a sample."""
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(SPOILED))
    ports = _ports()

    with capture_logs() as logs:
        await _run(ports, provider)

    assert len(provider.stream_calls) == 1
    assert not [f for f in ports.frames if isinstance(f, ResetFrame)]
    assert _shown(ports.frames) == SPOILED  # nothing was held back, nothing was cut
    [turn] = ports.committed
    assert turn.text == SPOILED and turn.guard_retries == 0
    assert _events(turn) == [("spoiler", "answer_in_text", "spoiler_shadow", False)]
    [sample] = turn.guard_samples
    assert (sample.rule, sample.reason, sample.answer_token) == ("spoiler", "shadow", ANSWER)
    assert sample.sentence == f"The sum is {ANSWER}."  # the matched sentence, not the whole reply
    assert any(e["event"] == "guard_violation" and e["mode"] == "shadow" for e in logs)


async def test_shadow_does_not_hold_the_text_back() -> None:
    """Without the check there is nothing to wait for: the text goes out as it comes."""
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(CLEAN_PREFIX * 2))
    ports = _ports()

    await _run(ports, provider, spoiler_holdback_chars=100_000)

    tokens = [f for f in ports.frames if isinstance(f, TokenFrame)]
    assert len(tokens) > 1 and _shown(ports.frames) == CLEAN_PREFIX * 2


async def test_shadow_clean_turn_records_nothing() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn())
    ports = _ports()

    await _run(ports, provider)

    [turn] = ports.committed
    assert turn.guard_events == () and turn.guard_samples == ()


async def test_sample_is_cut_to_the_setting() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(SPOILED))
    ports = _ports()

    await _run(ports, provider, guard_sample_max_chars=6)

    [sample] = ports.committed[0].guard_samples
    assert sample.sentence == "The su" and sample.answer_token == ANSWER


async def test_enforce_spoiler_is_recorded_with_its_retries() -> None:
    """`enforce` stays what it was (the rollback, the `reset`); every firing is an event, a
    returned attempt is `retried`, and the sentence of each is a sample."""
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(SPOILED), _turn())
    ports = _ports()

    await _run(ports, provider, spoiler_holdback_chars=20, spoiler_guard_mode="enforce")

    [turn] = ports.committed
    assert turn.guard_retries == 1 and ANSWER not in turn.text
    assert _events(turn) == [("spoiler", "answer_in_text", "spoiler", True)]
    [sample] = turn.guard_samples
    assert sample.reason == "enforce" and sample.answer_token == ANSWER
    assert sample.sentence.startswith("The sum is") and ANSWER in sample.sentence


async def test_enforce_final_spoiler_is_recorded_not_retried() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(SPOILED))
    ports = _ports()

    await _run(
        ports,
        provider,
        spoiler_holdback_chars=20,
        guard_max_retries=1,
        spoiler_guard_mode="enforce",
    )

    [turn] = ports.committed
    assert _events(turn) == [
        ("spoiler", "answer_in_text", "spoiler", True),
        ("spoiler", "answer_in_text", "spoiler", False),
    ]
    assert ANSWER not in turn.text


async def test_guard_events_name_the_head_the_markup_and_the_cut_step() -> None:
    """Names only: a rolled-back head is `retried`, a lowered stance and the markup are not."""
    provider = ScriptedLLMProvider()
    _answers(
        provider,
        _turn("Unmarked.", head=_head(targetSkills=[])),
        _turn(
            head=_head(verdict_stance="code"),
            exposures=[{"skill": "S9", "kind": "explained", "approach": "none"}],
        ),
    )
    ports = _ports(verdict=_verdict("model"))

    await _run(ports, provider)

    [turn] = ports.committed
    assert _events(turn) == [
        ("head", "no_target_skills", "head", True),
        ("head", "stance_without_code_verdict", "head", False),
        ("markup", "unknown_skill_label", "markup", False),
    ]
    assert "S9" not in str(turn.guard_events)


async def test_guard_event_of_a_jumped_boundary() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(head=_head(targetSkills=["S1"])))
    ports = _ports(boundary=_jump())

    await _run(ports, provider)

    assert _events(ports.committed[0]) == [("boundary", "boundary_jump", "head", False)]


async def test_guard_event_of_a_cut_step() -> None:
    provider = ScriptedLLMProvider()
    provider.script_tools(SCHEMA, ScriptedToolStep((ScriptedToolCall("skill_history", {}),)))
    _answers(provider, _turn(), _turn(), _turn())
    ports = _ports()

    await run_turn(ports, _CutSecondStep(provider), settings=_settings(), cancel=asyncio.Event())

    assert ("incomplete", "incomplete_output", "cut", True) in _events(ports.committed[0])


# --- stage timings (owner decision 2026-11-19) ---


async def test_the_turn_carries_its_stage_timings() -> None:
    provider = ScriptedLLMProvider()
    provider.script_tools(SCHEMA, ScriptedToolStep((ScriptedToolCall("skill_history", {}),)))
    _answers(provider, _turn())
    ports = _ports()

    await _run(ports, provider)

    timings = ports.committed[0].timings
    assert {"admit_ms", "assemble_total_ms", "first_status_ms", "ttft_ms", "total_ms"} <= set(
        timings
    )
    assert all(isinstance(v, int) and v >= 0 for v in timings.values())
    assert timings["first_status_ms"] <= timings["ttft_ms"] <= timings["total_ms"]
    assert timings["admit_ms"] + timings["assemble_total_ms"] <= timings["total_ms"]
    assert (timings["model_attempts"], timings["tool_steps"], timings["guard_retries"]) == (2, 1, 0)


async def test_the_timings_count_the_retries_of_the_guard() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(head=_head(targetSkills=[])), _turn())
    ports = _ports()

    await _run(ports, provider)

    timings = ports.committed[0].timings
    assert (timings["model_attempts"], timings["guard_retries"]) == (2, 1)


async def test_a_failed_measurement_does_not_break_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quack.session import turn_timings

    def broken(_start: int) -> int:
        raise RuntimeError("clock")

    monkeypatch.setattr(turn_timings, "since_ms", broken)
    provider = ScriptedLLMProvider()
    _answers(provider, _turn())
    ports = _ports()

    seq = await _run(ports, provider)

    assert seq == 42 and ports.committed[0].timings == {}
