"""The WebSocket of a canvas, `WS /v1/canvases/{id}/ws` (ТЗ Ф4 WP21.2; Т-§1.6, Т-§15.1, Т-§15.2).

Sign-in is the session cookie of the handshake: a `full` session of the canvas owner, or a
`companion` session paired to this canvas, which only receives the status (WP22.1): of the
server messages only `COMPANION_KINDS`, and any message it sends closes the socket with 1008
before it is read (D-C-124). Without a session the socket is refused with 1008; a malformed
client message closes it with 1007 (D-C-121).

`stroke_batch` goes to the stroke journal (`quack.canvas.strokes`): `ack {batch_no}` only after
the chunk with the batch is written; a repeated `batch_no` writes nothing and, once written, is
acknowledged again; with the store down nothing is acknowledged and the client repeats
(Т-§20.7). Pending batches are written on close. The messages of the server to every device of
the canvas, in any API process, go through Redis pub/sub `canvas:{id}` (`publish_canvas`);
without Redis a socket gets only its own acks (§4.2).

`line_ready` is recognized in the background (`quack.canvas.recognition`, WP21.3): the socket
keeps acknowledging batches while Spark reads the line; the result is `canvas.line_committed`
and `line_recognized` to every device of the canvas. `typed_formula` goes to the log as LaTeX;
erasing every stroke of a recognized line writes `canvas.line_erased`.

On a canvas opened by a task every new line goes through the step check (`quack.canvas.
step_check`, WP21.4) and the marks it earns through the gate of interventions (`quack.canvas.
interventions`, WP21.5): server messages `mark {mark_id, line_id, kind}` (`kind: null` - taken
off quietly) and `hint {step, line_id, text}`. `check_request` checks every line and shows
every mark at once, bypassing the grace, the limits and the quiet mode (D-C-159).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import orjson
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from quack.api.auth import CurrentSession
from quack.api.resources import AppResources
from quack.canvas.boards import Canvas
from quack.canvas.interventions import (
    Action,
    InterventionGate,
    canvas_quiet,
    clear_mark,
    mark_id,
    show_first_hint,
    show_mark,
    task_finished,
)
from quack.canvas.recognition import (
    LineBook,
    commit_typed,
    erase_line,
    line_recognized,
    recognize_line,
)
from quack.canvas.step_check import (
    StepEnv,
    canvas_lines,
    check_step,
    second_opinion,
    solution_space_of,
)
from quack.canvas.strokes import (
    Erasure,
    Point,
    Stroke,
    StrokeBatch,
    StrokeJournal,
    chunk_max_batches,
)
from quack.events.event_types import CanvasLineCommitted
from quack.graph.read import GraphReader, create_graph_reader
from quack.logging import get_logger

log = get_logger(__name__)

# RFC 6455 §7.4.1: 1008 — policy violation (no session, a foreign canvas, a companion sending),
# 1007 — data inconsistent with the type of the message.
CLOSE_POLICY = 1008
CLOSE_INVALID = 1007

# WP22.1: the companion sees the status of its canvas "and nothing more". Of the server messages
# of WP21.2 only `task_closed` is a status; recognitions, marks, hints and layers are the
# content of the solution (D-C-124).
COMPANION_KINDS = frozenset({"task_closed"})

# The clock of the interventions (seconds); a test replaces it.
clock = time.monotonic


def memory_reader(resources: AppResources, learner_id: UUID) -> GraphReader:
    """The memory read of the step check: the diagnoser's known misconceptions."""
    return create_graph_reader(
        resources.neo4j, resources.settings, learner_id=learner_id, db=resources.db
    )


def canvas_channel(canvas_id: UUID) -> str:
    """Т-§15.2: one channel per canvas."""
    return f"canvas:{canvas_id}"


