"""The journal part of `POST /v1/sessions/{id}/actions` (Т-§2.2, Т-§2.3; ТЗ Ф4 WP13.1): every
action kind writes its event (`ACTION_EVENT_TYPES`) under the client's `idempotency_key`; an
answer goes with its instant verdict in one transaction (WP11.2); the kinds of
`TURN_ACTION_KINDS` then start a teacher turn (WP13.2)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis

from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import (
    AnswerSubmitted,
    CheckSkipped,
    ConfidenceReported,
    ElementResponded,
    HintRequested,
    ItemFlaggedUnclear,
    MessageSent,
    SolutionRequested,
    TurnCommitted,
)
from quack.events.log import NewEvent, append_events
from quack.events.registry import EventPayload
from quack.practice.answers import part_spec, record_answer
from quack.practice.check import Verdict
from quack.practice.repo import event_by_seq
from quack.practice.task_store import TaskDocument, get_task
from quack.session.actions import (
    ACTION_EVENT_TYPES,
    TURN_ACTION_KINDS,
    Action,
    AnswerAction,
    CheckSkipAction,
    ConfidenceAction,
    ElementResponseAction,
    FlagUnclearAction,
    HintRequestAction,
    IntentAction,
    MessageAction,
    SolutionRequestAction,
)


@dataclass(frozen=True)
class RecordedAction:
    """`seq` — the action's event (`action_recorded` frame, §4.7); `verdict` — an instant
    verdict written with an answer; `starts_turn` — the kind has a teacher turn."""

    seq: int
    starts_turn: bool
    verdict: Verdict | None = None


async def record_action(
    db: Db,
    action: Action,
    *,
    learner_id: UUID,
    session_id: UUID,
    domain_id: UUID | None,
    settings: Settings,
    redis: Redis | None = None,
) -> RecordedAction:
    """Write the action's event(s); `LookupError` — the task or the turn element it names
    is not the learner's."""
    starts_turn = action.kind in TURN_ACTION_KINDS
    key = str(action.idempotency_key)
    if isinstance(action, AnswerAction):
        answer = AnswerSubmitted.model_validate(
            action.model_dump(exclude={"kind", "idempotency_key"})
        )
        return await _answer(db, answer, key, learner_id, session_id, domain_id, settings, redis)
    if isinstance(action, HintRequestAction | SolutionRequestAction | CheckSkipAction):
        await _own_task(db, learner_id, action.task_id)
    if isinstance(action, MessageAction) and action.voice_seq is not None:
        await _own_voice(db, learner_id, session_id, action.voice_seq)
    async with db.transaction():
        [event] = await append_events(
            db,
            learner_id,
            [
                NewEvent(
                    type=ACTION_EVENT_TYPES[action.kind],
                    payload=_payload(action),
                    actor="learner",
                    idempotency_key=key,
                    domain_id=domain_id,
                    session_id=session_id,
                )
            ],
            redis=redis,
        )
        if not (isinstance(action, ElementResponseAction) and action.answer is not None):
            return RecordedAction(event.seq, starts_turn)
        # WP13.1: an element that is a task also writes `answer.submitted`.
        task_id = await _element_task(db, learner_id, action.turn_seq, action.element_id)
        answer = AnswerSubmitted(
            task_id=task_id,
            answer=action.answer,
            text=action.text,
            confidence=action.confidence,
            element_id=action.element_id,
            source="chat",
        )
        recorded = await _answer(
            db,
            answer,
            f"{key}:answer",
            learner_id,
            session_id,
            domain_id,
            settings,
            redis,
            causation_seq=event.seq,
        )
    return RecordedAction(event.seq, starts_turn, recorded.verdict)


def _payload(action: Action) -> EventPayload:
    match action:
        case MessageAction():
            return MessageSent.model_validate(
                action.model_dump(mode="json", exclude={"kind", "idempotency_key"})
            )
        case IntentAction():
            # Т-§12.7: «an event of the action with the intent and the topic»; no words.
            return MessageSent(
                text="", intent=action.intent, topic_id=action.topic_id, hub_id=action.hub_id
            )
        case ElementResponseAction():
            return ElementResponded(
                turn_seq=action.turn_seq,
                element_id=action.element_id,
                response=action.response,
                text=action.text,
                confidence=action.confidence,
            )
        case HintRequestAction():
            return HintRequested(task_id=action.task_id)
        case SolutionRequestAction():
            return SolutionRequested(task_id=action.task_id)
        case CheckSkipAction():
            return CheckSkipped(task_id=action.task_id)
        case FlagUnclearAction():
            return ItemFlaggedUnclear(
                turn_seq=action.turn_seq, block_id=action.block_id, text=action.quote
            )
        case ConfidenceAction():
            return ConfidenceReported(target_seq=action.target_seq, value=action.value)
        case AnswerAction():
            raise AssertionError("answers go through record_answer")


async def _answer(
    db: Db,
    answer: AnswerSubmitted,
    key: str,
    learner_id: UUID,
    session_id: UUID,
    domain_id: UUID | None,
    settings: Settings,
    redis: Redis | None,
    causation_seq: int | None = None,
) -> RecordedAction:
    task = await _own_task(db, learner_id, answer.task_id)
    # Т-§6.2: a part of a task is checked by the spec of that part.
    spec = await part_spec(db, learner_id=learner_id, task=task, part=answer.part)
    recorded = await record_answer(
        db,
        learner_id=learner_id,
        session_id=session_id,
        domain_id=domain_id,
        idempotency_key=key,
        payload=answer,
        spec=spec,
        settings=settings,
        redis=redis,
        causation_seq=causation_seq,
    )
    return RecordedAction(recorded.answer_seq, True, recorded.verdict)


async def _own_task(db: Db, learner_id: UUID, task_id: UUID) -> TaskDocument:
    """The learner's task; `LookupError` for a task of another learner or none (D-B-147)."""
    task = await get_task(db, learner_id=learner_id, task_id=task_id)
    if task is None:
        raise LookupError(f"no task {task_id}")
    return task


async def _own_voice(db: Db, learner_id: UUID, session_id: UUID, seq: int) -> None:
    """The `voice.transcribed` of this session that a spoken message points at;
    `LookupError` otherwise."""
    event = await event_by_seq(db, learner_id=learner_id, seq=seq)
    if event is None or event.type != "voice.transcribed" or event.session_id != session_id:
        raise LookupError(f"no voice segment at seq {seq} in session {session_id}")


async def _element_task(db: Db, learner_id: UUID, turn_seq: int, element_id: str) -> UUID:
    """The task the turn's markup put behind `element_id` (`TurnTaskRecord`, WP2.2)."""
    event = await event_by_seq(db, learner_id=learner_id, seq=turn_seq)
    if event is None or event.type != "turn.committed":
        raise LookupError(f"no turn at seq {turn_seq}")
    turn = TurnCommitted.model_validate(event.payload)
    task = next((t for t in turn.markup.tasks if t.element_id == element_id), None)
    if task is None:
        raise LookupError(f"no task behind element {element_id!r} of turn {turn_seq}")
    return task.task_id
