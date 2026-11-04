"""Teacher turn graph (Т-§12.1, Т-§12.2, Т-§12.4, Т-§11.5; ТЗ Ф4 WP13.2, WP13.4, WP13.5).

LangGraph without checkpoints, in the API process: `admit → assemble → model ⇄ tools →
guard → commit`. What touches the stores is a port of the caller (`TurnPorts`): admission
with the turn lock, context assembly (WP14), tool execution (WP14.4), the write of
`turn.committed` with the manifest (WP2.2, WP13.9) and the SSE frames (§4.7).

- **model** streams one step (`stream_step`, WP10.2). The head is checked by code as soon as
  `p1_head` closes (WP13.5); a violation stops the stream before any body character is shown.
  The body goes through the holdback buffer (`HoldbackBuffer`, WP13.4 stage 3); a spoiler
  stops the stream, `reset {spoiler}` if something was shown. A step with tool calls goes to
  **tools** (`status: tool` per call) and back.
- **guard** routes a rejected attempt back to **model** with its reasons (a developer
  message, D-B-148), up to `guard_max_retries`; after the retries: `code` stance lowered to
  `formative`, an unmarked turn shown and logged, a spoiler sentence removed by code. It
  checks `p3_markup` (labels of exposures and of `targetsMisconception`; `reset {guard}` if
  the body was shown) and the markup of each task element (Т-§5.3): bad markup goes to a
  separate fix call «fix the markup for this text» (`TurnPorts.fix_markup`, WP13.4 п.4,
  D-B-150), the text stays; still bad after `guard_max_retries` fixes — the element is not
  shown and leaves the committed turn.
- **commit** writes through the port unless the turn was cancelled (`TurnCancelled`): a new
  action during the turn cancels it without a write (Т-§12.1, `TurnSlots`).

Not here yet: «generated is checked» (WP12.5).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, TypedDict
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from quack.config import Settings
from quack.events.event_types import DegradedMode
from quack.llm.errors import ProviderIncompleteError
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
    head_violations,
    markup_violations,
    settle_stance,
)
from quack.session.holdback import HoldbackBuffer, SpoilerDetected
from quack.session.teacher_output import TeacherOutputV2, TurnHead, TurnTask

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


@dataclass(frozen=True)
class AssembledTurn:
    """The output of **assemble**: the model request (context rendered, tools, effort), the
    guard inputs, the fast verdict that came while assembling (`verdict` frame)."""

    request: StreamRequest
    guard: GuardInputs
    effort: Effort
    degraded_mode: DegradedMode | None
    verdict: VerdictFrame | None = None


@dataclass(frozen=True)
class TurnToCommit:
    """What **commit** writes: the output as shown (head settled, removed elements gone,
    spoiler sentences removed), the shown body text, who answered and how."""

    output: TeacherOutputV2
    text: str
    role: Role
    model: str
    effort: Effort
    degraded_mode: DegradedMode | None
    guard_retries: int
    usage: StreamUsage


@dataclass(frozen=True)
class MarkupFix:
    """A fix call of one task element (WP13.4 п.4): the element, its markup as the model wrote
    it (`None` if missing), what code found wrong (`validate_markup` rules), the body text as
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

    async def fix_markup(self, fix: MarkupFix) -> TurnTask | None:
        """The model call «fix the markup for this text»; `None` — no fix came."""
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
        self._buffer = HoldbackBuffer(run.settings.spoiler_holdback_chars, run.guard.has_spoiler)
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
        if violations and not self._final:
            raise _HeadRejected(violations)
        if violations:
            log.warning("guard_violation", rule="head", reasons=[v.reason for v in violations])
        self.head = settle_stance(head, guard.answered_verdict)
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
            await self._run.ports.emit(TokenFrame(text=text))

    async def _spoiler(self) -> None:
        if self.shown:
            await self._run.ports.emit(ResetFrame(reason="spoiler"))
        if not self._final:
            raise SpoilerDetected
        # After the retries code removes the sentence (Т-§12.4); the rest is shown at the end.
        self.spoiled = True
        self.shown = False


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


async def _admit(state: _State, config: RunnableConfig) -> _Update:
    run = _run_of(config)
    run.role = await run.ports.admit()
    await run.ports.emit(StatusFrame(state="thinking"))
    return {}