async def publish_canvas(redis: Redis, canvas_id: UUID, message: dict[str, Any]) -> None:
    """A server message (`line_recognized`, `mark`, `hint`, `layer_update`, `task_closed`) to
    every socket of the canvas in every API process."""
    await redis.publish(canvas_channel(canvas_id), orjson.dumps(message))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrokeIn(_Strict):
    stroke_id: str
    # Absolute `(x, y, t_ms, pressure)`; the journal stores them as deltas (Т-§15.1).
    points: list[tuple[float, float, int, float]]


class ErasureIn(_Strict):
    stroke_ids: list[str]
    at_ms: int


class StrokeBatchIn(_Strict):
    type: Literal["stroke_batch"]
    batch_no: int = Field(ge=0)
    strokes: list[StrokeIn] = Field(default_factory=list[StrokeIn])
    erasures: list[ErasureIn] = Field(default_factory=list[ErasureIn])

    def batch(self) -> StrokeBatch:
        return StrokeBatch(
            batch_no=self.batch_no,
            strokes=tuple(
                Stroke(s.stroke_id, tuple(Point(*p) for p in s.points)) for s in self.strokes
            ),
            erasures=tuple(Erasure(tuple(e.stroke_ids), e.at_ms) for e in self.erasures),
        )


class LineReadyIn(_Strict):
    type: Literal["line_ready"]
    line_id: str
    stroke_ids: list[str]
    bbox: tuple[float, float, float, float]
    started_at: int
    ended_at: int


class TypedFormulaIn(_Strict):
    type: Literal["typed_formula"]
    block_id: str
    latex: str


class CheckRequestIn(_Strict):
    type: Literal["check_request"]


type ClientMessage = Annotated[
    StrokeBatchIn | LineReadyIn | TypedFormulaIn | CheckRequestIn, Field(discriminator="type")
]
_CLIENT_MESSAGE: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def may_open(session: CurrentSession | None, canvas_id: UUID) -> bool:
    """A session may open the socket of the canvas: `full`, or `companion` of this canvas."""
    if session is None:
        return False
    return session.scope == "full" or session.canvas_id == canvas_id


