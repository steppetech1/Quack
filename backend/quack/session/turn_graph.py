"""Teacher turn graph (Т-§12.1, Т-§12.2, Т-§12.4, Т-§11.5; ТЗ Ф4 WP13.2, WP13.4, WP13.5).

LangGraph without checkpoints, in the API process: `admit → assemble → model ⇄ tools →
guard → commit`. What touches the stores is a port of the caller (`TurnPorts`): admission
with the turn lock, context assembly (WP14), tool execution (WP14.4), the write of
`turn.committed` with the manifest (WP2.2, WP13.9) and the SSE frames (§4.7).

The order of the output keys is the order of the checks: `p1_head` < `p2_body` < `p3_markup`.
Only what is found **before the text is shown** returns the turn to the model (the rollback is
cheap and the student sees nothing); once the text is shown, code settles what is wrong and the
turn is committed, so the student never watches an answer vanish for a markup mistake.

- **model** streams one step (`stream_step`, WP10.2). The head is checked by code as soon as
  `p1_head` closes (WP13.5): an empty `targetSkills` or an unknown skill label stops the stream
  before any body character is shown and returns the turn (`HEAD_REJECTIONS`); a `code` stance
  without a code verdict is lowered to `formative` at once and «do not jump the boundary» is only
  logged (`guard_violation`, rule `boundary`, with the labels and a count), neither goes back to
  the model. In `spoiler_guard_mode = "enforce"` the body goes through the holdback buffer
  (`HoldbackBuffer`, WP13.4 stage 3); a spoiler stops the stream, `reset {spoiler}` if something
  was shown. In `"shadow"` (the default, owner decision 2026-11-19) the text goes out at once, the
  spoiler check runs on the finished body and only records the hit. A cut answer is streamed
  again with the doubled limit (`reset {guard}` if its text was shown). After every `reset` the
  stream says `status thinking` again, so the client knows the turn goes on. A step with tool
  calls goes to **tools** (`status: tool` per call) and back.
- **guard** routes a rejected attempt (a head or a spoiler) back to **model** with its reasons
  (a developer message, D-B-148), up to `guard_max_retries`; after the retries an unmarked turn is
  shown and logged and a spoiler sentence is removed by code (the cleaned text goes out in
  several `token` frames). The markup (`p3_markup`) never returns the turn: at every turn code
  takes the unknown labels (exposures, `targetsMisconception`, agent requests, `cancel_requests`,
  landmarks of the plan, the deadline) out of it, makes the slug of a plan step `id` and cuts
  `next_steps` to `plan_next_steps_max`, and logs the case (`guard_violation`, rule `markup`).
  Elements are checked after that (Т-§5.3): bad markup of a task element, or an unknown label of
  another element (`card_series`, `visualization`, `material_link`), goes to a separate fix call
  «fix the markup for this text» (`TurnPorts.fix_markup`, WP13.4 п.4, D-B-150), the text stays,
  the call is invisible to the student; still bad after `guard_max_retries` fixes — the element
  is not shown and leaves the committed turn with its block.
- **commit** writes through the port unless the turn was cancelled (`TurnCancelled`): a new
  action during the turn cancels it without a write (Т-§12.1, `TurnSlots`).

What every rule fired on is collected as names (`GuardEventRecord`: rule, reason, phase, whether it
returned the turn) and goes into `turn.committed.guard_events`; the sentence of a spoiler hit
(`GuardSample`, cut to `guard_sample_max_chars`) goes to the port, which keeps it in
`ops.guard_samples` after the commit, best effort (law 7). No learner text, no reply is kept.

Not here yet: «generated is checked» (WP12.5).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol, TypedDict
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from quack.config import Settings
from quack.events.event_types import DegradedMode
from quack.events.records import GuardEventRecord
from quack.llm.errors import ProviderIncompleteError
from quack.llm.labels import LabelMap
from quack.llm.streaming import (
    BlockClosed,
    BlockText,
    HeadClosed,
    MarkupClosed,
    StreamingLLMProvider,
    StreamRequest,
    StreamUsage,
    TurnStreamParser,
    next_step_input,
    tool_calls,
)
from quack.llm.tool_loop import (
    Step,
    ToolLoopLimitError,
    add_usage,
    doubled,
    run_tools,
    stream_step,
)
from quack.logging import get_logger
from quack.practice.check import Verdict
from quack.practice.markup import validate_markup
from quack.session.actions import (
    ElementFrame,
    ResetFrame,
    StatusFrame,
    TokenFrame,
    TurnCommittedFrame,
    VerdictFrame,
)
from quack.session.elements import TASK_ELEMENT_KINDS, Element
from quack.session.guards import (
    Boundary,
    GuardViolation,
    boundary_gaps,
    drop_unknown_head_labels,
    drop_unknown_markup_labels,
    head_rejections,
    head_violations,
    markup_violations,
    settle_stance,
    unresolved_element_labels,
)
from quack.session.holdback import HoldbackBuffer, SpoilerDetected
from quack.session.spoiler_guard import SpoilerHit
from quack.session.teacher_output import TeacherOutputV2, TurnHead, TurnTask
from quack.session.turn_timings import TurnTimer, now_ns

log = get_logger(__name__)

type Role = Literal["teacher", "teacher_fallback"]
type Effort = Literal["low", "high"]
type Frame = (
    StatusFrame | VerdictFrame | TokenFrame | ResetFrame | ElementFrame | TurnCommittedFrame
)

# D-B-148: text blocks of `p2_body` go to the `token` stream joined by an empty line.
BLOCK_SEPARATOR = "\n\n"


class TurnCancelled(Exception):
    """A new action of the session cancelled this turn: nothing is written (Т-§12.1)."""


@dataclass(frozen=True)
class GuardInputs:
    """What the code rules of Т-§12.4 need from the context: the verdict of the answered
    answer, the label table (Т-§4.5) and the spoiler check of the active tasks."""

    answered_verdict: Verdict | None
    skill_labels: frozenset[str]
    misconception_labels: frozenset[str]
    task_labels: frozenset[str]
    has_spoiler: Callable[[str], bool]
    remove_spoiler: Callable[[str], str]
    boundary: Boundary | None = None
    # The sentences of a text that hold an answer of an active task, for `ops.guard_samples`.
    spoiler_hits: Callable[[str], Sequence[SpoilerHit]] | None = None
    # The label table of the call: with it the guard also checks every label the write turns
    # into an id (requests, `cancel_requests`, elements, deadline), Т-§12.4.
    labels: LabelMap | None = None


@dataclass(frozen=True)
class AssembledTurn:
    """The output of **assemble**: the model request (context rendered, tools, effort), the
    guard inputs, the fast verdict that came while assembling (`verdict` frame)."""

    request: StreamRequest
    guard: GuardInputs
    effort: Effort
    degraded_mode: DegradedMode | None
    verdict: VerdictFrame | None = None
    parse_output: Callable[[str], TeacherOutputV2] = TeacherOutputV2.model_validate_json


@dataclass(frozen=True)
class GuardSample:
    """A row of `ops.guard_samples`: the sentence of the teacher reply with an answer of an
    active task and that answer, both cut to `guard_sample_max_chars`; `reason` is the mode that
    found it (`shadow` — nothing changed, `enforce` — the turn was returned or the sentence cut)."""

    rule: str
    reason: Literal["shadow", "enforce"]
    sentence: str
    answer_token: str


@dataclass(frozen=True)
class TurnToCommit:
    """What **commit** writes: the output as shown (head settled, removed elements gone,
    spoiler sentences removed), the shown body text, who answered and how; what the code rules
    fired on (`guard_events`, names only), the spoiler sentences for `ops.guard_samples` and the
    stage timings of the turn so far (`timings`, `turn_timings.py`; milliseconds and counts)."""

    output: TeacherOutputV2
    text: str
    role: Role
    model: str
    effort: Effort
    degraded_mode: DegradedMode | None
    guard_retries: int
    usage: StreamUsage
    guard_events: tuple[GuardEventRecord, ...] = ()
    guard_samples: tuple[GuardSample, ...] = ()
    timings: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class MarkupFix:
    """A fix call of one element (WP13.4 п.4): the element, its task markup as the model wrote
    it (`None` if missing, and for an element that is no task), what code found wrong
    (`validate_markup` rules; for another element `unknown_label: P9 (chunk)`), the body text as
    shown, the fix attempt (1…`guard_max_retries`)."""

    element: Element
    task: TurnTask | None
    problems: list[str]
    text: str
    attempt: int


class TurnPorts(Protocol):
    async def admit(self) -> Role:
        """Turn lock and admission to the model (WP13.2 admit); the role that answers."""
        ...

    async def assemble(self, role: Role) -> AssembledTurn: ...

    async def execute_tool(self, name: str, arguments: str) -> str: ...

    async def commit(self, turn: TurnToCommit) -> int:
        """Write `turn.committed` with its side events and the manifest; its `seq`."""
        ...

    async def emit(self, frame: Frame) -> None: ...

    async def fix_markup(self, fix: MarkupFix) -> TurnTask | Element | None:
        """The model call «fix the markup for this text»: the task markup of a task element,
        the element itself for another kind (an unknown label); `None` — no fix came."""
        ...


class TurnSlots:
    """One active turn per session in this process: claiming a session cancels the turn
    that holds it and waits until it is gone (Т-§12.1, «без Redis — флаг в памяти
    процесса»)."""

    def __init__(self) -> None:
        self._active: dict[UUID, tuple[asyncio.Event, asyncio.Event]] = {}

    @asynccontextmanager
    async def claim(self, session_id: UUID) -> AsyncIterator[asyncio.Event]:
        while (held := self._active.get(session_id)) is not None:
            cancel, done = held
            cancel.set()
            await done.wait()
        cancel, done = asyncio.Event(), asyncio.Event()
        self._active[session_id] = (cancel, done)
        try:
            yield cancel
        finally:
            del self._active[session_id]
            done.set()


# --- one streamed attempt ---


class _HeadRejected(Exception):
    def __init__(self, violations: list[GuardViolation]) -> None:
        self.violations = violations


class _Attempt:
    """Text side of one model step: parser, head check, holdback, frames."""

    def __init__(self, run: _Run, *, final: bool, head_checked: bool, shown: bool) -> None:
        self._run = run
        self._final = final
        self._parser = TurnStreamParser()
        # `shadow`: nothing is held back and nothing is checked on the way; the finished body is
        # checked once in `_guard`. `enforce`: the holdback buffer of WP13.4.
        if run.settings.spoiler_guard_mode == "shadow":
            self._buffer = HoldbackBuffer(0, lambda _: False)
        else:
            self._buffer = HoldbackBuffer(
                run.settings.spoiler_holdback_chars, run.guard.has_spoiler
            )
        self.head: TurnHead | None = None
        self.head_checked = head_checked
        self.head_violations: list[GuardViolation] = []
        self.shown = shown
        self.spoiled = False
        self.markup_closed = False
        self._pending: list[str] = []
        self._text_blocks: set[int] = set()
        self.body = ""

    async def on_text(self, delta: str) -> None:
        if self._run.cancel.is_set():
            raise TurnCancelled
        for event in self._parser.feed(delta):
            if isinstance(event, HeadClosed):
                await self._head(event.head)
            elif isinstance(event, BlockText):
                await self._block_text(event.index, event.text)
            elif isinstance(event, MarkupClosed):
                self.markup_closed = True
            elif isinstance(event, BlockClosed):
                pass

    async def finish(self) -> None:
        """End of the answer step: release the rest of the body."""
        if self.spoiled:
            return
        try:
            await self._show(self._buffer.finish())
        except SpoilerDetected:
            await self._spoiler()

    def output_text(self) -> str:
        return self._parser.close()

    async def _head(self, text: str) -> None:
        head = TurnHead.model_validate_json(text)
        guard = self._run.guard
        violations = head_violations(
            head,
            answered_verdict=guard.answered_verdict,
            skill_labels=guard.skill_labels,
            boundary=guard.boundary,
        )
        rejected = head_rejections(violations)
        if rejected and not self._final:
            for v in rejected:
                _note(self._run, "head", v.reason, "head", retried=True)
            raise _HeadRejected(rejected)
        _log_head(self._run, head, violations)
        self.head = drop_unknown_head_labels(
            settle_stance(head, guard.answered_verdict), guard.skill_labels
        )
        self.head_violations = violations
        self.head_checked = True
        pending, self._pending = self._pending, []
        for chunk in pending:
            await self._feed(chunk)

    async def _block_text(self, index: int, text: str) -> None:
        if index not in self._text_blocks:
            if self._text_blocks:
                text = BLOCK_SEPARATOR + text
            self._text_blocks.add(index)
        self.body += text
        if not self.head_checked:
            self._pending.append(text)  # body before head: full buffering (Т-§12.2)
            return
        await self._feed(text)

    async def _feed(self, text: str) -> None:
        if self.spoiled:
            return
        try:
            await self._show(self._buffer.feed(text))
        except SpoilerDetected:
            await self._spoiler()

    async def _show(self, text: str) -> None:
        if text:
            self.shown = True
            await _emit(self._run, TokenFrame(text=text))

    async def _spoiler(self) -> None:
        _note(self._run, "spoiler", "answer_in_text", "spoiler", retried=not self._final)
        _sample(self._run, self.body, "enforce")
        if self.shown:
            await _reset(self._run, "spoiler")
        if not self._final:
            raise SpoilerDetected
        # After the retries code removes the sentence (Т-§12.4); the rest is shown at the end.
        self.spoiled = True
        self.shown = False


def _note(
    run: _Run,
    rule: str,
    reason: str,
    phase: Literal["head", "markup", "spoiler", "spoiler_shadow", "cut"],
    *,
    retried: bool = False,
) -> None:
    """A firing of a code rule, by names, for `turn.committed.guard_events`."""
    run.events.append(GuardEventRecord(rule=rule, reason=reason, phase=phase, retried=retried))


def _sample(run: _Run, text: str, reason: Literal["shadow", "enforce"]) -> None:
    """The sentences of `text` with an answer of an active task, for `ops.guard_samples`."""
    hits = run.guard.spoiler_hits
    if hits is None:
        return
    limit = run.settings.guard_sample_max_chars
    run.samples.extend(
        GuardSample("spoiler", reason, hit.sentence[:limit], hit.answer[:limit])
        for hit in hits(text)
    )


def _log_head(run: _Run, head: TurnHead, violations: list[GuardViolation]) -> None:
    """The head violations that did not return the turn: a lowered stance, the labels dropped
    after the retries (rule `head`), and the boundary (rule `boundary`, only logged: the labels
    of the skills and of the prerequisites, and how many prerequisites are missing)."""
    guard = run.guard
    for v in violations:
        _note(run, "boundary" if v.reason == "boundary_jump" else "head", v.reason, "head")
    reasons = [v.reason for v in violations if v.reason != "boundary_jump"]
    if reasons:
        log.warning("guard_violation", rule="head", reasons=reasons)
    if guard.boundary is None or all(v.reason != "boundary_jump" for v in violations):
        return
    gaps = boundary_gaps(head, guard.boundary)
    log.warning(
        "guard_violation",
        rule="boundary",
        reasons=["boundary_jump"],
        skills=[g.skill for g in gaps],
        missing=[label for g in gaps for label in g.missing],
        count=sum(len(g.missing) + g.unlabeled for g in gaps),
    )


async def _reset(run: _Run, reason: Literal["spoiler", "guard"]) -> None:
    """`reset` drops the shown text; the turn goes on, so the client hears `thinking` again."""
    await _emit(run, ResetFrame(reason=reason))
    await _emit(run, StatusFrame(state="thinking"))


# Where the cleaned text after a spoiler is cut into `token` frames: a sentence end (the space
# stays with the sentence before it) or an empty line.
_PIECE_END = re.compile(r"(?<=[.!?…])\s+|\n{2,}")


def _pieces(text: str) -> list[str]:
    """`text` in sentences and paragraphs; the pieces joined are `text`."""
    pieces: list[str] = []
    start = 0
    for end in _PIECE_END.finditer(text):
        pieces.append(text[start : end.end()])
        start = end.end()
    if start < len(text):
        pieces.append(text[start:])
    return pieces


# --- graph ---


@dataclass
class _Run:
    ports: TurnPorts
    provider: StreamingLLMProvider
    settings: Settings
    cancel: asyncio.Event
    role: Role = "teacher"
    assembled: AssembledTurn | None = None
    attempt: _Attempt | None = None
    events: list[GuardEventRecord] = field(default_factory=list)
    samples: list[GuardSample] = field(default_factory=list)
    timer: TurnTimer = field(default_factory=TurnTimer)

    @property
    def guard(self) -> GuardInputs:
        assert self.assembled is not None
        return self.assembled.guard


class _State(TypedDict):
    request: StreamRequest
    retries: int
    steps: int
    cut_retried: bool
    usage: StreamUsage
    step: Step
    outcome: Literal["tools", "cut", "answer", "retry"]
    reasons: list[GuardViolation]
    output: TeacherOutputV2
    text: str
    model: str
    seq: int


# A node returns the keys it changes.
type _Update = dict[str, Any]


async def _cancellable[T](run: _Run, work: Coroutine[Any, Any, T]) -> T:
    """Run `work` until it ends or a new action cancels the turn (WP13.2 «стримящий ход
    прерывается»): a step without text (thinking, tools, a markup fix) stops too."""
    if run.cancel.is_set():
        work.close()
        raise TurnCancelled
    task = asyncio.ensure_future(work)
    waiter = asyncio.ensure_future(run.cancel.wait())
    try:
        await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
    if not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        raise TurnCancelled
    return task.result()


def _run_of(config: RunnableConfig) -> _Run:
    return config.get("configurable", {})["run"]


async def _emit(run: _Run, frame: Frame) -> None:
    """Send a frame; the first `status: thinking` and the first `token` are timed on the way."""
    if isinstance(frame, StatusFrame) and frame.state == "thinking":
        run.timer.mark_once("first_status_ms")
    elif isinstance(frame, TokenFrame):
        run.timer.mark_once("ttft_ms")
    await run.ports.emit(frame)


async def _admit(state: _State, config: RunnableConfig) -> _Update:
    run = _run_of(config)
    started = now_ns()
    run.role = await run.ports.admit()
    run.timer.put_since("admit_ms", started)
    await _emit(run, StatusFrame(state="thinking"))
    return {}


async def _assemble(state: _State, config: RunnableConfig) -> _Update:
    run = _run_of(config)
    started = now_ns()
    run.assembled = await run.ports.assemble(run.role)
    run.timer.put_since("assemble_total_ms", started)
    if run.assembled.verdict is not None:
        await _emit(run, run.assembled.verdict)
    return {
        "request": run.assembled.request,
        "retries": 0,
        "steps": 0,
        "cut_retried": False,
        "usage": StreamUsage(),
    }


async def _model(state: _State, config: RunnableConfig) -> _Update:
    run = _run_of(config)
    run.timer.model_attempts += 1
    final = state["retries"] >= run.settings.guard_max_retries
    previous = run.attempt if state["steps"] > 0 else None
    attempt = _Attempt(
        run,
        final=final,
        head_checked=False,
        shown=previous is not None and previous.shown,
    )
    run.attempt = attempt
    request = state["request"]
    usage = state["usage"]
    try:
        step = await _cancellable(run, stream_step(run.provider, request, attempt.on_text))
        usage = add_usage(usage, step.completed.usage)
        if step.completed.status == "incomplete":
            # Т-§11.1: a cut answer is never parsed; once more with the doubled limit.
            if state["cut_retried"]:
                raise ProviderIncompleteError(step.text, headers=step.completed.headers)
            _note(run, "incomplete", "incomplete_output", "cut", retried=True)
            if attempt.shown:
                await _reset(run, "guard")
                attempt.shown = False  # the client dropped it: the next step starts clean
            limit = doubled(request.base.max_output_tokens, run.settings.max_output_tokens_cap)
            request = replace(request, base=replace(request.base, max_output_tokens=limit))
            return {"request": request, "cut_retried": True, "usage": usage, "outcome": "cut"}
        if tool_calls(step.items):
            return {"step": step, "usage": usage, "outcome": "tools"}
        await attempt.finish()
    except _HeadRejected as rejected:
        return {"usage": usage, "outcome": "retry", "reasons": rejected.violations}
    except SpoilerDetected:
        reason = GuardViolation("spoiler", "the text reveals the answer of an active task")
        return {"usage": usage, "outcome": "retry", "reasons": [reason]}
    return {"step": step, "usage": usage, "outcome": "answer", "model": step.completed.model}


async def _tools(state: _State, config: RunnableConfig) -> _Update:
    run = _run_of(config)
    steps = state["steps"] + 1
    run.timer.tool_steps += 1
    if steps >= run.settings.tool_loop_max_steps:
        raise ToolLoopLimitError(f"no answer after {steps} model steps")
    calls = tool_calls(state["step"].items)
    for call in calls:
        await _emit(run, StatusFrame(state="tool", tool=call.name))
    outputs = await _cancellable(
        run, run_tools(run.ports.execute_tool, calls, run.settings.tool_timeout_ms)
    )
    request = state["request"]
    items = next_step_input(request.input_items, state["step"].items, outputs)
    return {"request": replace(request, input_items=items), "steps": steps}


async def _guard(state: _State, config: RunnableConfig) -> _Update:
    run = _run_of(config)
    attempt = run.attempt
    assert attempt is not None
    if state["outcome"] == "retry":
        return _retry(run, state, state["reasons"])
    assert run.assembled is not None
    output = run.assembled.parse_output(attempt.output_text())
    assert attempt.head is not None
    output = output.model_copy(update={"p1_head": attempt.head})
    output = _settled_markup(run, output)
    text = attempt.body
    if run.settings.spoiler_guard_mode == "shadow" and run.guard.has_spoiler(text):
        # Nothing changes in the turn: the hit is only recorded (names, and the sentence for the
        # review of false hits).
        _note(run, "spoiler", "answer_in_text", "spoiler_shadow")
        _sample(run, text, "shadow")
        log.warning("guard_violation", rule="spoiler", reasons=["answer_in_text"], mode="shadow")
    if attempt.spoiled:
        output = _without_spoiler(output, run.guard.remove_spoiler)
        text = run.guard.remove_spoiler(text)
        for piece in _pieces(text):
            await _emit(run, TokenFrame(text=piece))
    output = await _elements(run, output, text)
    return {"output": output, "text": text, "outcome": "answer"}


def _settled_markup(run: _Run, output: TeacherOutputV2) -> TeacherOutputV2:
    """`p3_markup` comes after its text was shown: code settles it at every turn, nothing goes back
    to the model. Unknown labels are dropped, the slug of a plan step is made and `next_steps` is
    cut; the case is logged. An element with an unknown label stays for the fix call of
    `_elements`."""
    guard = run.guard
    violations = markup_violations(
        output.p3_markup,
        skill_labels=guard.skill_labels,
        misconception_labels=guard.misconception_labels,
        labels=guard.labels,
        next_steps_max=run.settings.plan_next_steps_max,
    )
    for v in violations:
        _note(run, "markup", v.reason, "markup")
    if violations:
        log.warning(
            "guard_violation",
            rule="markup",
            reasons=[v.reason for v in violations],
            details=[v.detail for v in violations],
        )
    markup = drop_unknown_markup_labels(
        output.p3_markup,
        skill_labels=guard.skill_labels,
        misconception_labels=guard.misconception_labels,
        labels=guard.labels,
        next_steps_max=run.settings.plan_next_steps_max,
        keep_elements=True,
    )
    return output.model_copy(update={"p3_markup": markup})


def _retry(run: _Run, state: _State, reasons: list[GuardViolation]) -> _Update:
    """Back to the model with the reasons (D-B-148); a fresh attempt from the context."""
    assert run.assembled is not None
    lines = "; ".join(f"{v.reason}: {v.detail}" for v in reasons)
    note: Mapping[str, Any] = {
        "type": "message",
        "role": "developer",
        "content": [
            {
                "type": "input_text",
                "text": (
                    f"Code rejected the previous attempt ({lines}). Write the whole turn again."
                ),
            }
        ],
    }
    base = run.assembled.request
    previous = state["request"].input_items[len(base.input_items) :]
    notes = tuple(i for i in previous if i.get("role") == "developer")
    request = replace(base, input_items=(*base.input_items, *notes, note))
    return {
        "request": request,
        "retries": state["retries"] + 1,
        "steps": 0,
        "cut_retried": False,
        "outcome": "retry",
    }


def _without_spoiler(output: TeacherOutputV2, remove: Callable[[str], str]) -> TeacherOutputV2:
    blocks = [
        b if b.text is None else b.model_copy(update={"text": remove(b.text)})
        for b in output.p2_body.blocks
    ]
    body = output.p2_body.model_copy(update={"blocks": blocks})
    return output.model_copy(update={"p2_body": body})


async def _elements(run: _Run, output: TeacherOutputV2, text: str) -> TeacherOutputV2:
    """Elements of the body after their markup passed the code check (WP13.4 stage 4).
    Bad task markup, or an unknown label of another element, goes to the fix call, the text stays
    (п.4); still bad after `guard_max_retries` fixes, or no element behind the block — the element
    is removed with its task and its block («every task is marked»)."""
    markup = output.p3_markup
    elements = {e.element_id: e for e in markup.elements}
    fixed_elements: dict[str, Element] = {}
    tasks = {t.element_id: t for t in markup.tasks}
    removed: set[str] = set()
    checked: set[str] = set()
    seen: set[str] = set()
    for block in output.p2_body.blocks:
        if block.element_id is None:
            continue
        element = elements.get(block.element_id)
        if element is None:
            removed.add(block.element_id)
            _note(run, "task_markup", "no_element", "markup")
            log.warning(
                "guard_violation",
                rule="task_markup",
                element_id=block.element_id,
                reasons=["no element"],
            )
            continue
        seen.add(element.element_id)
        if element.kind in TASK_ELEMENT_KINDS:
            checked.add(element.element_id)
            task = await _checked_task(run, element, tasks.get(element.element_id), text)
            if task is None:
                removed.add(element.element_id)
                continue
            tasks[element.element_id] = task
        else:
            fixed = await _checked_element(run, element, text)
            if fixed is None:
                removed.add(element.element_id)
                continue
            fixed_elements[element.element_id] = element = fixed
        await _emit(run, ElementFrame(block_id=block.block_id, element=element))
    # Т-§5.3: a task no task block of the body points at is committed too — it passes the same
    # check (there is no element to fix it for, so a bad one is dropped).
    dropped: set[str] = set()
    for element_id, task in tasks.items():
        if element_id not in checked and (problems := _task_problems(run.guard, task)):
            dropped.add(element_id)
            for problem in problems:
                _note(run, "task_markup", problem, "markup")
            log.warning(
                "guard_violation", rule="task_markup", element_id=element_id, reasons=problems
            )
    # An element no block points at is not shown, so it is not fixed: with an unknown label it goes.
    for element in markup.elements:
        if element.element_id in seen or not (
            bad := unresolved_element_labels(element, run.guard.labels)
        ):
            continue
        removed.add(element.element_id)
        _note(run, "element_markup", "unknown_label", "markup")
        log.warning(
            "guard_violation",
            rule="element_markup",
            element_id=element.element_id,
            reasons=[f"unknown_label: {label}" for label in bad],
        )
    markup = markup.model_copy(
        update={
            "elements": [
                fixed_elements.get(e.element_id, e)
                for e in markup.elements
                if e.element_id not in removed
            ],
            # Review C #109: a task fixed from no markup at all is kept too (insertion order).
            "tasks": [task for eid, task in tasks.items() if eid not in removed | dropped],
        }
    )
    blocks = [b for b in output.p2_body.blocks if b.element_id not in removed]
    body = output.p2_body.model_copy(update={"blocks": blocks})
    return output.model_copy(update={"p2_body": body, "p3_markup": markup})


async def _checked_element(run: _Run, element: Element, text: str) -> Element | None:
    """`element` (not a task) with every label known to the label table, after up to
    `guard_max_retries` fix calls with the unknown labels named; `None` — none passed."""

    def problems_of(e: Element) -> list[str]:
        return [f"unknown_label: {p}" for p in unresolved_element_labels(e, run.guard.labels)]

    problems = problems_of(element)
    for attempt in range(1, run.settings.guard_max_retries + 1):
        if not problems:
            return element
        fixed = await _cancellable(
            run,
            run.ports.fix_markup(
                MarkupFix(element=element, task=None, problems=problems, text=text, attempt=attempt)
            ),
        )
        if fixed is None:
            break
        if (
            isinstance(fixed, TurnTask)
            or fixed.element_id != element.element_id
            or fixed.kind != element.kind
        ):
            problems = ["fix for another element"]
            continue
        element, problems = fixed, problems_of(fixed)
    if not problems:
        return element
    _note(run, "element_markup", "unknown_label", "markup")
    log.warning(
        "guard_violation", rule="element_markup", element_id=element.element_id, reasons=problems
    )
    return None


async def _checked_task(
    run: _Run, element: Element, task: TurnTask | None, text: str
) -> TurnTask | None:
    """The task markup of `element` that passes `validate_markup`, after up to
    `guard_max_retries` fix calls; `None` — none passed."""
    problems = _task_problems(run.guard, task)
    for attempt in range(1, run.settings.guard_max_retries + 1):
        if not problems:
            return task
        fixed = await _cancellable(
            run,
            run.ports.fix_markup(
                MarkupFix(element=element, task=task, problems=problems, text=text, attempt=attempt)
            ),
        )
        if fixed is None:
            break
        if not isinstance(fixed, TurnTask) or fixed.element_id != element.element_id:
            problems = ["fix for another element"]
            continue
        task, problems = fixed, _task_problems(run.guard, fixed)
    if not problems:
        return task
    for problem in problems:
        _note(run, "task_markup", problem, "markup")
    log.warning(
        "guard_violation", rule="task_markup", element_id=element.element_id, reasons=problems
    )
    return None


def _task_problems(guard: GuardInputs, task: TurnTask | None) -> list[str]:
    if task is None:
        return ["no task markup"]
    if task.answer.type == "code":
        # Т-§6.10: a code task needs tests the teacher cannot write; it comes from a
        # `task_generation` request with a language and is shown with a `code_task` element.
        return ["code_task_written_by_teacher"]
    return [
        v.rule
        for v in validate_markup(
            task,
            skill_labels=guard.skill_labels,
            misconception_labels=guard.misconception_labels,
            task_labels=guard.task_labels,
        )
    ]


async def _commit(state: _State, config: RunnableConfig) -> _Update:
    run = _run_of(config)
    assert run.assembled is not None
    if run.cancel.is_set():
        raise TurnCancelled
    turn = TurnToCommit(
        output=state["output"],
        text=state["text"],
        role=run.role,
        model=state["model"],
        effort=run.assembled.effort,
        degraded_mode=run.assembled.degraded_mode,
        guard_retries=state["retries"],
        usage=state["usage"],
        guard_events=tuple(run.events),
        guard_samples=tuple(run.samples),
        timings=run.timer.snapshot(guard_retries=state["retries"]),
    )
    seq = await run.ports.commit(turn)
    await _emit(
        run,
        TurnCommittedFrame(
            seq=seq,
            degraded_mode=run.assembled.degraded_mode,
            effort=run.assembled.effort,
            role=run.role,
        ),
    )
    return {"seq": seq}


def _after_model(state: _State) -> str:
    """A cut step is streamed again by the model node itself."""
    return {"tools": "tools", "cut": "model"}.get(state["outcome"], "guard")


def _after_guard(state: _State) -> str:
    return "model" if state["outcome"] == "retry" else "commit"


def _build() -> CompiledStateGraph[Any, Any, Any, Any]:
    graph = StateGraph(_State)
    graph.add_node("admit", _admit)
    graph.add_node("assemble", _assemble)
    graph.add_node("model", _model)
    graph.add_node("tools", _tools)
    graph.add_node("guard", _guard)
    graph.add_node("commit", _commit)
    graph.add_edge(START, "admit")
    graph.add_edge("admit", "assemble")
    graph.add_edge("assemble", "model")
    graph.add_conditional_edges("model", _after_model, ["tools", "model", "guard"])
    graph.add_edge("tools", "model")
    graph.add_conditional_edges("guard", _after_guard, ["model", "commit"])
    graph.add_edge("commit", END)
    return graph.compile()  # no checkpointer (Т-§22.3)


TURN_GRAPH = _build()


async def run_turn(
    ports: TurnPorts,
    provider: StreamingLLMProvider,
    *,
    settings: Settings,
    cancel: asyncio.Event,
    timer: TurnTimer | None = None,
) -> int | None:
    """Run one teacher turn; the `seq` of `turn.committed`, `None` if a new action cancelled
    it. Errors before the write propagate: there is no turn in the log (Т-§12.1). `timer` — the
    clock of the turn when the caller started it earlier (the wait for the session slot)."""
    run = _Run(ports, provider, settings, cancel, timer=timer or TurnTimer())
    attempts = settings.guard_max_retries + 1
    limit = attempts * (2 * settings.tool_loop_max_steps + 4) + 4
    try:
        state = await TURN_GRAPH.ainvoke(
            {}, config={"configurable": {"run": run}, "recursion_limit": limit}
        )
    except TurnCancelled:
        return None
    return int(state["seq"])


__all__ = [
    "AssembledTurn",
    "GuardInputs",
    "GuardSample",
    "MarkupFix",
    "Role",
    "TurnCancelled",
    "TurnPorts",
    "TurnSlots",
    "TurnToCommit",
    "run_turn",
]
