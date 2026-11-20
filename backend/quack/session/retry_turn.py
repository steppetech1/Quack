"""`retry_turn`: the learner input a teacher turn was lost on (Т-§12.1, law 1).

The journal is the source of truth: a turn that never reached `turn.committed` leaves its input
(`message.sent`, `answer.submitted`, …) with no `turn.committed {action_seq = its seq}`. The retry
runs the turn again on the latest such input; it writes no event of its own. An answer that came
with an `element.responded` is not an input by itself (`causation_seq` is the response): the
response is.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from quack.db.db import Db
from quack.session.actions import ACTION_EVENT_TYPES, TURN_ACTION_KINDS

# Event types of the inputs that start a teacher turn, written by the learner.
INPUT_EVENT_TYPES: tuple[str, ...] = tuple(
    sorted(
        {
            ACTION_EVENT_TYPES[kind]
            for kind in TURN_ACTION_KINDS
            if kind in ACTION_EVENT_TYPES  # `retry_turn` has no event
        }
    )
)

_INPUTS = (
    "SELECT e.seq, EXISTS (SELECT 1 FROM core.events t "
    "WHERE t.learner_id = e.learner_id AND t.session_id = e.session_id "
    "AND t.type = 'turn.committed' AND t.payload ->> 'action_seq' = e.seq::text) AS answered "
    "FROM core.events e WHERE e.learner_id = %(learner_id)s AND e.session_id = %(session_id)s "
    "AND e.actor = 'learner' AND e.type = ANY(%(types)s) "
    "AND NOT (e.type = 'answer.submitted' AND e.causation_seq IS NOT NULL)"
)
_LATEST_UNANSWERED = f"SELECT seq FROM ({_INPUTS}) i WHERE NOT answered ORDER BY seq DESC LIMIT 1"
_LATEST = f"SELECT seq FROM ({_INPUTS}) i ORDER BY seq DESC LIMIT 1"


@dataclass(frozen=True)
class RetryTarget:
    """`seq` — the input to answer; `answered` — it has its `turn.committed` already, so a
    retry has nothing to run (a repeated request)."""

    seq: int
    answered: bool


async def unanswered_input_seq(db: Db, *, learner_id: UUID, session_id: UUID) -> int | None:
    """The `seq` of the latest learner input of the session without a `turn.committed`."""
    row = await db.fetch_one(
        _LATEST_UNANSWERED,
        {"learner_id": learner_id, "session_id": session_id, "types": list(INPUT_EVENT_TYPES)},
    )
    return None if row is None else int(row["seq"])


async def retry_target(db: Db, *, learner_id: UUID, session_id: UUID) -> RetryTarget | None:
    """What a `retry_turn` works on: the latest unanswered input; else the latest input, marked
    answered (nothing to run); `None` — the session has no input at all."""
    seq = await unanswered_input_seq(db, learner_id=learner_id, session_id=session_id)
    if seq is not None:
        return RetryTarget(seq, answered=False)
    row = await db.fetch_one(
        _LATEST,
        {"learner_id": learner_id, "session_id": session_id, "types": list(INPUT_EVENT_TYPES)},
    )
    return None if row is None else RetryTarget(int(row["seq"]), answered=True)