async def serve_canvas(
    websocket: WebSocket, resources: AppResources, session: CurrentSession, canvas: Canvas
) -> None:
    """The accepted socket until the client leaves."""
    assert canvas.session_id is not None
    journal = StrokeJournal(
        resources.storage,
        learner_id=session.learner_id,
        session_id=canvas.session_id,
        canvas_id=canvas.canvas_id,
        chunk_seconds=resources.settings.stroke_chunk_seconds,
        max_batches=chunk_max_batches(resources.settings),
    )
    sending = asyncio.Lock()

    async def send(message: dict[str, Any]) -> None:
        async with sending:
            await websocket.send_json(message)

    async def acknowledge(batch_nos: list[int]) -> None:
        for batch_no in batch_nos:
            await send({"type": "ack", "batch_no": batch_no})

    async def deliver(message: dict[str, Any]) -> None:
        """A server message to every device of the canvas; without Redis — to this socket."""
        if resources.redis is not None:
            try:
                await publish_canvas(resources.redis, canvas.canvas_id, message)
                return
            except RedisError as exc:
                log.warning("canvas_publish_failed", error=str(exc))
        await send(message)

    book = LineBook()
    pending: set[asyncio.Task[None]] = set()
    where = {
        "learner_id": session.learner_id,
        "session_id": canvas.session_id,
        "canvas_id": canvas.canvas_id,
        "redis": resources.redis,
    }

    async def recognize(line: LineReadyIn) -> None:
        strokes = book.line(
            line.line_id, line.stroke_ids, line.bbox, line.started_at, line.ended_at
        )
        committed = await recognize_line(
            resources.db, resources.llm, resources.compute, line=strokes, **where
        )
        if committed is not None:
            book.recognized(committed)
        await deliver(line_recognized(line.line_id, committed))
        if committed is not None:
            committed_line(committed)

    env = StepEnv(
        db=resources.db,
        llm=resources.llm,
        compute=resources.compute,
        settings=resources.settings,
        learner_id=session.learner_id,
        session_id=canvas.session_id,
        canvas_id=canvas.canvas_id,
        task_id=canvas.task_id,
        reader=memory_reader(resources, session.learner_id) if canvas.task_id else None,
        redis=resources.redis,
    )
    checks = canvas.task_id is not None and session.scope == "full"
    gate = InterventionGate(
        resources.settings,
        shown_before=len(canvas.doc.teacher.marks) + len(canvas.doc.teacher.hints),
    )
    wake = asyncio.Event()

    async def perform(action: Action) -> None:
        """An action of the gate on the board and to every device."""
        if action.kind == "mark" and action.line_id and action.mark:
            await show_mark(
                resources.db,
                learner_id=session.learner_id,
                canvas_id=canvas.canvas_id,
                line_id=action.line_id,
                kind=action.mark,
            )
            await deliver(_mark_message(action.line_id, action.mark))
        elif action.kind == "clear" and action.line_id:
            await clear_mark(
                resources.db,
                learner_id=session.learner_id,
                canvas_id=canvas.canvas_id,
                line_id=action.line_id,
            )
            await deliver(_mark_message(action.line_id, None))
        elif action.kind == "hint" and canvas.task_id is not None:
            text = await show_first_hint(
                resources.db,
                learner_id=session.learner_id,
                session_id=canvas.session_id,
                canvas_id=canvas.canvas_id,
                task_id=canvas.task_id,
                space=await solution_space_of(env),
                line_id=action.line_id,
            )
            if text is not None:
                await deliver({"type": "hint", "step": 1, "line_id": action.line_id, "text": text})

    async def analyze(line: CanvasLineCommitted) -> None:
        """The cascade for a committed line, its second opinion, then the gate (WP21.4-21.5)."""
        lines = await canvas_lines(
            resources.db, learner_id=session.learner_id, canvas_id=canvas.canvas_id
        )
        ids = [item.line_id for item in lines]
        if line.line_id not in ids:
            return  # erased meanwhile
        earlier = lines[: ids.index(line.line_id)]
        outcome = await check_step(env, line, earlier)
        if outcome.mark is None:
            return
        gate.propose(line.line_id, outcome.mark, clock())
        wake.set()
        if outcome.mark != "question":
            return
        agreed = await second_opinion(env, line, outcome, earlier)
        for action in gate.second_opinion(line.line_id, agreed):
            await perform(action)
        wake.set()

    def start(coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        pending.add(task)
        task.add_done_callback(pending.discard)

    def committed_line(line: CanvasLineCommitted) -> None:
        """A line is in the log: waiting marks may fall due, and the line is checked."""
        if not checks:
            return
        gate.line_written(line.line_id, clock())
        wake.set()
        start(analyze(line))

    async def check_all() -> None:
        """The check request: every line is checked and every mark shown (Т-§15.5)."""
        lines = await canvas_lines(
            resources.db, learner_id=session.learner_id, canvas_id=canvas.canvas_id
        )
        for index, line in enumerate(lines):
            outcome = await check_step(env, line, lines[:index])
            mark = outcome.mark
            if mark == "question":
                agreed = await second_opinion(env, line, outcome, lines[:index])
                mark = "error" if agreed else None if agreed is False else "question"
            shown = gate.prompted(line.line_id, mark) if mark else None
            if shown is not None:
                await perform(shown)

    async def ticker() -> None:
        """Marks and hints fall due by the clock, not by the next message of the client."""
        while True:
            delay = gate.next_due_in(clock())
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), delay)
            wake.clear()
            gate.quiet = await canvas_quiet(resources.db, learner_id=session.learner_id)
            if canvas.task_id is not None:
                gate.finished = await task_finished(
                    resources.db, learner_id=session.learner_id, task_id=canvas.task_id
                )
                space = await solution_space_of(env)
                gate.can_hint = space is not None and bool(space.hints)
            for action in gate.due(clock()):
                await perform(action)

    ticking = asyncio.create_task(ticker()) if checks else None
    relay = (
        asyncio.create_task(
            _relay(
                resources.redis,
                canvas.canvas_id,
                send,
                COMPANION_KINDS if session.scope == "companion" else None,
            )
        )
        if resources.redis is not None
        else None
    )
    try:
        while True:
            timeout = journal.due_in(now=datetime.now(UTC))
            try:
                text = await asyncio.wait_for(websocket.receive_text(), timeout)
            except TimeoutError:
                await acknowledge(await journal.flush_due(now=datetime.now(UTC)))
                continue
            if session.scope == "companion":
                await websocket.close(code=CLOSE_POLICY)  # the companion only gets the status
                return
            try:
                message = _CLIENT_MESSAGE.validate_json(text)
            except ValidationError:
                await websocket.close(code=CLOSE_INVALID)
                return
            if isinstance(message, StrokeBatchIn):
                if journal.written(message.batch_no):
                    await acknowledge([message.batch_no])
                else:
                    batch = message.batch()
                    journal.add(batch, now=datetime.now(UTC))
                    await acknowledge(await journal.flush_due(now=datetime.now(UTC)))
                    gone = book.add(batch)
                    for line_id, line_text in gone:
                        await erase_line(resources.db, line_id=line_id, text=line_text, **where)
                    if checks:
                        _watch_strokes(gate, book, batch)
                        for line_id, _ in gone:
                            for action in gate.line_erased(line_id):
                                await perform(action)
                        wake.set()
            elif isinstance(message, LineReadyIn):
                task = asyncio.create_task(recognize(message))
                pending.add(task)
                task.add_done_callback(pending.discard)
            elif isinstance(message, TypedFormulaIn):
                typed = await commit_typed(
                    resources.db,
                    block_id=message.block_id,
                    latex=message.latex,
                    at_ms=int(datetime.now(UTC).timestamp() * 1000),
                    **where,
                )
                committed_line(typed)
            elif checks:  # check_request
                start(check_all())
    except WebSocketDisconnect:
        pass
    finally:
        if pending:  # lines already sent are still recognized and written
            await asyncio.gather(*pending, return_exceptions=True)
        if ticking is not None:
            ticking.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticking
        if relay is not None:
            relay.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await relay
        await journal.flush()  # the client is gone: written, but nobody to acknowledge


