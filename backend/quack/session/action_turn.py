"""The teacher turn of `POST /v1/sessions/{id}/actions` (ТЗ Ф4 §4.7, WP13.1–13.2; D-B-156).

`ApiTurnPorts` implements the ports of the turn graph for the API process:

- **admit**: the turn lock of the session — Redis `SET NX` with a TTL (taken by another
  process → `TurnLockedError`, `TURN_LOCKED`); in this process `TurnSlots` already gives one
  turn per session and cancels the old one (D-B-148 п.е). The role: while the teacher model has
  a pressure flag the turn waits for it, at most `teacher_admission_wait_ms`; a longer flag
  sends the turn to `teacher_fallback` (Т-§11.3, WP5.3 of Phase 3).
- **assemble**: the context is the port `TurnContextSource` (WP14): the model request, the
  guard inputs, the label table, the manifest row, the tools.
- **commit**: `turn_batch` + `commit_turn` (WP2.2, WP13.9).
- **emit**: the frames go to the SSE stream of the request.

`turn_frames` gives the SSE frames in the order of §4.7: `action_recorded` first, then the
frames of the graph; an error before the write is an `error` frame and no turn (`Т-§12.1`).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from quack.config import Settings
from quack.db.db import Db
from quack.db.redis import turn_lock_key
from quack.llm.errors import (
    LLMError,
    ProviderBadRequestError,
    ProviderIncompleteError,
    ProviderUnavailableError,
)
from quack.llm.labels import LabelMap
from quack.llm.pressure import pressure_ttl_s
from quack.llm.streaming import StreamEvent, StreamingLLMProvider, StreamRequest
from quack.llm.tool_loop import ToolLoopLimitError
from quack.logging import get_logger
from quack.session.actions import (
    FRAME_EVENTS,
    Action,
    ActionRecordedFrame,
    ErrorFrame,
    VerdictFrame,
)
from quack.session.record import RecordedAction
from quack.session.teacher_output import TurnTask
from quack.session.turn_commit import (
    AnsweredAction,
    LearnerText,
    TurnManifestRow,
    commit_turn,
    turn_batch,
)
from quack.session.turn_graph import (
    AssembledTurn,
    Frame,
    MarkupFix,
    Role,
    TurnSlots,
    TurnToCommit,
    run_turn,
)

log = get_logger(__name__)

_RENEW_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""

_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


_PROVIDER_ERRORS = (ProviderUnavailableError, ProviderBadRequestError, ProviderIncompleteError)


class TurnLockedError(Exception):
    """Another process holds the turn of this session: `TURN_LOCKED`."""


@dataclass(frozen=True)
class TurnRequest:
    """What the context source needs: whose turn, on which action, which role answers."""

    learner_id: UUID
    session_id: UUID
    domain_id: UUID
    action_seq: int
    action: Action
    role: Role
    manifest_id: UUID


@dataclass(frozen=True)
class TurnSetup:
    """The output of the context source (WP14): the model request and guard inputs, the
    streaming provider of the role (`LLMGateway.streaming`), the label table and the manifest
    row of the call, the memory tools, what the commit needs to check a deadline and to close
    or help a task."""

    assembled: AssembledTurn
    provider: StreamingLLMProvider
    labels: LabelMap
    manifest: TurnManifestRow
    execute_tool: Callable[[str, str], Awaitable[str]]
    learner_texts: Sequence[LearnerText] = ()
    learner_timezone: str | None = None
    answered: AnsweredAction | None = None


class TurnContextSource(Protocol):
    async def assemble(self, request: TurnRequest) -> TurnSetup: ...


class ApiTurnPorts:
    def __init__(
        self,
        *,
        db: Db,
        redis: Redis | None,
        settings: Settings,
        source: TurnContextSource,
        learner_id: UUID,
        session_id: UUID,
        domain_id: UUID,
        recorded: RecordedAction,
        action: Action,
        frames: asyncio.Queue[Frame],
    ) -> None:
        self._db = db
        self._redis = redis
        self._settings = settings
        self._source = source
        self._learner_id = learner_id
        self._session_id = session_id
        self._domain_id = domain_id
        self._recorded = recorded
        self._action = action
        self._frames = frames
        self._lock_token: str | None = None
        self._renewal: asyncio.Task[None] | None = None
        self.setup: TurnSetup | None = None

    async def admit(self) -> Role:
        await self._lock()
        model = self._settings.model_for_role("teacher")
        left = await pressure_ttl_s(self._redis, model)
        if left is None:
            return "teacher"
        if left * 1000 > self._settings.teacher_admission_wait_ms:
            return "teacher_fallback"
        await asyncio.sleep(left)
        return "teacher"

    async def assemble(self, role: Role) -> AssembledTurn:
        self.setup = await self._source.assemble(
            TurnRequest(
                learner_id=self._learner_id,
                session_id=self._session_id,
                domain_id=self._domain_id,
                action_seq=self._recorded.seq,
                action=self._action,
                role=role,
                manifest_id=uuid4(),
            )
        )
        return self.setup.assembled

    async def execute_tool(self, name: str, arguments: str) -> str:
        return await self._ready().execute_tool(name, arguments)

    async def commit(self, turn: TurnToCommit) -> int:
        setup = self._ready()
        manifest = replace(
            setup.manifest,
            role=turn.role,
            model=turn.model,
            effort=turn.effort,
            degraded_mode=turn.degraded_mode,
        )
        batch = turn_batch(
            turn,
            setup.labels,
            manifest=manifest,
            action_seq=self._recorded.seq,
            domain_id=self._domain_id,
            settings=self._settings,
            answered=setup.answered,
            learner_texts=setup.learner_texts,
            learner_timezone=setup.learner_timezone,
        )
        return await commit_turn(self._db, batch, learner_id=self._learner_id, redis=self._redis)

    async def emit(self, frame: Frame) -> None:
        await self._frames.put(frame)

    async def fix_markup(self, fix: MarkupFix) -> TurnTask | None:
        """D-B-156: the model call «fix the markup for this text» has no instruction in
        `teacher@2` yet; no fix — the element is removed after the check (WP13.5)."""
        return None

    async def release(self) -> None:
        """Free the turn lock if this turn holds it (compare-and-delete)."""
        if self._renewal is not None:
            self._renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renewal
            self._renewal = None
        if self._redis is None or self._lock_token is None:
            return
        with contextlib.suppress(RedisError, OSError):
            await self._redis.eval(  # type: ignore[misc]
                _RELEASE_LUA, 1, turn_lock_key(self._session_id), self._lock_token
            )
        self._lock_token = None

    async def _lock(self) -> None:
        if self._redis is None:
            return  # without Redis the in-process `TurnSlots` is the lock (Т-§20.7)
        token = str(uuid4())
        try:
            taken = await self._redis.set(
                turn_lock_key(self._session_id), token, nx=True, ex=self._settings.turn_lock_ttl_sec
            )
        except (RedisError, OSError) as exc:
            log.warning(
                "turn_lock_redis_degraded", session_id=str(self._session_id), error=repr(exc)
            )
            return
        if not taken:
            raise TurnLockedError(f"turn of session {self._session_id} is held elsewhere")
        self._lock_token = token
        self._renewal = asyncio.ensure_future(self._renew(self._redis, token))

    async def _renew(self, redis: Redis, token: str) -> None:
        """Keep the lock while the turn runs: a turn longer than the TTL must not let a second
        process start a parallel turn of the session. A Redis error is skipped, the next
        round tries again."""
        settings = self._settings
        while True:
            await asyncio.sleep(settings.turn_lock_renew_sec)
            with contextlib.suppress(RedisError, OSError):
                await redis.eval(  # type: ignore[misc]
                    _RENEW_LUA,
                    1,
                    turn_lock_key(self._session_id),
                    token,
                    settings.turn_lock_ttl_sec,
                )

    def _ready(self) -> TurnSetup:
        if self.setup is None:
            raise RuntimeError("the turn has no context yet")
        return self.setup


class _SetupProvider:
    """The graph gets its provider before `assemble`; the role's provider comes with it."""

    def __init__(self, ports: ApiTurnPorts) -> None:
        self._ports = ports

    def stream_structured(self, request: StreamRequest) -> AsyncIterator[StreamEvent]:
        return self._ports._ready().provider.stream_structured(request)  # pyright: ignore[reportPrivateUsage]