async def _assemble(state: _State, config: RunnableConfig) -> _Update:
    run = _run_of(config)
    run.assembled = await run.ports.assemble(run.role)
    if run.assembled.verdict is not None:
        await run.ports.emit(run.assembled.verdict)
    return {
        "request": run.assembled.request,
        "retries": 0,
        "steps": 0,
        "cut_retried": False,
        "usage": StreamUsage(),
    }


async def _model(state: _State, config: RunnableConfig) -> _Update:
    run = _run_of(config)
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
            if attempt.shown:
                await run.ports.emit(ResetFrame(reason="guard"))
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
    if steps >= run.settings.tool_loop_max_steps:
        raise ToolLoopLimitError(f"no answer after {steps} model steps")
    calls = tool_calls(state["step"].items)
    for call in calls:
        await run.ports.emit(StatusFrame(state="tool", tool=call.name))
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
    final = state["retries"] >= run.settings.guard_max_retries
    if state["outcome"] == "retry":
        return _retry(run, state, state["reasons"])
    output = TeacherOutputV2.model_validate_json(attempt.output_text())
    assert attempt.head is not None
    output = output.model_copy(update={"p1_head": attempt.head})
    guard = run.guard
    violations = markup_violations(
        output.p3_markup,
        skill_labels=guard.skill_labels,
        misconception_labels=guard.misconception_labels,
    )
    if violations and not final:
        if attempt.shown:
            await run.ports.emit(ResetFrame(reason="guard"))
        return _retry(run, state, violations)
    if violations:
        log.warning("guard_violation", rule="markup", reasons=[v.reason for v in violations])
    text = attempt.body
    if attempt.spoiled:
        output = _without_spoiler(output, guard.remove_spoiler)
        text = guard.remove_spoiler(text)
        await run.ports.emit(TokenFrame(text=text))
    output = await _elements(run, output, text)
    return {"output": output, "text": text, "outcome": "answer"}


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
    Bad task markup goes to the fix call, the text stays (п.4); still bad after
    `guard_max_retries` fixes, or no element behind the block — the element is removed with
    its task and its block («every task is marked»)."""
    markup = output.p3_markup
    elements = {e.element_id: e for e in markup.elements}
    tasks = {t.element_id: t for t in markup.tasks}
    removed: set[str] = set()
    for block in output.p2_body.blocks:
        if block.element_id is None:
            continue
        element = elements.get(block.element_id)
        if element is None:
            removed.add(block.element_id)
            log.warning(
                "guard_violation",
                rule="task_markup",
                element_id=block.element_id,
                reasons=["no element"],
            )
            continue
        if element.kind in TASK_ELEMENT_KINDS:
            task = await _checked_task(run, element, tasks.get(element.element_id), text)
            if task is None:
                removed.add(element.element_id)
                continue
            tasks[element.element_id] = task
        await run.ports.emit(ElementFrame(block_id=block.block_id, element=element))
    markup = markup.model_copy(
        update={
            "elements": [e for e in markup.elements if e.element_id not in removed],
            # Review C #109: a task fixed from no markup at all is kept too (insertion order).
            "tasks": [task for eid, task in tasks.items() if eid not in removed],
        }
    )
    blocks = [b for b in output.p2_body.blocks if b.element_id not in removed]
    body = output.p2_body.model_copy(update={"blocks": blocks})
    return output.model_copy(update={"p2_body": body, "p3_markup": markup})


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
        if fixed.element_id != element.element_id:
            problems = ["fix for another element"]
            continue
        task, problems = fixed, _task_problems(run.guard, fixed)
    if not problems:
        return task
    log.warning(
        "guard_violation", rule="task_markup", element_id=element.element_id, reasons=problems
    )
    return None


def _task_problems(guard: GuardInputs, task: TurnTask | None) -> list[str]:
    if task is None:
        return ["no task markup"]
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
    )
    seq = await run.ports.commit(turn)
    await run.ports.emit(
        TurnCommittedFrame(
            seq=seq,
            degraded_mode=run.assembled.degraded_mode,
            effort=run.assembled.effort,
            role=run.role,
        )
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
) -> int | None:
    """Run one teacher turn; the `seq` of `turn.committed`, `None` if a new action cancelled
    it. Errors before the write propagate: there is no turn in the log (Т-§12.1)."""
    run = _Run(ports, provider, settings, cancel)
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
    "MarkupFix",
    "Role",
    "TurnCancelled",
    "TurnPorts",
    "TurnSlots",
    "TurnToCommit",
    "run_turn",
]