def _mark_message(line_id: str, kind: str | None) -> dict[str, Any]:
    """The server message `mark`; `kind: null` takes the mark off (WP21.5)."""
    return {"type": "mark", "mark_id": mark_id(line_id), "line_id": line_id, "kind": kind}


def _watch_strokes(gate: InterventionGate, book: LineBook, batch: StrokeBatch) -> None:
    """What the strokes of a batch tell the gate: the learner writes, or erases a place."""
    now = clock()
    if batch.strokes:
        gate.strokes(now)
    for box in book.erasure_boxes(batch):
        gate.erased_place(box, now)


async def _relay(redis: Redis, canvas_id: UUID, send: Any, kinds: frozenset[str] | None) -> None:
    """Messages of `canvas:{id}` to this socket (only `kinds` when given); a Redis failure ends
    the relay, not the socket."""
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(canvas_channel(canvas_id))
        async for message in pubsub.listen():  # ends by cancellation when the socket closes
            if message["type"] != "message":
                continue
            payload = orjson.loads(message["data"])
            if kinds is None or payload.get("type") in kinds:
                await send(payload)
    except RedisError as exc:
        log.warning("canvas_relay_failed", canvas_id=str(canvas_id), error=str(exc))
    finally:
        with contextlib.suppress(RedisError):
            await asyncio.shield(pubsub.aclose())