def error_frame(exc: BaseException) -> ErrorFrame:
    """§4.7 `error {code, message, retryable}`: codes of §4.5 / Phase 3."""
    if isinstance(exc, TurnLockedError):
        return ErrorFrame(code="TURN_LOCKED", message=str(exc), retryable=True)
    if isinstance(exc, (LLMError, ToolLoopLimitError, *_PROVIDER_ERRORS)):
        # The model did not answer (the gateway's errors, or the provider's when it streams
        # without the gateway): the turn can be asked again (Т-§12.1).
        return ErrorFrame(code="LLM_DEGRADED", message=str(exc), retryable=True)
    return ErrorFrame(code="SERVICE_UNAVAILABLE", message="the turn failed", retryable=True)


def recorded_frame(recorded: RecordedAction) -> ActionRecordedFrame:
    verdict = recorded.verdict
    return ActionRecordedFrame(
        seq=recorded.seq,
        verdict=None
        if verdict is None
        else VerdictFrame(
            answer_seq=verdict.answer_seq,
            part=verdict.part,
            outcome=verdict.outcome,
            method=verdict.method,
            verification=verdict.verification,
        ),
    )


async def turn_frames(
    *,
    db: Db,
    redis: Redis | None,
    settings: Settings,
    source: TurnContextSource,
    slots: TurnSlots,
    learner_id: UUID,
    session_id: UUID,
    domain_id: UUID,
    recorded: RecordedAction,
    action: Action,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """SSE frames of one action with a teacher turn, in the order of §4.7."""
    yield "action_recorded", recorded_frame(recorded).model_dump(mode="json")
    frames: asyncio.Queue[Frame] = asyncio.Queue()
    ports = ApiTurnPorts(
        db=db,
        redis=redis,
        settings=settings,
        source=source,
        learner_id=learner_id,
        session_id=session_id,
        domain_id=domain_id,
        recorded=recorded,
        action=action,
        frames=frames,
    )

    async def turn() -> int | None:
        async with slots.claim(session_id) as cancel:
            try:
                return await run_turn(
                    ports, _SetupProvider(ports), settings=settings, cancel=cancel
                )
            finally:
                await ports.release()

    task = asyncio.ensure_future(turn())
    try:
        while True:
            next_frame = asyncio.ensure_future(frames.get())
            await asyncio.wait({next_frame, task}, return_when=asyncio.FIRST_COMPLETED)
            if next_frame.done():
                frame = next_frame.result()
                yield FRAME_EVENTS[type(frame)], frame.model_dump(mode="json")
                continue
            next_frame.cancel()
            while not frames.empty():
                frame = frames.get_nowait()
                yield FRAME_EVENTS[type(frame)], frame.model_dump(mode="json")
            if (exc := task.exception()) is not None:
                log.warning("turn_failed", session_id=str(session_id), error=repr(exc))
                yield "error", error_frame(exc).model_dump(mode="json")
            return
    finally:
        if not task.done():  # the client went away: no turn in the log (Т-§12.1)
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


__all__ = [
    "ApiTurnPorts",
    "TurnContextSource",
    "TurnLockedError",
    "TurnRequest",
    "TurnSetup",
    "error_frame",
    "recorded_frame",
    "turn_frames",
]
