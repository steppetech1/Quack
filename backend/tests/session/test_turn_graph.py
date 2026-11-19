"""ТЗ Ф4 §8.9 T-TT2, T-TT3, T-TT4, T-TT5, T-TT6 at the turn graph (WP13.2, WP13.4, WP13.5).

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
from quack.session.guards import Boundary
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
            "completed_steps": [],
            "current_step": "sum of a geometric series",
            "next_steps": [],
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

    async def fix_markup(self, fix: MarkupFix) -> TurnTask | None:
        """The n-th fix answer is `fixes[n]` (the last repeats); none — no fix."""
        self.fix_calls.append(fix)
        if not self.fixes:
            return None
        answer = self.fixes.pop(0) if len(self.fixes) > 1 else self.fixes[0]
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

    await _run(ports, provider, spoiler_holdback_chars=40)

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

    await _run(ports, provider, spoiler_holdback_chars=20)

    resets = [f for f in ports.frames if isinstance(f, ResetFrame)]
    assert resets == [ResetFrame(reason="spoiler")]
    before = ports.frames[: ports.frames.index(resets[0])]
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

    await _run(ports, provider, spoiler_holdback_chars=20, guard_max_retries=2)

    assert len(provider.stream_calls) == 3
    assert ANSWER not in _shown(ports.frames)
    [turn] = ports.committed
    assert ANSWER not in turn.text and "Check it." in turn.text
    assert turn.guard_retries == 2
    body = [b.text for b in turn.output.p2_body.blocks if b.text is not None]
    assert all(ANSWER not in text for text in body)


# --- T-TT4 ---


async def test_head_guards() -> None:
    """Honest verdict and «the turn is marked»: violation → retry before the body is shown."""
    provider = ScriptedLLMProvider()
    _answers(
        provider,
        _turn("Unmarked.", head=_head(targetSkills=[])),
        _turn("Code stance.", head=_head(verdict_stance="code")),
        _turn(),
    )
    ports = _ports(verdict=_verdict("model"))

    await _run(ports, provider)

    assert len(provider.stream_calls) == 3
    assert _shown(ports.frames) == CLEAN_PREFIX
    assert not [f for f in ports.frames if isinstance(f, ResetFrame)]
    reasons = str(provider.stream_calls[2].input_items)
    assert "no_target_skills" in reasons and "stance_without_code_verdict" in reasons


async def test_stance_settled_after_retries() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(head=_head(verdict_stance="code")))
    ports = _ports(verdict=_verdict("model"))

    await _run(ports, provider, guard_max_retries=1)

    assert len(provider.stream_calls) == 2
    [turn] = ports.committed
    assert turn.output.p1_head.verdict_stance == "formative"
    assert turn.guard_retries == 1


async def test_code_stance_with_code_verdict_passes() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(head=_head(verdict_stance="code")))
    ports = _ports(verdict=_verdict("code"))

    await _run(ports, provider)

    assert len(provider.stream_calls) == 1
    assert ports.committed[0].output.p1_head.verdict_stance == "code"


def _jump() -> Boundary:
    """S1 needs S2, which was neither shown nor practised."""
    pre = uuid4()
    return Boundary(
        requires={"S1": (RequiresLink(skill_id=pre, confidence=0.9),)},
        skill_ids={"S1": uuid4(), "S2": pre},
        min_confidence=0.6,
    )


async def test_boundary_jump_retried_before_the_body() -> None:
    """«Do not jump the boundary» at the head: retry before any body text; a turn that takes
    the prerequisite too passes."""
    provider = ScriptedLLMProvider()
    _answers(
        provider,
        _turn("Jumped.", head=_head(targetSkills=["S1"])),
        _turn(head=_head(targetSkills=["S1", "S2"])),
    )
    ports = _ports(boundary=_jump())

    await _run(ports, provider)

    assert len(provider.stream_calls) == 2
    assert _shown(ports.frames) == CLEAN_PREFIX
    assert "boundary_jump" in str(provider.stream_calls[1].input_items)


async def test_boundary_jump_after_retries_is_shown_and_logged() -> None:
    provider = ScriptedLLMProvider()
    _answers(provider, _turn(head=_head(targetSkills=["S1"])))
    ports = _ports(boundary=_jump())

    with capture_logs() as logs:
        await _run(ports, provider, guard_max_retries=1)

    assert len(provider.stream_calls) == 2 and len(ports.committed) == 1
    assert _shown(ports.frames) == CLEAN_PREFIX
    assert any(e["event"] == "guard_violation" and "boundary_jump" in e["reasons"] for e in logs)


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


async def test_markup_labels_reset_after_shown_text() -> None:
    """Unknown label in `p3_markup` after the body was shown → `reset {guard}`, retry."""
    provider = ScriptedLLMProvider()
    bad = _turn(exposures=[{"skill": "S9", "kind": "explained", "approach": "none"}])
    _answers(provider, bad, _turn())
    ports = _ports()

    await _run(ports, provider, spoiler_holdback_chars=20)

    assert [f for f in ports.frames if isinstance(f, ResetFrame)] == [ResetFrame(reason="guard")]
    assert "unknown_skill_label" in str(provider.stream_calls[1].input_items)
    assert ports.committed[0].guard_retries == 1


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


async def test_unresolvable_labels_are_retried_then_dropped_from_the_turn() -> None:
    """`cancel_requests: ["R9"]`, a request of an unknown topic and a link to an unknown chunk
    used to reach `turn_batch` after the text was shown (`UnknownLabelError`, `SERVICE_UNAVAILABLE`,
    no turn in the journal). Now the guard returns the turn to the model naming the labels and,
    after the retries, takes them out of what is committed; the element goes with its block."""
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

    await _run(ports, provider, guard_max_retries=1)

    assert len(provider.stream_calls) == 2
    retry_input = str(provider.stream_calls[1].input_items)
    assert "unknown_label" in retry_input and "R9" in retry_input and "P9" in retry_input
    [turn] = ports.committed
    markup = turn.output.p3_markup
    assert markup.plan_update.cancel_requests == ["R1"]
    assert markup.agent_requests == []
    assert [e.element_id for e in markup.elements] == ["e1"]
    assert [b.block_id for b in turn.output.p2_body.blocks] == ["b1", "b2"]
    assert [f.block_id for f in ports.frames if isinstance(f, ElementFrame)] == ["b2"]


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
        ports, cut, settings=_settings(spoiler_holdback_chars=20), cancel=asyncio.Event()
    )

    resets = [f for f in ports.frames if isinstance(f, ResetFrame)]
    assert resets == [ResetFrame(reason="guard")]
    [turn] = ports.committed
    assert ANSWER not in turn.text
