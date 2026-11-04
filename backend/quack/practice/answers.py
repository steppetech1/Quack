"""A learner's answer and its instant verdict in one transaction (Т-§2.3, Т-§6.1; ТЗ Ф4 WP11.2,
WP11.4).

`record_answer` writes `answer.submitted` and, for an instant type (`choice`, `number`, short
exact text, `set`/`vector` of numbers), `verdict.recorded` with the key
`verdict:{answer_seq}:{part}` in the same transaction: the teacher sees the verdict at once
(Т-§6.1). Fast types are left to the inline wait of the turn and the insurance job
(WP11.3), slow types to `check.model` (WP11.6): the dispatcher schedules both from
`answer.submitted` (§6.3). Help on the task (highest hint step shown, solution asked) is
computed by code from the task's events in the session (WP11.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel
from redis.asyncio import Redis

from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import AnswerSubmitted
from quack.events.keys import verdict_key
from quack.events.log import NewEvent, append_events
from quack.practice.answer_spec import INSTANT_TYPES, AnswerSpec, LearnerAnswer
from quack.practice.check import CheckResult, HelpUsed, Verdict, check

# Phase 4 decision D-B-121: the part of a task without parts (Т-§6.2: a verdict per part).
DEFAULT_PART = "main"

_HELP_IN_SESSION = (
    "SELECT coalesce(max((payload->>'step')::int) FILTER (WHERE type = 'hint.shown'), 0) "
    "AS hint_step, coalesce(bool_or(type = 'solution.requested'), false) AS solution_requested "
    "FROM core.events WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s "
    "AND type IN ('hint.shown', 'solution.requested') AND payload->>'task_id' = %(task_id)s"
)


class _Help(BaseModel):
    hint_step: int
    solution_requested: bool


@dataclass(frozen=True)
class RecordedAnswer:
    """`answer_seq` — seq of `answer.submitted`; `verdict` — the instant verdict written with
    it, if any; `result` — what `check()` said (a fast or slow type is not a verdict yet)."""

    answer_seq: int
    verdict: Verdict | None
    result: CheckResult | None


async def help_in_session(db: Db, *, learner_id: UUID, session_id: UUID, task_id: UUID) -> HelpUsed:
    """WP11.2: help on the task in this session, from its events."""
    row = await db.fetch_one(
        _HELP_IN_SESSION,
        {"learner_id": learner_id, "session_id": session_id, "task_id": str(task_id)},
        row=_Help,
    )
    if row is None:
        return HelpUsed()
    return HelpUsed(hint_step=row.hint_step, solution_requested=row.solution_requested)


async def record_answer(
    db: Db,
    *,
    learner_id: UUID,
    session_id: UUID,
    domain_id: UUID | None,
    idempotency_key: str,
    payload: AnswerSubmitted,
    spec: AnswerSpec,
    settings: Settings,
    redis: Redis | None = None,
) -> RecordedAnswer:
    """Т-§6.1: the answer and its instant verdict in one transaction (also inside the caller's
    transaction). A repeat with the same key returns the stored answer and verdict."""
    part = payload.part or DEFAULT_PART
    async with db.transaction():
        help_used = await help_in_session(
            db, learner_id=learner_id, session_id=session_id, task_id=payload.task_id
        )
        [answer] = await append_events(
            db,
            learner_id,
            [
                NewEvent(
                    type="answer.submitted",
                    payload=payload,
                    actor="learner",
                    idempotency_key=idempotency_key,
                    domain_id=domain_id,
                    session_id=session_id,
                )
            ],
            redis=redis,
        )
        result: CheckResult | None = None
        if spec.type in INSTANT_TYPES:
            result = await check(
                LearnerAnswer.model_validate(payload.answer.model_dump()),
                spec,
                answer_seq=answer.seq,
                part=part,
                settings=settings,
                help_used=help_used,
                recognition_confidence=payload.recognition_confidence,
            )
        if not isinstance(result, Verdict):
            return RecordedAnswer(answer.seq, None, result)
        await append_events(
            db,
            learner_id,
            [
                NewEvent(
                    type="verdict.recorded",
                    payload=result.model_dump(mode="json"),
                    actor="code",
                    idempotency_key=verdict_key(answer.seq, part),
                    domain_id=domain_id,
                    session_id=session_id,
                    causation_seq=answer.seq,
                )
            ],
            redis=redis,
        )
    return RecordedAnswer(answer.seq, result, result)
